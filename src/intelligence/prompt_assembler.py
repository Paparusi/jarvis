"""Prompt Assembler v2 — .md-based identity injection + intelligent context.

Loads JARVIS identity from workspace/JARVIS.md and user profile from
workspace/USER.md instead of hardcoded strings. Falls back to inline
defaults when .md files are unavailable.

Priority-based context injection (in order):
1. System prompt (from JARVIS.md identity + tool directives) — always present
2. User profile (from USER.md digital twin) — when available
3. Skill metadata summary — always present (compact)
4. Matched skill bodies — when triggered
5. Semantic memory context — relevant facts
6. Episodic context — recent conversation history
7. User message — always present

Token budget ensures we don't exceed context window limits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.memory.summarizer import ConversationSummarizer
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("intelligence.prompt_assembler")

# Approximate chars per token for budget estimation
CHARS_PER_TOKEN = 3.5

# Fallback when JARVIS.md is not available
_FALLBACK_SYSTEM_PROMPT = """Bạn là JARVIS — trợ lý AI cá nhân thông minh, đáng tin cậy, và có cá tính.
- Thân thiện, trung thực, ngắn gọn
- Trả lời bằng ngôn ngữ user dùng (mặc định tiếng Việt)
- Dùng tools khi cần, không đoán"""

_TOOL_INSTRUCTIONS = """
# TOOL CALLING — QUAN TRỌNG
Bạn có quyền truy cập các tools qua function calling interface.

1. **LUÔN dùng tools thay vì đoán** — Khi cần thông tin thực tế (giá cả, tin tức,
   thời tiết, dữ liệu), PHẢI gọi tool. KHÔNG trả lời từ kiến thức cũ.
2. **Nếu không chắc → tìm kiếm** — Gọi `web_search` để xác minh.
3. **Đọc URL khi user gửi link** — Dùng `fetch_url`.
4. **Chạy code khi cần tính toán** — Dùng `run_python`.
5. **Multi-step: lập kế hoạch trước** — Nêu ngắn gọn kế hoạch rồi thực hiện.
6. **Tổng hợp kết quả tool** — Tổng hợp thành câu trả lời tự nhiên, có nguồn.

