"""LLM integration via Claude API with memory-augmented context."""

from __future__ import annotations

import time
from typing import Any

from src.gateway.models import AgentResponse, SessionState
from src.intelligence.claude_client import get_claude_client
from src.utils.config import load_config
from src.utils.logging import get_logger

log = get_logger("llm")

SYSTEM_PROMPT = """Bạn là JARVIS — trợ lý AI cá nhân thông minh, trung thực, và hữu ích.

Quy tắc:
- Trả lời bằng ngôn ngữ mà user sử dụng (mặc định tiếng Việt)
- Ngắn gọn, đi thẳng vào vấn đề, không lòng vòng
- Khi không chắc chắn → nói rõ, không bịa
- Có thể dùng emoji khi phù hợp
- Gọi user là "bạn" hoặc theo cách user muốn
- Khi user nói "nhớ giúp..." hoặc "remember..." → xác nhận đã ghi nhớ

Khả năng hiện tại:
- Trò chuyện, trả lời câu hỏi, phân tích vấn đề
- Viết code, debug, review
- Nghiên cứu, tổng hợp thông tin
- Lập kế hoạch, chia nhỏ task
- Ghi nhớ thông tin user yêu cầu (bộ nhớ dài hạn)

Bạn có bộ nhớ dài hạn — bạn nhớ được thông tin từ các cuộc trò chuyện trước."""


async def chat_completion(
    session: SessionState,
    user_message: str,
    memory_context: str = "",
) -> AgentResponse:
    """Send message to LLM with memory-augmented context."""
    config = load_config()
    intel_config = config.get("intelligence", {})
    model = intel_config.get("default_model", "claude-sonnet-4-20250514")
    max_tokens = intel_config.get("max_tokens", 4096)
    temperature = intel_config.get("temperature", 0.7)

    # Build system prompt with memory context
    system_content = SYSTEM_PROMPT
    if memory_context:
        system_content += f"\n\n--- BỘ NHỚ ---\n{memory_context}\n--- HẾT BỘ NHỚ ---"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
    ]
    messages.extend(session.get_history())
    messages.append({"role": "user", "content": user_message})

    start_time = time.monotonic()

    try:
        client = get_claude_client()
        response = await client.complete(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        content = response.choices[0].message.content or ""
        usage = response.usage

        result = AgentResponse(
            request_id=session.session_id,
            session_id=session.session_id,
            content=content,
            model_used=model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            latency_ms=elapsed_ms,
            cost_usd=0.0,
        )

        log.info(
            "llm_response",
            model=model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=elapsed_ms,
            has_memory=bool(memory_context),
        )
        return result

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log.error("llm_error", model=model, error=str(e), latency_ms=elapsed_ms)
        return AgentResponse(
            request_id=session.session_id,
            session_id=session.session_id,
            content=f"Xin lỗi, tôi gặp lỗi khi xử lý: {e}",
            model_used=model,
            latency_ms=elapsed_ms,
        )
