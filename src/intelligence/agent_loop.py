"""Agent Loop — Agentic execution with tool calling.

Core loop (OpenClaw Pi SDK pattern):
1. Send message + tools to LLM
2. LLM decides: respond directly OR call tool(s)
3. If tool call → execute (parallel if multiple) → feed results back
4. Check finish_reason → handle truncation
5. Repeat until LLM gives final text response or max_iterations hit

Per-iteration error handling: a failure at iteration N does NOT discard
the work from iterations 0..N-1.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator

from src.intelligence.claude_client import get_claude_client

from src.gateway.models import AgentResponse, SessionState
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry, ToolResult
from src.utils.logging import get_logger

log = get_logger("intelligence.agent_loop")

# Per-iteration retry limit for transient LLM errors
_LLM_RETRIES = 1
_LLM_RETRY_DELAY = 2.0

# Tool name → emoji for status messages
_TOOL_EMOJI = {
    "web_search": "🔍",
    "fetch_url": "🌐",
    "run_command": "⚡",
    "run_python": "🐍",
    "read_file": "📄",
    "write_file": "📝",
    "list_directory": "📁",
    "http_request": "🔗",
    "port_scan": "🔓",
    "dns_lookup": "📡",
    "ping": "📶",
    "traceroute": "🗺️",
    "git_status": "📊",
    "git_diff": "📝",
    "git_log": "📜",
    "git_commit": "💾",
    "git_branch": "🌿",
    "docker_ps": "🐳",
    "docker_logs": "📋",
    "docker_exec": "🐳",
    "docker_images": "🖼️",
    "docker_compose": "🐳",
    "screenshot": "📸",
    "extract_page": "🌐",
    # Crypto & Utility
    "base64": "🔐",
    "hash": "🔑",
    "url_encode": "🔗",
    "jwt_decode": "🎫",
    "hex_convert": "🔢",
    "regex_test": "🔤",
    "timestamp": "⏰",
    "ip_info": "🌍",
    "whois": "📋",
    "ssl_check": "🔒",
    "generate_password": "🔑",
    "cidr_calc": "🧮",
    # Security Recon
    "subdomain_enum": "🕵️",
    "http_headers": "🛡️",
    "cve_lookup": "⚠️",
    "reverse_dns": "🔄",
    "tech_detect": "🔬",
    # Code Analysis
    "ast_analyze": "🔍",
    "complexity_check": "📊",
    "dependency_graph": "🕸️",
    "code_search": "🔎",
    "diff_summary": "📝",
    # Data Tools
    "csv_analyze": "📊",
    "json_query": "🗂️",
    "sqlite_query": "🗄️",
    "text_stats": "📏",
    "json_transform": "🔄",
}


class StreamEvent:
    """Event yielded during streaming agent loop execution.

    Types:
    - "text": streaming text chunk from LLM
    - "tool_start": tool execution starting (tool_name set)
    - "tool_end": tool execution finished (tool_name + tool_success set)
    - "done": final response ready (response set)
    - "error": error occurred (text has error message)
    """
    __slots__ = ("type", "text", "tool_name", "tool_success", "response")

    def __init__(
        self,
        type: str,
        text: str = "",
        tool_name: str = "",
        tool_success: bool = True,
        response: "AgentResponse | None" = None,
    ):
        self.type = type
        self.text = text
        self.tool_name = tool_name
        self.tool_success = tool_success
        self.response = response


class AgentLoop:
    """Execute agentic tasks with tool calling support.

    v2 enhancements:
    - Planning hints for complex multi-tool queries
    - Quality gate on final response (self-verification)
    - Better fallback synthesis with structured summaries
    """

    # Max chars of tool output to feed back (prevents context overflow)
    _MAX_TOOL_OUTPUT = 4000

    # Queries with these signals benefit from planning hints
    _PLANNING_SIGNALS = [
        "phân tích", "analyze", "so sánh", "compare", "nghiên cứu", "research",
        "tìm hiểu", "investigate", "lập kế hoạch", "plan", "thiết kế", "design",
        "tổng hợp", "summarize", "đánh giá", "evaluate", "kiểm tra", "check",
        "audit", "scan", "review", "refactor",
    ]

    def __init__(
        self,
        tool_registry: ToolRegistry,
        assembler: PromptAssembler,
        tracer: ReasoningTracer,
        cloud_model: str = "claude-sonnet-4-20250514",
        max_iterations: int = 8,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> None:
        self._tools = tool_registry
        self._assembler = assembler
        self._tracer = tracer
        self._cloud_model = cloud_model
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = get_claude_client()
        self._tool_result_cache: dict[str, tuple[float, ToolResult]] = {}
        self._TOOL_CACHE_TTL = 30  # seconds

    def _needs_planning(self, text: str) -> bool:
        """Check if query would benefit from a planning step."""
        text_lower = text.lower()
        signal_count = sum(1 for s in self._PLANNING_SIGNALS if s in text_lower)
        # Multiple tools likely needed, or explicit planning signals
        return signal_count >= 1 or len(text.split()) > 30

    def _inject_planning_hint(self, messages: list[dict], user_message: str) -> list[dict]:
        """Add a planning instruction for complex queries."""
        if not self._needs_planning(user_message):
            return messages

        planning_hint = (
            "\n\n[PLANNING] Đây là task phức tạp. Trước khi thực hiện, hãy:"
            "\n1. Xác định chính xác user cần gì"
            "\n2. Liệt kê các bước cần thực hiện (ngắn gọn)"
            "\n3. Xác định tools nào cần dùng cho mỗi bước"
            "\n4. Thực hiện từng bước, verify kết quả trước khi tiếp tục"
            "\n5. Tổng hợp kết quả cuối cùng có cấu trúc rõ ràng"
        )

        # Append planning hint to system message
        if messages and messages[0]["role"] == "system":
            messages[0] = {
                **messages[0],
                "content": messages[0]["content"] + planning_hint,
            }

        return messages

    _REALTIME_PATTERN = re.compile(
        r"(?i)"
        r"(?:xauusd|gold|vàng|giá vàng|mt5|trading|trade|lệnh|position|pending)"
        r"|(?:phân tích.*(?:thị trường|market|chart|kỹ thuật|technical))"
        r"|(?:giá|price|bid|ask|spread|pip)"
        r"|(?:check.*giá|giá.*hiện|current.*price)"
    )

    def _inject_realtime_hint(self, messages: list[dict], user_message: str) -> list[dict]:
        """Force tool use for real-time/trading queries.

        Injects a strong instruction into the last user message so Claude
        cannot rely on stale prices from conversation history.
        """
        if not self._REALTIME_PATTERN.search(user_message):
            return messages

        hint = (
            "\n\n[SYSTEM] Dữ liệu giá/thị trường trong lịch sử chat ĐÃ CŨ. "
            "BẮT BUỘC gọi tool (mt5_price, mt5_candles, web_search) để lấy data mới nhất. "
            "KHÔNG ĐƯỢC dùng số liệu từ tin nhắn trước."
        )

        # Append to last user message
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                messages[i] = {
                    **messages[i],
                    "content": messages[i]["content"] + hint,
                }
                break

        return messages

    async def run(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str = "",
        skill_context: str = "",
        use_tools: bool = True,
        model: str | None = None,
    ) -> AgentResponse:
        """Run the agent loop."""
        start_time = time.monotonic()
        model = model or self._cloud_model
        trace = self._tracer.start_trace(session.session_id, user_message)

        # Build initial messages
        messages = await self._assembler.assemble(
            user_message=user_message,
            history=session.get_history(),
            memory_context=memory_context,
            skill_context=skill_context,
        )

        # v2: Inject planning hints for complex queries
        messages = self._inject_planning_hint(messages, user_message)

        # v3: Force tool use for real-time/trading queries
        messages = self._inject_realtime_hint(messages, user_message)

        # Get tool schemas if tools enabled
        tools = self._tools.get_schemas() if use_tools and self._tools.get_all() else None

        total_tokens_in = 0
        total_tokens_out = 0
        tool_calls_made: list[dict[str, Any]] = []

        for iteration in range(self._max_iterations):
            trace.add_step(
                "agent_loop",
                f"iteration_{iteration}",
                f"model={model}",
                tools_available=len(tools) if tools else 0,
            )

            # --- Call LLM with per-iteration retry ---
            response = await self._call_llm_with_retry(
                model, messages, tools, trace, iteration,
            )

            if response is None:
                # All retries failed — return what we have so far
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                trace.add_step("error", "llm_failed", f"iteration={iteration}")
                self._tracer.save_trace(trace)

                if tool_calls_made:
                    content = self._build_fallback_response(tool_calls_made)
                else:
                    content = "Xin lỗi, tôi gặp sự cố khi xử lý. Vui lòng thử lại."

                return AgentResponse(
                    request_id=session.session_id,
                    session_id=session.session_id,
                    content=content,
                    model_used=model,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    latency_ms=elapsed_ms,
                    reasoning_trace=json.dumps(tool_calls_made) if tool_calls_made else None,
                )

            # Track tokens
            usage = response.usage
            if usage:
                total_tokens_in += usage.prompt_tokens or 0
                total_tokens_out += usage.completion_tokens or 0

            choice = response.choices[0]
            message = choice.message
            finish_reason = choice.finish_reason or ""

            # --- Check finish_reason ---
            if finish_reason == "length":
                # Response was truncated due to max_tokens
                log.warning("response_truncated", iteration=iteration, model=model)
                trace.add_step("warning", "truncated", "max_tokens_reached")
                # Still use whatever content we got, but note it's partial
                content = (message.content or "") + "\n\n⚠️ _(phản hồi bị cắt do quá dài)_"
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                self._tracer.save_trace(trace)
                return AgentResponse(
                    request_id=session.session_id,
                    session_id=session.session_id,
                    content=content,
                    model_used=model,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    latency_ms=elapsed_ms,
                    reasoning_trace=json.dumps(tool_calls_made) if tool_calls_made else None,
                )

            # --- Process tool calls ---
            if message.tool_calls:
                # Execute tools in parallel
                tool_results_messages = await self._execute_tools_parallel(
                    message.tool_calls, tool_calls_made, trace, iteration,
                )

                # Append assistant message (clean serialization, not model_dump)
                assistant_msg = self._serialize_assistant_message(message)
                messages.append(assistant_msg)
                messages.extend(tool_results_messages)

                # Continue loop — LLM will process tool results
                continue

            # --- No tool calls — LLM is done ---
            content = message.content or ""

            # Strip thinking tags if present (Qwen3)
            if "<think>" in content:
                from src.intelligence.router import strip_thinking
                content = strip_thinking(content)

            # v2: Quality gate — self-verification
            content = self._quality_check(content, tool_calls_made)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            trace.add_step(
                "agent_loop", "completed",
                f"iterations={iteration + 1}",
                tool_calls=len(tool_calls_made),
            )
            trace.final_model = model
            self._tracer.save_trace(trace)

            log.info(
                "agent_loop_completed",
                model=model,
                iterations=iteration + 1,
                tool_calls=len(tool_calls_made),
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=elapsed_ms,
            )

            return AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=content,
                model_used=model,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=elapsed_ms,
                reasoning_trace=json.dumps(tool_calls_made) if tool_calls_made else None,
            )

        # Max iterations reached — return summary of work done
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log.warning(
            "agent_loop_max_iterations",
            max=self._max_iterations,
            tool_calls=len(tool_calls_made),
        )
        trace.add_step("agent_loop", "max_iterations", f"limit={self._max_iterations}")
        self._tracer.save_trace(trace)

        content = self._build_fallback_response(tool_calls_made)

        return AgentResponse(
            request_id=session.session_id,
            session_id=session.session_id,
            content=content,
            model_used=model,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            latency_ms=elapsed_ms,
            reasoning_trace=json.dumps(tool_calls_made) if tool_calls_made else None,
        )

    async def run_stream(
        self,
        session: SessionState,
        user_message: str,
        memory_context: str = "",
        skill_context: str = "",
        use_tools: bool = True,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run agent loop with streaming — yields StreamEvents for progressive UI.

        Yields:
        - StreamEvent(type="tool_start") when a tool begins executing
        - StreamEvent(type="tool_end") when a tool finishes
        - StreamEvent(type="text") for each text chunk from the final LLM response
        - StreamEvent(type="done", response=AgentResponse) when complete
        """
        start_time = time.monotonic()
        model = model or self._cloud_model
        trace = self._tracer.start_trace(session.session_id, user_message)

        messages = await self._assembler.assemble(
            user_message=user_message,
            history=session.get_history(),
            memory_context=memory_context,
            skill_context=skill_context,
        )

        # v2: Planning hints for complex queries
        messages = self._inject_planning_hint(messages, user_message)

        # v3: Force tool use for real-time/trading queries
        messages = self._inject_realtime_hint(messages, user_message)

        tools_schema = self._tools.get_schemas() if use_tools and self._tools.get_all() else None

        total_tokens_in = 0
        total_tokens_out = 0
        tool_calls_made: list[dict[str, Any]] = []

        for iteration in range(self._max_iterations):
            trace.add_step("agent_loop", f"iteration_{iteration}", f"model={model}")

            # Non-streaming call for tool-calling iterations
            # (we need the full response to check for tool_calls)
            response = await self._call_llm_with_retry(
                model, messages, tools_schema, trace, iteration,
            )

            if response is None:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                content = (
                    self._build_fallback_response(tool_calls_made)
                    if tool_calls_made
                    else "Xin lỗi, tôi gặp sự cố khi xử lý. Vui lòng thử lại."
                )
                resp = AgentResponse(
                    request_id=session.session_id,
                    session_id=session.session_id,
                    content=content,
                    model_used=model,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    latency_ms=elapsed_ms,
                )
                yield StreamEvent(type="error", text=content, response=resp)
                return

            usage = response.usage
            if usage:
                total_tokens_in += usage.prompt_tokens or 0
                total_tokens_out += usage.completion_tokens or 0

            choice = response.choices[0]
            message = choice.message
            finish_reason = choice.finish_reason or ""

            # Handle tool calls
            if message.tool_calls:
                # Yield tool_start events
                for tc in message.tool_calls:
                    emoji = _TOOL_EMOJI.get(tc.function.name, "🔧")
                    yield StreamEvent(
                        type="tool_start",
                        tool_name=tc.function.name,
                        text=f"{emoji} {tc.function.name}",
                    )

                # Execute tools
                tool_results_messages = await self._execute_tools_parallel(
                    message.tool_calls, tool_calls_made, trace, iteration,
                )

                # Yield tool_end events
                for i, tc in enumerate(message.tool_calls):
                    success = tool_calls_made[-(len(message.tool_calls) - i)]["success"]
                    yield StreamEvent(
                        type="tool_end",
                        tool_name=tc.function.name,
                        tool_success=success,
                    )

                assistant_msg = self._serialize_assistant_message(message)
                messages.append(assistant_msg)
                messages.extend(tool_results_messages)
                continue

            # No tool calls — stream the final text response
            content = message.content or ""

            if "<think>" in content:
                from src.intelligence.router import strip_thinking
                content = strip_thinking(content)

            if finish_reason == "length":
                content += "\n\n⚠️ _(phản hồi bị cắt do quá dài)_"

            # v2: Quality gate
            content = self._quality_check(content, tool_calls_made)

            # Yield text in chunks for progressive display
            chunk_size = 80
            for i in range(0, len(content), chunk_size):
                yield StreamEvent(type="text", text=content[i:i + chunk_size])

            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            trace.final_model = model
            self._tracer.save_trace(trace)

            resp = AgentResponse(
                request_id=session.session_id,
                session_id=session.session_id,
                content=content,
                model_used=model,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=elapsed_ms,
                reasoning_trace=json.dumps(tool_calls_made) if tool_calls_made else None,
            )
            yield StreamEvent(type="done", response=resp)
            return

        # Max iterations
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        content = self._build_fallback_response(tool_calls_made)
        resp = AgentResponse(
            request_id=session.session_id,
            session_id=session.session_id,
            content=content,
            model_used=model,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            latency_ms=elapsed_ms,
            reasoning_trace=json.dumps(tool_calls_made) if tool_calls_made else None,
        )
        yield StreamEvent(type="done", response=resp)

    async def _call_llm_with_retry(
        self, model: str, messages: list, tools: list | None,
        trace: Any, iteration: int,
    ):
        """Call LLM with per-iteration retry. Returns response or None."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        for attempt in range(_LLM_RETRIES + 1):
            try:
                return await self._client.complete(**kwargs)
            except Exception as e:
                error_str = str(e).lower()
                # Don't retry auth errors
                if "401" in error_str or "403" in error_str:
                    log.error("llm_auth_error", model=model, error=str(e))
                    trace.add_step("error", "auth", str(e)[:200])
                    return None

                if attempt < _LLM_RETRIES:
                    log.warning(
                        "llm_retry", model=model, attempt=attempt,
                        error=str(e)[:100], iteration=iteration,
                    )
                    await asyncio.sleep(_LLM_RETRY_DELAY)
                else:
                    log.error(
                        "llm_failed_all_retries", model=model,
                        error=str(e), iteration=iteration,
                    )
                    trace.add_step("error", str(type(e).__name__), str(e)[:200])
                    return None
        return None

    # Trading tools need more output space for full analysis
    _LARGE_OUTPUT_TOOLS = frozenset({
        "mt5_analyze", "mt5_smc", "trade_status", "trade_history",
        "mt5_journal_stats", "trade_plan",
    })

    # Tools that must NEVER be cached — they return real-time data
    _NOCACHE_TOOLS = frozenset({
        "mt5_get_price", "mt5_get_tick", "mt5_get_positions", "mt5_get_orders",
        "mt5_place_order", "mt5_close_position", "mt5_modify_position",
        "mt5_get_candles", "mt5_account_info", "mt5_place_pending",
        "mt5_modify_order", "mt5_cancel_order", "mt5_get_pending_orders",
        "trade_plan", "trade_status", "trade_control", "trade_pending",
        "trade_config", "trade_history", "web_search", "deep_search", "browse_web",
    })

    async def _execute_tool_cached(self, name: str, args: dict) -> ToolResult:
        """Execute a tool with short-TTL caching to avoid redundant calls."""
        # Never cache real-time tools
        if name in self._NOCACHE_TOOLS:
            return await self._tools.execute(name, **args)

        import hashlib
        cache_key = f"{name}:{hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()[:12]}"
        now = time.time()

        # Check cache
        if cache_key in self._tool_result_cache:
            cached_time, cached_result = self._tool_result_cache[cache_key]
            if now - cached_time < self._TOOL_CACHE_TTL:
                log.debug("tool_cache_hit", tool=name)
                return cached_result

        # Execute
        result = await self._tools.execute(name, **args)
        self._tool_result_cache[cache_key] = (now, result)

        # Trim cache (max 50 entries)
        if len(self._tool_result_cache) > 50:
            oldest_key = min(self._tool_result_cache, key=lambda k: self._tool_result_cache[k][0])
            del self._tool_result_cache[oldest_key]

        return result

    async def _execute_tools_parallel(
        self,
        tool_calls: list,
        tool_calls_made: list[dict],
        trace: Any,
        iteration: int,
    ) -> list[dict]:
        """Execute multiple tool calls in parallel via asyncio.gather."""

        async def _exec_one(tool_call) -> dict:
            func = tool_call.function
            tool_name = func.name
            try:
                tool_args = json.loads(func.arguments) if func.arguments else {}
            except json.JSONDecodeError:
                tool_args = {}

            log.info("tool_call", tool=tool_name, args=str(tool_args)[:200], iteration=iteration)

            result = await self._execute_tool_cached(tool_name, tool_args)

            tool_calls_made.append({
                "tool": tool_name,
                "args": tool_args,
                "success": result.success,
                "time_ms": result.execution_time_ms,
            })

            trace.add_step(
                "tool_call", tool_name,
                "success" if result.success else "failed",
                args=str(tool_args)[:100],
                time_ms=result.execution_time_ms,
            )

            # Format result for LLM (truncate to prevent overflow)
            result_content = result.output if result.success else f"Error: {result.error}"
            max_output = 8000 if tool_name in self._LARGE_OUTPUT_TOOLS else self._MAX_TOOL_OUTPUT
            if len(result_content) > max_output:
                truncated_len = len(result_content)
                result_content = (
                    result_content[:max_output]
                    + f"\n\n...[truncated: {truncated_len} chars total]"
                )

            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_content,
            }

        # Execute all tools in parallel
        results = await asyncio.gather(
            *[_exec_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        # Handle any exceptions from gather
        tool_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                log.error("tool_execution_error", error=str(result), iteration=iteration)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_calls[i].id,
                    "content": f"Error: Tool execution failed — {type(result).__name__}",
                })
            else:
                tool_results.append(result)

        return tool_results

    @staticmethod
    def _serialize_assistant_message(message) -> dict:
        """Clean serialization of assistant message — only include needed fields.

        Avoids fragile model_dump() that can inject unexpected fields.
        """
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }

        if message.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in message.tool_calls
            ]

        return msg

    @staticmethod
    def _quality_check(content: str, tool_calls: list[dict]) -> str:
        """Self-verification: check response quality and add warnings if needed.

        Returns the content, potentially with quality warnings appended.
        """
        if not content or not content.strip():
            return content

        # Check if tools were used but results not referenced
        if tool_calls:
            tool_names = {tc["tool"] for tc in tool_calls if tc.get("success")}
            search_used = "web_search" in tool_names
            # If web_search was used but no citation-like patterns found
            if search_used and not any(
                marker in content for marker in ["http", "nguồn", "source", "theo", "://"]
            ):
                content += "\n\n> _Lưu ý: Kết quả từ tìm kiếm web đã được tổng hợp ở trên._"

        return content

    def _build_fallback_response(self, tool_calls: list[dict]) -> str:
        """Build a synthesized response when max iterations is reached."""
        if not tool_calls:
            return "Xin lỗi, tôi không thể hoàn thành xử lý. Hãy thử lại với câu hỏi đơn giản hơn."

        lines = ["Tôi đã thực hiện các bước sau:\n"]
        successful = []
        failed = []
        for tc in tool_calls:
            if tc["success"]:
                successful.append(tc)
                lines.append(f"✅ **{tc['tool']}** ({tc['time_ms']}ms)")
            else:
                failed.append(tc)
                lines.append(f"❌ **{tc['tool']}** — thất bại ({tc['time_ms']}ms)")

        if failed:
            lines.append(f"\n⚠️ {len(failed)}/{len(tool_calls)} bước gặp lỗi.")

        if successful:
            lines.append(
                f"\n📊 Đã hoàn thành {len(successful)}/{len(tool_calls)} bước. "
                "Hãy hỏi lại nếu muốn tôi tổng hợp kết quả."
            )
        else:
            lines.append("\nTất cả các bước đều gặp lỗi. Hãy thử lại hoặc đặt câu hỏi khác.")

        return "\n".join(lines)
