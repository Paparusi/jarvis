"""LLM Router — 3-tier intelligent routing: Cache → Local → Cloud.

Flow:
1. Check Semantic Cache → cache hit? Return cached response
2. Classify complexity → simple/medium/complex
3. Simple → Local model (Ollama/Qwen3) — no tools
4. Medium/Complex OR local fails → Agent Loop (cloud with tools)
5. Cache response for future use
6. Log everything for Brain Independence training data
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import litellm

from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.intelligence.cache import SemanticCache
from src.intelligence.prompt_assembler import PromptAssembler
from src.intelligence.tracker import CostTracker
from src.metacognition.confidence import ConfidenceCalibrator
from src.metacognition.feedback_loop import FeedbackLoop
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry
from src.utils.config import load_config
from src.utils.logging import get_logger

log = get_logger("intelligence.router")

litellm.suppress_debug_info = True


# Complexity classification keywords
_COMPLEX_INDICATORS = [
    "phân tích", "analyze", "viết code", "write code", "debug", "refactor",
    "so sánh", "compare", "giải thích chi tiết", "explain in detail",
    "lập kế hoạch", "plan", "thiết kế", "design", "review",
    "tối ưu", "optimize", "strategy", "chiến lược",
]

_SIMPLE_INDICATORS = [
    "xin chào", "hello", "hi", "chào", "cảm ơn", "thanks",
    "ok", "được", "tạm biệt", "bye", "có", "không",
    "tên", "name", "thời gian", "time", "ngày", "date",
]


def strip_thinking(text: str) -> str:
    """Strip Qwen3 <think>...</think> tags and SFT artifacts from response."""
    # Remove thinking tags
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
    # Remove ### Assistant:/### System: prefix artifacts from fine-tuned model
    text = re.sub(r"^###\s*(Assistant|System):\s*\n?", "", text).strip()
    return text


def check_response_quality(text: str) -> bool:
    """Check if a local model response is good enough to return.

    Returns True if response passes quality checks.
    """
    text = text.strip()

    # Too short
    if len(text) < 5:
        return False

    # Empty after stripping
    if not text:
        return False

    # Excessive repetition (same word/phrase repeated 5+ times)
    words = text.lower().split()
    if len(words) >= 10:
        from collections import Counter
        counts = Counter(words)
        most_common_count = counts.most_common(1)[0][1]
        # If most common word is > 50% of total words → likely gibberish
        if most_common_count > len(words) * 0.5 and len(words) > 5:
            return False

    # Response is just a short question (likely parroting user's question)
    if text.endswith("?") and len(text.split("\n")) == 1 and len(text) < 30:
        return False

    return True


def needs_tool_access(text: str) -> bool:
    """Check if a message likely needs tool access (web search, code, shell).

    Local models can't call tools, so these should go to cloud.
    CRITICAL: Must catch factual/real-time queries to prevent hallucination.
    """
    text_lower = text.lower()

    # --- Direct tool signals (explicit) ---
    _TOOL_SIGNALS = [
        # Web search signals
        "tìm kiếm", "search for", "tra cứu", "google", "look up",
        "tin tức", "news", "thời tiết", "weather",
        "mới nhất", "latest", "tỷ giá", "stock price", "exchange rate",
        "crypto", "bitcoin", "xauusd", "forex",
        "http://", "https://", "www.",
        # Code execution signals
        "chạy code", "run code", "chạy lệnh", "run command",
        "execute", "terminal", "shell",
        # File operation signals
        "đọc file", "read file", "ghi file", "write file",
        "tạo file", "create file",
    ]
    if any(signal in text_lower for signal in _TOOL_SIGNALS):
        return True

    # --- Factual/real-time query detection (prevent hallucination) ---
    # These questions need web search because local model will make up answers
    # IMPORTANT: Include both có dấu AND không dấu variants for Vietnamese
    _FACTUAL_SIGNALS = [
        # Prices, rates, market data (có dấu + không dấu)
        "giá", "gia ", "bao nhiêu tiền", "bao nhieu tien", "how much", "price",
        "thị trường", "thi truong", "market",
        # Current events, people, facts
        "tổng thống", "tong thong", "president",
        "thủ tướng", "thu tuong", "prime minister",
        "ai là", "ai la ", "who is", "ai đang", "ai dang", "who won",
        "bao giờ", "bao gio", "khi nào", "khi nao", "when is", "when did", "when will",
        "ở đâu", "o dau", "where is", "where can",
        # Real-time data
        "hôm nay", "hom nay", "today", "bây giờ", "bay gio", "right now", "currently",
        "mấy giờ", "may gio", "what time",
        "năm nay", "nam nay", "this year", "năm ngoái", "nam ngoai", "last year",
        "tuần này", "tuan nay", "this week", "tháng này", "thang nay", "this month",
        "hiện tại", "hien tai", "hiện nay", "hien nay",
        # Facts that need verification
        "có đúng không", "co dung khong", "is it true", "thật không", "that khong",
        "số liệu", "so lieu", "statistics", "data",
        "link", "url", "website", "trang web",
        # Comparison with real data
        "so với", "so voi", "compared to", "versus",
        "top ", "ranking", "xếp hạng", "xep hang",
        # Recipes, instructions from web
        "cách làm", "cach lam", "how to make", "recipe", "công thức", "cong thuc",
        # News, events
        "sự kiện", "su kien", "event",
        "dự báo", "du bao", "forecast",
    ]
    if any(signal in text_lower for signal in _FACTUAL_SIGNALS):
        return True

    # --- Question patterns that likely need real data ---
    import re
    # "X là gì/ai?" / "What is X?" patterns (có dấu + không dấu)
    if re.search(r"(là gì|la gi|là ai|la ai|what is|what are|who is)\s*\??$", text_lower):
        return True

    return False


def classify_complexity(text: str) -> str:
    """Classify message complexity: simple | medium | complex."""
    text_lower = text.lower()
    word_count = len(text.split())

    # Short messages are usually simple
    if word_count <= 5:
        for indicator in _SIMPLE_INDICATORS:
            if indicator in text_lower:
                return "simple"

    # Long messages or those with complex keywords → complex
    if word_count > 50:
        return "complex"

    for indicator in _COMPLEX_INDICATORS:
        if indicator in text_lower:
            return "complex"

    # Check for code blocks
    if "```" in text or "def " in text or "function " in text:
        return "complex"

    return "medium"


@dataclass
class StreamChunk:
    """A single chunk in a streaming response."""
    text: str = ""
    tool_name: str = ""         # non-empty when a tool starts/ends
    is_tool_start: bool = False
    is_tool_end: bool = False
    is_done: bool = False


class ResponseStream:
    """Async iterable that streams LLM response chunks.

    After iteration completes, `.response` contains the full AgentResponse.

    Usage:
        stream = router.route_stream(session, text, ...)
        async for chunk in stream:
            print(chunk.text, end="")
        final_response = stream.response
    """

    def __init__(self) -> None:
        self.response: AgentResponse | None = None
        self._chunks: list[StreamChunk] = []
        self._generator: AsyncIterator[StreamChunk] | None = None

    def _set_generator(self, gen: AsyncIterator[StreamChunk]) -> None:
        self._generator = gen

    def __aiter__(self) -> AsyncIterator[StreamChunk]:
        if self._generator is None:
            raise RuntimeError("Stream not initialized")
        return self._generator


class LLMRouter:
    """3-tier LLM router: Cache → Local → Cloud (with tools)."""

    def __init__(
        self,
        skill_summary: str = "",
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        config = load_config()
        intel_config = config.get("intelligence", {})
        router_config = intel_config.get("router", {})

        self._cloud_model = intel_config.get("default_model", "claude-sonnet-4-20250514")
        self._max_tokens = intel_config.get("max_tokens", 4096)
        self._temperature = intel_config.get("temperature", 0.7)

        # Local model config — dual model: fast (simple) + strong (medium)
        self._local_model = router_config.get("local_model", "ollama/qwen3.5:4b")
        self._local_model_strong = router_config.get("local_model_strong", "")
        self._local_enabled = router_config.get("enabled", False)

        # Tool registry
        self._tool_registry = tool_registry or ToolRegistry()

        # Prompt Assembler (manages token budget + context injection)
        self._assembler = PromptAssembler(
            max_context_tokens=30000,
            skill_summary=skill_summary,
            tool_registry=self._tool_registry,
        )

        # Confidence Calibrator (meta-cognition)
        self._confidence = ConfidenceCalibrator(
            accept_threshold=0.65,
            escalate_threshold=0.35,
        )

        # Cache
        cache_config = router_config.get("cache", {})
        self._cache = SemanticCache(
            similarity_threshold=cache_config.get("similarity_threshold", 0.92),
            ttl_seconds=cache_config.get("ttl_seconds", 86400),
        )

        # Tracker + Tracer + Feedback Loop
        self._tracker = CostTracker()
        self._tracer = ReasoningTracer()
        self._feedback_loop = FeedbackLoop()

        # Agent Loop (for cloud calls with tool support)
        self._agent_loop = AgentLoop(
            tool_registry=self._tool_registry,
            assembler=self._assembler,
            tracer=self._tracer,
            cloud_model=self._cloud_model,
            local_model=self._local_model,
            max_iterations=8,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        log.info(
            "router_initialized",
            cloud_model=self._cloud_model,
            local_model=self._local_model,
            local_model_strong=self._local_model_strong or "none",
            local_enabled=self._local_enabled,
            tools=len(self._tool_registry.get_all()),
        )

    @property
    def tracker(self) -> CostTracker:
        return self._tracker

    @property
    def cache(self) -> SemanticCache:
        return self._cache

    @property
    def feedback_loop(self) -> FeedbackLoop:
        return self._feedback_loop

    def route_stream(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str = "",
        skill_context: str = "",
    ) -> ResponseStream:
        """Create a streaming response for non-tool queries.

        Returns a ResponseStream that yields StreamChunk objects.
        After iteration, stream.response has the full AgentResponse.
        """
        stream = ResponseStream()
        stream._set_generator(
            self._stream_impl(stream, session, user_message, memory_context, skill_context)
        )
        return stream

    async def _stream_impl(
        self,
        stream: ResponseStream,
        session: SessionState,
        user_message: str,
        memory_context: str,
        skill_context: str,
    ) -> AsyncIterator[StreamChunk]:
        """Internal streaming implementation."""
        start_time = time.monotonic()
        trace = self._tracer.start_trace(session.session_id, user_message)

        # 1. Cache check
        cached = await self._cache.get(user_message)
        if cached:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._tracker.log_usage(
                model=cached["model_used"], tokens_in=0, tokens_out=0,
                latency_ms=elapsed_ms, source="cache", cached=True,
            )
            trace.add_step("cache", "check", "hit")
            trace.final_model = f"{cached['model_used']}(cached)"
            self._tracer.save_trace(trace)
            stream.response = AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=cached["response_text"],
                model_used=f"{cached['model_used']}(cached)",
                latency_ms=elapsed_ms,
            )
            yield StreamChunk(text=cached["response_text"], is_done=True)
            return

        trace.add_step("cache", "check", "miss")

        # 2. Build messages
        messages = await self._build_messages(
            session, user_message, memory_context, skill_context
        )
        model = self._cloud_model

        # 3. Stream from LLM
        full_content = ""
        total_tokens_in = 0
        total_tokens_out = 0

        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "stream": True,
            }

            if "ollama" in model:
                kwargs["api_base"] = "http://localhost:11434"
                kwargs["timeout"] = 60

            response = await litellm.acompletion(**kwargs)

            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue
                delta = choice.delta
                if delta and delta.content:
                    text = delta.content
                    full_content += text
                    yield StreamChunk(text=text)

                # Collect usage from stream end
                if hasattr(chunk, "usage") and chunk.usage:
                    total_tokens_in = chunk.usage.prompt_tokens or 0
                    total_tokens_out = chunk.usage.completion_tokens or 0

            # Strip thinking tags if present (Qwen3)
            if "<think>" in full_content:
                full_content = strip_thinking(full_content)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            # Track cost
            cost = self._tracker.estimate_cost(model, total_tokens_in, total_tokens_out)
            self._tracker.log_usage(
                model=model, tokens_in=total_tokens_in, tokens_out=total_tokens_out,
                latency_ms=elapsed_ms, source="cloud",
            )
            self._tracker.record_success(model)

            # Cache response
            if full_content:
                await self._cache.put(
                    user_message, full_content, model,
                    total_tokens_in, total_tokens_out,
                )

            trace.final_model = model
            self._tracer.save_trace(trace)

            stream.response = AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=full_content,
                model_used=model,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=elapsed_ms,
                cost_usd=cost,
            )
            yield StreamChunk(is_done=True)

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._tracker.record_failure(model)
            log.error("stream_error", model=model, error=str(e))
            trace.add_step("error", "stream", str(e)[:200])
            self._tracer.save_trace(trace)

            error_msg = f"Xin lỗi, tôi gặp lỗi: {e}"
            stream.response = AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=error_msg,
                model_used=model,
                latency_ms=elapsed_ms,
            )
            yield StreamChunk(text=error_msg, is_done=True)

    async def route(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str = "",
        skill_context: str = "",
        use_tools: bool = True,
    ) -> AgentResponse:
        """Route a message through the 3-tier pipeline."""
        start_time = time.monotonic()

        # Start reasoning trace
        trace = self._tracer.start_trace(session.session_id, user_message)

        # 1. Check semantic cache — works for factual queries regardless of context
        cached = await self._cache.get(user_message)
        if cached:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._tracker.log_usage(
                model=cached["model_used"],
                tokens_in=0, tokens_out=0,
                latency_ms=elapsed_ms,
                source="cache", cached=True,
            )
            trace.add_step("cache", "check", "hit", model=cached["model_used"])
            trace.final_model = f"{cached['model_used']}(cached)"
            self._tracer.save_trace(trace)
            log.info("routed_cache_hit", latency_ms=elapsed_ms)
            return AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=cached["response_text"],
                model_used=f"{cached['model_used']}(cached)",
                latency_ms=elapsed_ms,
            )

        trace.add_step("cache", "check", "miss")

        # 2. Classify complexity
        complexity = classify_complexity(user_message)
        trace.add_step("classify", "complexity", complexity)

        # 3. Check feedback loop — has local model historically failed on similar queries?
        feedback_escalate = False
        if self._local_enabled and complexity in ("simple", "medium"):
            try:
                feedback_escalate, penalty = await self._feedback_loop.check_escalation(
                    user_message
                )
                if feedback_escalate:
                    trace.add_step(
                        "feedback_loop", "escalate",
                        f"penalty={penalty:.2f}",
                        reason="similar_queries_got_negative_feedback",
                    )
            except Exception as e:
                log.debug("feedback_loop_check_failed", error=str(e))

        # 4. Triage: local models for text-only tasks
        #    Tool-requiring requests → always cloud (small models can't
        #    reliably call tools — OpenClaw pattern)
        msg_needs_tools = use_tools and needs_tool_access(user_message)

        if msg_needs_tools:
            trace.add_step("triage", "skip_local", "needs_tools")

        if (
            self._local_enabled
            and complexity in ("simple", "medium")
            and not feedback_escalate
            and not msg_needs_tools
        ):
            # Use fast model (4B) for all interactive queries (think=false: 0.3-1.3s)
            # Strong model (30B) reserved as quality fallback if 4B confidence is low
            local_model = self._local_model

            if self._tracker.is_model_available(local_model):
                trace.add_step("route", "try_local", local_model, complexity=complexity)
                result = await self._try_local(
                    session, user_message, memory_context, complexity,
                    skill_context, trace, model_override=local_model,
                )
                if result:
                    await self._cache.put(
                        user_message, result.content, result.model_used,
                        result.tokens_in, result.tokens_out,
                    )
                    trace.final_model = result.model_used
                    trace.confidence_score = result.confidence_score
                    self._tracer.save_trace(trace)
                    return result

                trace.was_escalated = True
                trace.add_step("escalate", "local_failed", "falling_back_to_cloud")

        # 5. Cloud fallback (always works)
        trace.add_step("route", "cloud", self._cloud_model, use_tools=use_tools)
        result = await self._call_cloud(session, user_message, memory_context, skill_context, use_tools=use_tools)

        # Cache cloud response too
        if result.content:
            await self._cache.put(
                user_message, result.content, result.model_used,
                result.tokens_in, result.tokens_out,
            )

        trace.final_model = result.model_used
        self._tracer.save_trace(trace)
        return result

    async def _try_local(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str,
        complexity: str,
        skill_context: str = "",
        trace: "ReasoningTrace | None" = None,
        model_override: str = "",
    ) -> AgentResponse | None:
        """Try local model for TEXT-ONLY responses.

        Local models (Qwen3:4b) are used ONLY for conversational tasks
        that don't need tools. Tool-requiring requests are routed to cloud
        by the triage step in route() — small models can't reliably call
        tools (validated by OpenClaw community).

        Returns None if fails → triggers cloud fallback.
        """
        from src.metacognition.tracer import ReasoningTrace
        start_time = time.monotonic()
        model = model_override or self._local_model
        messages = await self._build_messages(session, user_message, memory_context, skill_context)

        # Adjust max_tokens and timeout based on complexity and model
        is_strong = model == self._local_model_strong
        local_max_tokens = 512 if complexity == "simple" else (2048 if is_strong else 1024)
        timeout = 60 if is_strong else 30

        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                max_tokens=local_max_tokens,
                temperature=self._temperature,
                api_base="http://localhost:11434",
                timeout=timeout,
                extra_body={"think": False},  # Disable Qwen3 thinking mode — 15x faster
            )

            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            raw_content = response.choices[0].message.content or ""
            content = strip_thinking(raw_content)
            usage = response.usage

            tokens_in = usage.prompt_tokens if usage else 0
            tokens_out = usage.completion_tokens if usage else 0

            self._tracker.log_usage(
                model=model, tokens_in=tokens_in,
                tokens_out=tokens_out, latency_ms=elapsed_ms, source="local",
            )
            self._tracker.record_success(model)

            # Meta-cognitive confidence check
            confidence = self._confidence.assess(user_message, content, complexity)
            if trace:
                trace.add_step(
                    "confidence", "assess", confidence.decision,
                    score=f"{confidence.overall_score:.2f}",
                    signals={s.name: f"{s.score:.2f}" for s in confidence.signals},
                )
            if confidence.should_escalate:
                log.info(
                    "confidence_escalate",
                    model=model,
                    score=f"{confidence.overall_score:.2f}",
                    decision=confidence.decision,
                    reason=confidence.reasoning,
                    content_preview=content[:80],
                )
                return None

            log.info(
                "routed_local",
                model=model,
                complexity=complexity,
                confidence=f"{confidence.overall_score:.2f}",
                tokens_out=tokens_out,
                latency_ms=elapsed_ms,
            )

            return AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=content,
                model_used=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=elapsed_ms,
                cost_usd=0.0,
                confidence_score=confidence.overall_score,
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._tracker.record_failure(model)
            log.warning(
                "local_model_failed",
                model=model,
                error=str(e),
                latency_ms=elapsed_ms,
            )
            return None

    async def _call_cloud(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str,
        skill_context: str = "",
        use_tools: bool = True,
    ) -> AgentResponse:
        """Call cloud LLM via Agent Loop (with tool support + retry)."""
        start_time = time.monotonic()
        has_tools = use_tools and bool(self._tool_registry.get_all())
        max_retries = 2

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    # Exponential backoff: 2s, 4s
                    delay = 2 ** attempt
                    log.info("cloud_retry", attempt=attempt, delay_s=delay)
                    await asyncio.sleep(delay)

                result = await self._agent_loop.run(
                    session=session,
                    user_message=user_message,
                    memory_context=memory_context,
                    skill_context=skill_context,
                    use_tools=has_tools,
                    model=self._cloud_model,
                )

                # Track cost
                cost = self._tracker.estimate_cost(
                    self._cloud_model, result.tokens_in, result.tokens_out
                )
                self._tracker.log_usage(
                    model=self._cloud_model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                    source="cloud",
                )
                self._tracker.record_success(self._cloud_model)
                result.cost_usd = cost

                log.info(
                    "routed_cloud",
                    model=self._cloud_model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                    cost_usd=f"{cost:.4f}",
                    has_memory=bool(memory_context),
                    has_tools=has_tools,
                    retries=attempt,
                    tool_trace=result.reasoning_trace[:100] if result.reasoning_trace else None,
                )

                return result

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Don't retry on auth errors or rate limits (429 needs longer backoff)
                if "401" in error_str or "403" in error_str:
                    break
                if "429" in error_str and attempt < max_retries:
                    # Rate limited — longer backoff
                    delay = 5 * (attempt + 1)
                    log.warning("cloud_rate_limited", delay_s=delay)
                    await asyncio.sleep(delay)
                    continue

                log.warning(
                    "cloud_attempt_failed",
                    attempt=attempt,
                    error=str(e),
                    model=self._cloud_model,
                )

        # All retries exhausted
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        self._tracker.record_failure(self._cloud_model)
        log.error("cloud_model_failed", model=self._cloud_model,
                  error=str(last_error), latency_ms=elapsed_ms, retries=max_retries)
        return AgentResponse(
            request_id=session.session_id,
            session_id=session.session_id,
            content=f"Xin lỗi, tôi gặp lỗi khi xử lý. Vui lòng thử lại sau.",
            model_used=self._cloud_model,
            latency_ms=elapsed_ms,
        )

    async def route_stream_tools(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str = "",
        skill_context: str = "",
    ) -> AsyncIterator:
        """Route with streaming — yields StreamEvent objects for progressive UI.

        Flow: Cache → Local (if eligible) → Cloud stream with tools.
        """
        from src.intelligence.agent_loop import StreamEvent

        # 1. Cache check
        cached = await self._cache.get(user_message)
        if cached:
            self._tracker.log_usage(
                model=cached["model_used"], tokens_in=0, tokens_out=0,
                latency_ms=0, source="cache", cached=True,
            )
            resp = AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=cached["response_text"],
                model_used=f"{cached['model_used']}(cached)",
                latency_ms=0,
            )
            yield StreamEvent(type="text", text=cached["response_text"])
            yield StreamEvent(type="done", response=resp)
            return

        # 2. Try local model for non-tool queries (same logic as route())
        complexity = classify_complexity(user_message)
        msg_needs_tools = needs_tool_access(user_message)

        if (
            self._local_enabled
            and complexity in ("simple", "medium")
            and not msg_needs_tools
        ):
            # Check feedback escalation
            feedback_escalate = False
            try:
                feedback_escalate, _ = await self._feedback_loop.check_escalation(
                    user_message
                )
            except Exception:
                pass

            if not feedback_escalate:
                # Use fast model for all interactive queries (think=false: 0.3-1.3s)
                local_model = self._local_model

                if self._tracker.is_model_available(local_model):
                    result = await self._try_local(
                        session, user_message, memory_context, complexity,
                        skill_context, model_override=local_model,
                    )
                    if result:
                        await self._cache.put(
                            user_message, result.content, result.model_used,
                            result.tokens_in, result.tokens_out,
                        )
                        yield StreamEvent(type="text", text=result.content)
                        yield StreamEvent(type="done", response=result)
                        return

        # 3. Cloud fallback — stream from agent loop
        has_tools = bool(self._tool_registry.get_all())
        final_response = None

        async for event in self._agent_loop.run_stream(
            session=session,
            user_message=user_message,
            memory_context=memory_context,
            skill_context=skill_context,
            use_tools=has_tools,
            model=self._cloud_model,
        ):
            if event.type == "done" and event.response:
                final_response = event.response
                cost = self._tracker.estimate_cost(
                    self._cloud_model,
                    final_response.tokens_in,
                    final_response.tokens_out,
                )
                self._tracker.log_usage(
                    model=self._cloud_model,
                    tokens_in=final_response.tokens_in,
                    tokens_out=final_response.tokens_out,
                    latency_ms=final_response.latency_ms,
                    source="cloud",
                )
                self._tracker.record_success(self._cloud_model)
                final_response.cost_usd = cost

                # Cache
                if final_response.content:
                    await self._cache.put(
                        user_message, final_response.content,
                        final_response.model_used,
                        final_response.tokens_in,
                        final_response.tokens_out,
                    )

            yield event

    def update_skill_summary(self, summary: str) -> None:
        """Update skill metadata summary (when skills change)."""
        self._assembler.update_skill_summary(summary)

    async def _build_messages(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str,
        skill_context: str = "",
    ) -> list[dict[str, Any]]:
        """Build message list using PromptAssembler for intelligent context management."""
        return await self._assembler.assemble(
            user_message=user_message,
            history=session.get_history(),
            memory_context=memory_context,
            skill_context=skill_context,
        )

    def get_stats(self) -> dict:
        """Get combined router statistics."""
        return {
            "cost": self._tracker.get_stats(),
            "cache": self._cache.get_stats(),
            "traces": self._tracer.get_escalation_rate(),
            "tools": self._tool_registry.get_stats(),
            "feedback_loop": self._feedback_loop.get_stats(),
            "local_enabled": self._local_enabled,
            "local_model": self._local_model,
            "local_model_strong": self._local_model_strong or "(not configured)",
            "cloud_model": self._cloud_model,
        }
