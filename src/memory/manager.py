"""Memory Manager — Bộ não của JARVIS.

Kết hợp 3 lớp memory:
1. Working Memory (in-memory, current conversation)
2. Semantic Memory (facts, knowledge — persistent)
3. Episodic Memory (conversation history — persistent)

Tự động:
- Lưu mọi tin nhắn vào episodic memory
- Trích xuất facts từ conversation → semantic memory
- Truy xuất context liên quan khi xử lý tin nhắn mới
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from src.memory.episodic import EpisodicMemory
from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.semantic import SemanticMemory
from src.memory.summarizer import ConversationSummarizer
from src.utils.logging import get_logger

log = get_logger("memory.manager")

# Category labels for proactive memory display
_CATEGORY_LABELS = {
    "user_identity": "Danh tính",
    "user_preference": "Sở thích",
    "user_stated": "User nói",
    "user_directive": "Yêu cầu",
    "general": "Thông tin",
}

# Memory freshness thresholds (seconds)
_FRESH_THRESHOLD = 7 * 86400       # < 7 days = fresh
_AGING_THRESHOLD = 30 * 86400      # 7-30 days = aging
# > 30 days = stale

# Categories that rarely become stale
_DURABLE_CATEGORIES = {"user_identity", "user_directive"}

# Trading-related keywords for identifying trading memories/queries
_TRADING_KEYWORDS = re.compile(
    r"(?i)(?:xauusd|gold|vàng|giá vàng|mt5|trading|trade|lệnh|position|pending"
    r"|buy|sell|sl|tp|lot|pip|spread|bid|ask|order|entry|exit|profit|loss"
    r"|phân tích.*(?:thị trường|kỹ thuật|technical)|setup|zone|confluence"
    r"|risk|reward|breakeven|trailing|support|resistance|fibonacci|session)"
)


class MemoryManager:
    """Central memory coordinator for JARVIS."""

    def __init__(self) -> None:
        self.semantic = SemanticMemory()
        self.episodic = EpisodicMemory()
        self.summarizer = ConversationSummarizer()
        self.knowledge_graph = KnowledgeGraph()

    def session_key(self, channel: str, user_id: str) -> str:
        """Generate consistent session key for episodic memory."""
        return f"{channel}:{user_id}"

    async def process_user_message(self, session_key: str, content: str) -> None:
        """Process and store a user message."""
        self.episodic.save_message(session_key, "user", content)

        # Detect explicit memory requests
        await self._detect_remember_intent(content)

        # Extract entities for knowledge graph
        self.knowledge_graph.extract_entities_from_text(content)

    async def process_assistant_response(self, session_key: str, content: str) -> None:
        """Store assistant response."""
        self.episodic.save_message(session_key, "assistant", content)

    async def extract_facts_from_message(self, user_message: str) -> None:
        """Auto-extract implicit facts from user messages.

        Detects patterns like:
        - "tao làm ở Google" → job info
        - "project của tao tên là JARVIS" → project info
        - "tao ở Sài Gòn" → location
        - "deadline ngày mai" → time-sensitive info
        - "tao đang học ML" → activity/interest

        This is different from _detect_remember_intent which handles
        explicit "nhớ giúp..." requests. This handles IMPLICIT facts.
        """
        implicit_patterns: list[tuple[str, str, float]] = [
            # Job/work
            (r"(?:tao|tôi|mình)\s+(?:làm|đang làm|work)\s+(?:ở|tại|at|for)\s+(.+)",
             "user_work", 0.6),
            (r"(?:tao|tôi|mình)\s+là\s+(?:developer|engineer|designer|manager|teacher|student|sinh viên|giáo viên|bác sĩ|kỹ sư|lập trình viên)(?:\s+(.+))?",
             "user_work", 0.65),

            # Location
            (r"(?:tao|tôi|mình)\s+(?:ở|sống ở|đang ở|live in|from)\s+(.+)",
             "user_location", 0.55),

            # Learning/studying
            (r"(?:tao|tôi|mình)\s+(?:đang học|đang tìm hiểu|learning|studying)\s+(.+)",
             "user_interest", 0.5),

            # Projects
            (r"(?:project|dự án)\s+(?:của\s+)?(?:tao|tôi|mình)\s+(?:tên là|là|called)\s+(.+)",
             "user_project", 0.6),
        ]

        for pattern, category, importance in implicit_patterns:
            match = re.search(pattern, user_message, re.IGNORECASE)
            if match:
                # Store the full matched text for context
                fact = match.group(0).strip().rstrip(".")
                if len(fact) > 5:
                    await self.semantic.remember(
                        content=fact,
                        category=category,
                        source="implicit",
                        importance=importance,
                    )
                    log.info("implicit_fact_extracted", fact=fact[:80],
                             category=category)
                    return  # Only extract one fact per message

    async def get_relevant_context(self, query: str, session_key: str) -> str:
        """Build relevant context from all memory layers for the current query.

        Returns a string to inject into the system prompt.
        Proactively surfaces memories with clear labels so JARVIS can
        reference them naturally in conversation.
        """
        parts = []

        # 1. Search semantic memory for relevant facts (with freshness)
        memories = await self.semantic.search(query, top_k=5, min_similarity=0.25)
        is_trading_query = self._is_trading_query(query)
        if memories:
            now_ts = time.time()
            fact_lines = []
            for m in memories:
                cat = m.get("category", "general")
                content = m["content"]
                # Skip stale trading memories — prices/positions/analysis expire fast
                if is_trading_query and self._is_trading_memory(content):
                    freshness = self._memory_freshness(m, now_ts)
                    if freshness in ("stale", "aging"):
                        log.debug("skip_stale_trading_memory", content=content[:60])
                        continue
                label = _CATEGORY_LABELS.get(cat, "Thông tin")
                freshness = self._memory_freshness(m, now_ts)
                if freshness == "stale":
                    content += " ⚠️(có thể đã cũ, hãy xác nhận nếu dùng)"
                fact_lines.append(f"- [{label}] {content}")

            parts.append(
                "## Bộ nhớ về user (dùng tự nhiên trong trả lời, "
                "không liệt kê trừ khi user hỏi):\n" + "\n".join(fact_lines)
            )

        # 2. Get conversation summaries (cross-session continuity)
        summaries = self.summarizer.get_session_summaries(session_key, limit=2)
        if summaries:
            summary_text = "\n".join(f"- {s}" for s in summaries)
            parts.append(
                "## Tóm tắt cuộc trò chuyện trước:\n" + summary_text
            )

        # 3. Get recent conversation context
        recent = self.episodic.get_recent_context(session_key, n_messages=20)
        if recent:
            # Don't add if it's the same session (already in working memory)
            msg_count = self.episodic.count_messages(session_key)
            if msg_count > 20:
                parts.append("## Lịch sử gần đây:\n" + recent)

        # 4. Knowledge graph context (entities & relations)
        kg_context = self.knowledge_graph.build_context(query)
        if kg_context:
            parts.append("## Knowledge Graph:\n" + kg_context)

        if not parts:
            return ""

        return "\n\n".join(parts)

    async def _detect_remember_intent(self, content: str) -> None:
        """Detect if user explicitly asks JARVIS to remember something.

        Covers Vietnamese + English patterns for explicit memory requests,
        identity statements, preferences, and naming requests.
        """
        # Pattern → (category, importance, use_full_match)
        # use_full_match=True stores the whole matched text instead of group(1)
        remember_patterns: list[tuple[str, str, float, bool]] = [
            # Explicit memory requests
            (r"(?:nhớ|ghi nhớ|lưu ý|remember|note)\s+(?:giúp\s+)?(?:rằng\s+|là\s+|that\s+)?(.+)",
             "user_stated", 0.9, False),

            # Naming / identity: "gọi tôi là X", "từ nay gọi tôi là X"
            (r"(?:từ nay\s+)?(?:hãy\s+)?gọi\s+(?:tôi|tao|mình)\s+là\s+(.+)",
             "user_identity", 0.95, True),

            # Name statements: "tên tôi là X", "my name is X"
            (r"(?:tên|name)\s+(?:tôi|tao|mình|của tôi|of mine|i'm|my name)\s+(?:là\s+)?(.+)",
             "user_identity", 0.95, True),
            (r"(?:i'm|i am|my name is)\s+(.+)",
             "user_identity", 0.95, True),
            # "tôi là X" — but NOT questions like "tôi là ai"
            (r"(?:tôi|tao|mình)\s+(?:tên\s+)?là\s+(?!ai|gì|sao|nào|như)(\w+)",
             "user_identity", 0.95, True),

            # Role/relationship: "tôi là chủ/boss/owner"
            (r"(?:tôi|tao|mình)\s+là\s+(?:chủ|boss|owner|admin|sếp)\s*(?:của\s+)?(.+)?",
             "user_identity", 0.95, True),

            # Preferences: "tôi thích X", "tôi muốn X", "tôi ghét X"
            (r"(?:tôi|tao|mình)\s+(?:thích|yêu thích|prefer|love|ghét|hate|không thích)\s+(.+)",
             "user_preference", 0.7, True),

            # "từ nay..." directives
            (r"từ\s+(?:nay|giờ|bây giờ)\s+(.+)",
             "user_directive", 0.85, True),
        ]

        for pattern, category, importance, use_full in remember_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                if use_full:
                    # Store the full matched portion for more context
                    fact = match.group(0).strip().rstrip(".")
                else:
                    fact = match.group(1).strip().rstrip(".")

                if len(fact) > 2:  # Allow short names like "Bi"
                    await self.semantic.remember(
                        content=fact,
                        category=category,
                        source="explicit",
                        importance=importance,
                    )
                    log.info("explicit_memory_stored", fact=fact[:80],
                             category=category)
                    return

    async def remember_fact(
        self,
        content: str,
        category: str = "general",
        importance: float = 0.5,
    ) -> str:
        """Explicitly store a fact in semantic memory."""
        return await self.semantic.remember(
            content=content,
            category=category,
            source="system",
            importance=importance,
        )

    @staticmethod
    def _is_trading_query(query: str) -> bool:
        """Check if a query is about trading/market data."""
        return bool(_TRADING_KEYWORDS.search(query))

    @staticmethod
    def _is_trading_memory(content: str) -> bool:
        """Check if a memory contains trading-specific data (prices, positions, analysis)."""
        return bool(_TRADING_KEYWORDS.search(content))

    @staticmethod
    def _memory_freshness(memory: dict, now_ts: float) -> str:
        """Determine if a memory is fresh, aging, or stale."""
        cat = memory.get("category", "general")
        # Identity and directives don't become stale easily
        if cat in _DURABLE_CATEGORIES:
            return "fresh"

        created = memory.get("created_at", "")
        if not created:
            return "fresh"  # No timestamp, assume OK

        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_seconds = now_ts - dt.timestamp()
        except (ValueError, TypeError):
            return "fresh"

        # Trading memories expire much faster (prices/positions change constantly)
        content = memory.get("content", "")
        if _TRADING_KEYWORDS.search(content):
            if age_seconds < 3600:       # < 1 hour = fresh
                return "fresh"
            elif age_seconds < 14400:    # 1-4 hours = aging
                return "aging"
            return "stale"

        if age_seconds < _FRESH_THRESHOLD:
            return "fresh"
        elif age_seconds < _AGING_THRESHOLD:
            return "aging"
        return "stale"

    async def refresh_memory(self, content: str, new_content: str) -> str:
        """Update a memory's content (refresh stale fact)."""
        # Find and delete old, store new
        memories = await self.semantic.search(content, top_k=1, min_similarity=0.8)
        if memories:
            self.semantic.delete(memories[0]["id"])
        return await self.semantic.remember(
            content=new_content,
            category="user_stated",
            source="refresh",
            importance=0.8,
        )

    def get_stats(self) -> dict:
        """Get memory statistics."""
        return {
            "semantic_memories": self.semantic.count(),
            "sessions": len(self.episodic.get_all_sessions()),
            "total_messages": sum(
                s["msg_count"] for s in self.episodic.get_all_sessions()
            ),
        }
