"""Conversation Summarizer — Compress long conversations to fit context window.

When a conversation exceeds a threshold, older messages get summarized
into a compact text. Recent messages stay verbatim for context continuity.

Strategy:
- Keep last N messages verbatim (default 10)
- Summarize everything before that into a brief paragraph
- Use local model for summarization (free, fast), cloud fallback
- Persist summaries in DB for cross-session continuity
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from src.intelligence.claude_client import get_claude_client
from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("memory.summarizer")

# Summarization prompt
_SUMMARIZE_PROMPT = """Tóm tắt cuộc hội thoại sau thành 2-3 câu ngắn gọn bằng tiếng Việt.
Giữ lại: chủ đề chính, quyết định quan trọng, thông tin user đã chia sẻ.
Bỏ qua: chi tiết nhỏ, chào hỏi, câu nói lặp lại.

Cuộc hội thoại:
{conversation}

Tóm tắt:"""


class ConversationSummarizer:
    """Summarize long conversations to save context tokens.

    Summaries are persisted in the episodic_memories table so they
    survive restarts and can be loaded for cross-session context.
    """

    def __init__(
        self,
        keep_recent: int = 10,
        trigger_threshold: int = 20,
        local_model: str = "ollama/qwen3:4b",
    ) -> None:
        self._keep_recent = keep_recent
        self._trigger_threshold = trigger_threshold
        self._local_model = local_model
        self._cache: dict[str, str] = {}  # hash → summary

    def needs_summarization(self, messages: list[dict[str, str]]) -> bool:
        """Check if conversation needs summarization."""
        return len(messages) > self._trigger_threshold

    def compression_level(self, messages: list[dict[str, str]]) -> int:
        """Determine compression level based on conversation length.

        Level 0: No compression (< threshold)
        Level 1: Summarize old, keep 10 recent (threshold-50 messages)
        Level 2: Summarize old, keep 6 recent (50-100 messages)
        Level 3: Aggressive — keep 4 recent, compact summary (100+ messages)
        """
        n = len(messages)
        if n <= self._trigger_threshold:
            return 0
        elif n <= 50:
            return 1
        elif n <= 100:
            return 2
        else:
            return 3

    async def summarize(
        self,
        messages: list[dict[str, str]],
        session_key: str = "",
    ) -> tuple[str, list[dict[str, str]]]:
        """Summarize older messages, keep recent ones verbatim.

        Returns:
            (summary_text, recent_messages)
        """
        if not self.needs_summarization(messages):
            return "", messages

        # Progressive compression: fewer recent messages for longer conversations
        level = self.compression_level(messages)
        keep = {0: self._keep_recent, 1: self._keep_recent, 2: 6, 3: 4}.get(level, self._keep_recent)

        split_point = len(messages) - keep
        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]

        # Check in-memory cache
        cache_key = self._hash_messages(old_messages)
        if cache_key in self._cache:
            log.debug("summary_cache_hit", messages_summarized=len(old_messages))
            return self._cache[cache_key], recent_messages

        # Check DB for existing summary
        db_summary = self._load_summary(cache_key)
        if db_summary:
            self._cache[cache_key] = db_summary
            return db_summary, recent_messages

        # Build conversation text for summarization
        conv_text = self._format_conversation(old_messages)

        # Summarize
        summary = await self._generate_summary(conv_text)

        if summary:
            self._cache[cache_key] = summary
            # Persist to DB
            self._save_summary(session_key, summary, cache_key, len(old_messages))
            log.info(
                "conversation_summarized",
                old_messages=len(old_messages),
                recent_messages=len(recent_messages),
                summary_len=len(summary),
            )

        return summary, recent_messages

    def build_summarized_history(
        self,
        summary: str,
        recent_messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Build message list with summary prepended."""
        result: list[dict[str, str]] = []

        if summary:
            result.append({
                "role": "system",
                "content": f"[Tóm tắt cuộc trò chuyện trước đó]\n{summary}",
            })

        result.extend(recent_messages)
        return result

    def get_session_summaries(self, session_key: str, limit: int = 3) -> list[str]:
        """Get recent summaries for a session (for cross-session context)."""
        conn = get_connection()
        rows = conn.execute(
            """SELECT summary FROM episodic_memories
               WHERE session_id = ? AND outcome = 'conversation_summary'
               ORDER BY created_at DESC LIMIT ?""",
            (session_key, limit),
        ).fetchall()
        return [r["summary"] for r in rows]

    async def _generate_summary(self, conversation_text: str) -> str:
        """Generate summary using Claude API, with simple fallback."""
        prompt = _SUMMARIZE_PROMPT.format(conversation=conversation_text[:3000])

        try:
            client = get_claude_client()
            response = await client.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
                temperature=0.3,
            )

            content = response.choices[0].message.content or ""
            result = content.strip()
            if len(result) > 20:
                return result
        except Exception as e:
            log.debug("summarization_failed", error=str(e))

        # Fallback: simple extraction
        return self._fallback_summary(conversation_text)

    def _fallback_summary(self, conversation_text: str) -> str:
        """Simple fallback when LLM summarization fails."""
        lines = conversation_text.split("\n")
        # Take first and last few exchanges
        if len(lines) > 6:
            summary_lines = lines[:3] + ["..."] + lines[-3:]
        else:
            summary_lines = lines
        return "\n".join(summary_lines)

    def _save_summary(
        self, session_key: str, summary: str, cache_key: str, msg_count: int,
    ) -> None:
        """Persist summary to episodic_memories table."""
        try:
            conn = get_connection()
            conn.execute(
                """INSERT OR REPLACE INTO episodic_memories
                   (id, session_id, summary, details, outcome, importance)
                   VALUES (?, ?, ?, ?, 'conversation_summary', 0.6)""",
                (cache_key[:12], session_key, summary,
                 f"Summarized {msg_count} messages"),
            )
            conn.commit()
        except Exception as e:
            log.debug("summary_save_failed", error=str(e))

    @staticmethod
    def _load_summary(cache_key: str) -> str | None:
        """Load a summary from DB by cache key."""
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT summary FROM episodic_memories WHERE id = ?",
                (cache_key[:12],),
            ).fetchone()
            return row["summary"] if row else None
        except Exception:
            return None

    @staticmethod
    def _format_conversation(messages: list[dict[str, str]]) -> str:
        """Format messages into readable conversation text."""
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "JARVIS"
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _hash_messages(messages: list[dict[str, str]]) -> str:
        """Create a hash of messages for caching."""
        content = "|".join(
            f"{m.get('role', '')}:{m.get('content', '')[:100]}" for m in messages
        )
        return hashlib.md5(content.encode()).hexdigest()
