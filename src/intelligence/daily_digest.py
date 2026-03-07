"""Daily Digest — Tổng hợp tin tức hàng ngày theo sở thích user.

Tự động tìm kiếm web theo topics user quan tâm, tổng hợp thành
bản tin ngắn gọn gửi mỗi sáng qua Telegram.

Flow:
1. Lấy top topics từ UserModel
2. Tìm kiếm tin tức mới nhất cho mỗi topic
3. Tổng hợp + format thành digest dễ đọc
4. Gửi qua ProactiveEngine callback
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.utils.logging import get_logger

log = get_logger("intelligence.digest")

# Default topics khi user chưa có đủ data
_DEFAULT_TOPICS = ["AI", "technology"]

# Max topics per digest
_MAX_TOPICS = 4

# Max results per topic
_MAX_RESULTS_PER_TOPIC = 3

# Topic → search query mapping (optimize search for specific topics)
_TOPIC_QUERIES: dict[str, str] = {
    "ai": "AI artificial intelligence news today",
    "ai/ml": "AI machine learning latest news",
    "python": "Python programming news updates",
    "trading": "trading market analysis news today",
    "security": "cybersecurity news vulnerabilities today",
    "docker": "Docker Kubernetes container news",
    "web": "web development frontend backend news",
    "data": "data engineering analytics news",
    "crypto": "cryptocurrency blockchain news today",
    "linux": "Linux open source news",
    "rust": "Rust programming language news",
    "golang": "Go programming news",
    "javascript": "JavaScript TypeScript news",
}


@dataclass
class DigestItem:
    """A single news item in the digest."""
    title: str
    snippet: str
    url: str
    topic: str


@dataclass
class DailyDigest:
    """Complete daily digest."""
    items: list[DigestItem] = field(default_factory=list)
    topics_searched: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)
    search_time_ms: int = 0

    def to_markdown(self) -> str:
        """Format digest as Markdown for Telegram."""
        if not self.items:
            return ""

        parts = [f"📰 *Daily Digest* — {self.generated_at.strftime('%d/%m/%Y')}\n"]

        # Group items by topic
        by_topic: dict[str, list[DigestItem]] = {}
        for item in self.items:
            by_topic.setdefault(item.topic, []).append(item)

        for topic, items in by_topic.items():
            parts.append(f"\n*{_topic_emoji(topic)} {topic.upper()}*")
            for item in items:
                title = _escape_md(item.title[:80])
                snippet = _escape_md(item.snippet[:150])
                parts.append(f"• [{title}]({item.url})")
                if snippet:
                    parts.append(f"  _{snippet}_")

        parts.append(f"\n⏱ Tìm trong {self.search_time_ms}ms | {len(self.items)} tin")
        return "\n".join(parts)

    def to_plain(self) -> str:
        """Format digest as plain text (fallback)."""
        if not self.items:
            return ""

        parts = [f"Daily Digest — {self.generated_at.strftime('%d/%m/%Y')}\n"]

        by_topic: dict[str, list[DigestItem]] = {}
        for item in self.items:
            by_topic.setdefault(item.topic, []).append(item)

        for topic, items in by_topic.items():
            parts.append(f"\n[{topic.upper()}]")
            for item in items:
                parts.append(f"- {item.title[:80]}")
                if item.snippet:
                    parts.append(f"  {item.snippet[:120]}")
                parts.append(f"  {item.url}")

        return "\n".join(parts)


class DigestGenerator:
    """Generates daily digest by searching web for user's topics.

    Usage:
        gen = DigestGenerator(user_model)
        digest = await gen.generate(user_id="telegram_1991690969")
        text = digest.to_markdown()
    """

    def __init__(self, user_model: Any = None) -> None:
        self._user_model = user_model
        self._last_digest: DailyDigest | None = None
        self._last_digest_date: str = ""

    def already_generated_today(self) -> bool:
        """Check if digest was already generated today."""
        return self._last_digest_date == datetime.now().strftime("%Y-%m-%d")

    async def generate(
        self,
        user_id: str = "default",
        topics: list[str] | None = None,
        max_topics: int = _MAX_TOPICS,
    ) -> DailyDigest:
        """Generate daily digest.

        Args:
            user_id: User ID to fetch topics from UserModel
            topics: Override topics (skip UserModel lookup)
            max_topics: Max number of topics to search
        """
        start = time.monotonic()

        # 1. Get topics
        if topics is None:
            topics = self._get_user_topics(user_id, max_topics)

        if not topics:
            topics = _DEFAULT_TOPICS[:max_topics]

        log.info("digest_generating", topics=topics)

        # 2. Search for each topic in parallel
        search_tasks = []
        for topic in topics[:max_topics]:
            search_tasks.append(self._search_topic(topic))

        results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # 3. Collect items
        all_items: list[DigestItem] = []
        topics_searched = []
        seen_urls: set[str] = set()

        for topic, result in zip(topics, results):
            if isinstance(result, Exception):
                log.warning("digest_search_failed", topic=topic, error=str(result))
                continue
            topics_searched.append(topic)
            for item in result:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)

        elapsed = int((time.monotonic() - start) * 1000)

        digest = DailyDigest(
            items=all_items,
            topics_searched=topics_searched,
            search_time_ms=elapsed,
        )

        self._last_digest = digest
        self._last_digest_date = datetime.now().strftime("%Y-%m-%d")

        log.info("digest_generated", items=len(all_items),
                topics=len(topics_searched), time_ms=elapsed)
        return digest

    def _get_user_topics(self, user_id: str, max_topics: int) -> list[str]:
        """Get user's top topics from UserModel."""
        if not self._user_model:
            return _DEFAULT_TOPICS[:max_topics]

        try:
            model = self._user_model.get_or_create(user_id)
            topics_raw = model.get("topics", "{}")
            topics = json.loads(topics_raw) if isinstance(topics_raw, str) else topics_raw

            if not topics:
                return _DEFAULT_TOPICS[:max_topics]

            # Sort by frequency, return top N
            sorted_topics = sorted(topics.items(), key=lambda x: -x[1])
            return [t[0] for t in sorted_topics[:max_topics]]
        except Exception as e:
            log.warning("digest_topics_error", error=str(e))
            return _DEFAULT_TOPICS[:max_topics]

    async def _search_topic(self, topic: str) -> list[DigestItem]:
        """Search web for a single topic, return DigestItems."""
        from src.tools.web_search import search_web

        # Build optimized query
        query = _TOPIC_QUERIES.get(topic.lower(), f"{topic} latest news today")

        try:
            result = await search_web(query, max_results=_MAX_RESULTS_PER_TOPIC)
            if not result.success:
                return []

            items = []
            for r in result.data.get("results", []):
                title = r.get("title", "").strip()
                snippet = r.get("body", r.get("snippet", "")).strip()
                url = r.get("href", r.get("url", "")).strip()

                if not title or not url:
                    continue

                items.append(DigestItem(
                    title=title,
                    snippet=snippet,
                    url=url,
                    topic=topic,
                ))

            return items

        except Exception as e:
            log.warning("digest_topic_search_error", topic=topic, error=str(e))
            return []

    @property
    def last_digest(self) -> DailyDigest | None:
        return self._last_digest

    def get_stats(self) -> dict:
        return {
            "last_date": self._last_digest_date or None,
            "last_items": len(self._last_digest.items) if self._last_digest else 0,
            "last_topics": self._last_digest.topics_searched if self._last_digest else [],
        }


def _topic_emoji(topic: str) -> str:
    """Map topic to emoji."""
    mapping = {
        "ai": "🤖", "ai/ml": "🤖", "python": "🐍", "trading": "📈",
        "security": "🔒", "docker": "🐳", "web": "🌐", "data": "📊",
        "crypto": "₿", "linux": "🐧", "rust": "⚙️", "golang": "🔷",
        "javascript": "📜", "technology": "💻",
    }
    return mapping.get(topic.lower(), "📌")


def _escape_md(text: str) -> str:
    """Escape Markdown special characters for Telegram."""
    for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(ch, f"\\{ch}")
    return text