# REAL-TIME DATA — BẮT BUỘC
**KHÔNG BAO GIỜ lấy giá, dữ liệu thị trường, hoặc thông tin real-time từ conversation history.**
Giá trong lịch sử chat luôn STALE/CŨ. Bạn PHẢI:
- Hỏi giá XAUUSD/vàng/gold/forex → gọi `mt5_price` hoặc `web_search`
- Hỏi phân tích thị trường → gọi `mt5_candles` + `mt5_price` để lấy data mới nhất
- Hỏi tin tức → gọi `web_search`
- Hỏi thời tiết → gọi `web_search`
Dữ liệu từ vài phút trước đã có thể sai. LUÔN gọi tool để lấy data fresh."""


def _load_md_file(filename: str) -> str | None:
    """Load a .md file from workspace/. Returns None if not found."""
    try:
        path = get_project_root() / "workspace" / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def _build_system_prompt_from_md() -> str:
    """Build system prompt from JARVIS.md + USER.md files.

    Returns a complete system prompt assembled from .md identity files,
    or falls back to inline default if files not available.
    """
    jarvis_md = _load_md_file("JARVIS.md")
    user_md = _load_md_file("USER.md")

    if not jarvis_md:
        log.warning("jarvis_md_not_found", msg="Using fallback system prompt")
        return _FALLBACK_SYSTEM_PROMPT + _TOOL_INSTRUCTIONS

    parts = []

    # Core identity from JARVIS.md
    parts.append(jarvis_md.strip())

    # User profile from USER.md (compact summary)
    if user_md:
        parts.append("\n---\n")
        parts.append(user_md.strip())

    # Tool instructions (always appended)
    parts.append(_TOOL_INSTRUCTIONS)

    return "\n".join(parts)


# Legacy alias for tests
BASE_SYSTEM_PROMPT = _FALLBACK_SYSTEM_PROMPT + _TOOL_INSTRUCTIONS


def estimate_tokens(text: str) -> int:
    """Rough token count estimation."""
    return int(len(text) / CHARS_PER_TOKEN)


class PromptAssembler:
    """Build LLM prompts with intelligent context management.

    v2: Loads identity from workspace/JARVIS.md + USER.md instead of hardcoded.
    """

    def __init__(
        self,
        max_context_tokens: int = 30000,
        skill_summary: str = "",
        tool_registry: "ToolRegistry | None" = None,
        system_prompt_override: str | None = None,
    ) -> None:
        self._max_tokens = max_context_tokens
        self._skill_summary = skill_summary
        self._tool_registry = tool_registry
        self._system_prompt_override = system_prompt_override

        # v2: Build prompt from .md files if no override provided
        if system_prompt_override:
            self._system_prompt = system_prompt_override
        else:
            self._system_prompt = _build_system_prompt_from_md()
            log.info("prompt_assembler_v2", source="JARVIS.md+USER.md",
                     prompt_len=len(self._system_prompt))

        self._base_tokens = estimate_tokens(self._system_prompt)
        self._summarizer = ConversationSummarizer(
            keep_recent=10,
            trigger_threshold=20,
        )

    def update_skill_summary(self, summary: str) -> None:
        self._skill_summary = summary

    def _build_tool_descriptions(self) -> str:
        """Auto-generate tool descriptions from registry."""
        if not self._tool_registry:
            return ""

        tools = self._tool_registry.get_all()
        if not tools:
            return ""

        lines = ["\n# AVAILABLE TOOLS"]
        for tool in tools:
            params_str = ", ".join(
                f"{p.name}: {p.type}" + ("?" if not p.required else "")
                for p in tool.parameters
            )
            lines.append(f"- **{tool.name}**({params_str}) — {tool.description}")

        return "\n".join(lines)

    async def assemble(
        self,
        user_message: str,
        history: list[dict[str, str]],
        memory_context: str = "",
        skill_context: str = "",
        session_key: str = "",
    ) -> list[dict[str, Any]]:
        """Assemble complete message list for LLM call."""
        # Build system prompt with datetime awareness + tool list
        now = datetime.now(timezone.utc)
        time_context = f"\n[Thời gian hiện tại: {now.strftime('%Y-%m-%d %H:%M UTC')} | {now.strftime('%A')}]"

        system_parts = [self._system_prompt]

        # Auto-generated tool descriptions (always accurate, never stale)
        tool_desc = self._build_tool_descriptions()
        if tool_desc:
            system_parts.append(tool_desc)

        system_parts.append(time_context)

        if self._skill_summary:
            system_parts.append(f"\n{self._skill_summary}")

        # Budget tracking
        used_tokens = estimate_tokens("\n".join(system_parts))
        user_msg_tokens = estimate_tokens(user_message)
        used_tokens += user_msg_tokens

        # Memory context (high priority)
        if memory_context:
            mem_tokens = estimate_tokens(memory_context)
            if used_tokens + mem_tokens < self._max_tokens * 0.8:
                system_parts.append(
                    f"\n--- BỘ NHỚ ---\n{memory_context}\n--- HẾT BỘ NHỚ ---"
                )
                used_tokens += mem_tokens
            else:
                available = int((self._max_tokens * 0.3 - used_tokens) * CHARS_PER_TOKEN)
                if available > 100:
                    truncated = memory_context[:available] + "\n...(truncated)"
                    system_parts.append(
                        f"\n--- BỘ NHỚ ---\n{truncated}\n--- HẾT BỘ NHỚ ---"
                    )
                    used_tokens += estimate_tokens(truncated)

        # Skill context (high priority — task guidance)
        if skill_context:
            skill_tokens = estimate_tokens(skill_context)
            if used_tokens + skill_tokens < self._max_tokens * 0.85:
                system_parts.append(
                    f"\n# SKILL INSTRUCTIONS — Follow precisely:\n{skill_context}"
                )
                used_tokens += skill_tokens
            else:
                available = int((self._max_tokens * 0.2) * CHARS_PER_TOKEN)
                if available > 100:
                    truncated = skill_context[:available] + "\n...(truncated)"
                    system_parts.append(
                        f"\n# SKILL INSTRUCTIONS:\n{truncated}"
                    )
                    used_tokens += estimate_tokens(truncated)

        system_content = "\n".join(system_parts)

        # Build messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
        ]

        # History (summarize if long, then trim to fit budget)
        remaining_tokens = self._max_tokens - used_tokens
        if history and remaining_tokens > 200:
            effective_history = history

            if self._summarizer.needs_summarization(history):
                summary, recent = await self._summarizer.summarize(
                    history, session_key=session_key,
                )
                if summary:
                    effective_history = self._summarizer.build_summarized_history(
                        summary, recent
                    )

            trimmed = self._trim_history(effective_history, remaining_tokens)
            messages.extend(trimmed)

        # User message (always included)
        messages.append({"role": "user", "content": user_message})

        total_tokens = estimate_tokens(
            "".join(m.get("content", "") for m in messages)
        )
        log.debug(
            "prompt_assembled",
            system_tokens=estimate_tokens(system_content),
            history_messages=len(messages) - 2,
            total_est_tokens=total_tokens,
            has_memory=bool(memory_context),
            has_skills=bool(skill_context),
            has_tools=bool(tool_desc),
        )

        return messages

    def _trim_history(
        self,
        history: list[dict[str, str]],
        max_tokens: int,
    ) -> list[dict[str, str]]:
        """Keep most recent messages that fit within token budget."""
        result = []
        used = 0
        for msg in reversed(history):
            msg_tokens = estimate_tokens(msg.get("content", ""))
            if used + msg_tokens > max_tokens:
                break
            result.insert(0, msg)
            used += msg_tokens
        return result
