"""Proactive Intelligence Engine — JARVIS chủ động gợi ý.

Instead of only reacting to user messages, JARVIS can:
- Morning briefing + daily digest (news curated by user interests)
- Pattern-based suggestions (recurring tasks)
- Idle-time insights (interesting facts about user's topics)
- Follow-up reminders (unfinished conversations)
- Health alerts (system status changes)

Runs as background task, pushes notifications via callback.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Awaitable

from src.intelligence.daily_digest import DigestGenerator
from src.memory.semantic import SemanticMemory
from src.digital_twin.user_model import UserModel
from src.brain.collector import DataCollector
from src.utils.logging import get_logger

log = get_logger("intelligence.proactive")


@dataclass
class ProactiveInsight:
    """A proactive suggestion/notification from JARVIS."""
    type: str          # "briefing", "suggestion", "followup", "alert", "insight"
    title: str
    content: str
    priority: float    # 0.0 - 1.0
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


# Type for notification callback
NotifyCallback = Callable[[ProactiveInsight], Awaitable[None]]


class ProactiveEngine:
    """Background engine that generates proactive insights.

    Usage:
        engine = ProactiveEngine(memory, user_model, collector)
        engine.on_notify(my_callback)
        await engine.start()  # Runs in background
    """

    def __init__(
        self,
        memory: SemanticMemory,
        user_model: UserModel,
        collector: DataCollector,
        user_id: str = "default",
    ) -> None:
        self._memory = memory
        self._user_model = user_model
        self._collector = collector
        self._user_id = user_id
        self._callbacks: list[NotifyCallback] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_briefing: datetime | None = None
        self._last_interaction: float = time.monotonic()
        self._insights_sent: list[str] = []  # Dedup
        self._digest = DigestGenerator(user_model)

    def on_notify(self, callback: NotifyCallback) -> None:
        """Register a callback for proactive notifications."""
        self._callbacks.append(callback)

    def mark_interaction(self) -> None:
        """Mark that user just interacted (reset idle timer)."""
        self._last_interaction = time.monotonic()

    async def start(self) -> None:
        """Start background proactive loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("proactive_started")

    async def stop(self) -> None:
        """Stop the proactive engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("proactive_stopped")

    async def _loop(self) -> None:
        """Main proactive loop — checks every 5 minutes."""
        while self._running:
            try:
                await asyncio.sleep(300)  # 5 minutes

                # Check what proactive actions to take
                now = datetime.now()

                # 1. Morning briefing (7-9 AM, once per day)
                if 7 <= now.hour <= 9 and not self._briefing_sent_today():
                    await self._generate_briefing()

                # 2. Idle suggestions (after 30 min idle)
                idle_minutes = (time.monotonic() - self._last_interaction) / 60
                if 30 <= idle_minutes <= 60:
                    await self._generate_idle_insight()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("proactive_loop_error", error=str(e))

    def _briefing_sent_today(self) -> bool:
        if not self._last_briefing:
            return False
        return self._last_briefing.date() == datetime.now().date()

    async def _generate_briefing(self) -> None:
        """Generate morning briefing + daily digest."""
        self._last_briefing = datetime.now()

        # Gather data
        model = self._user_model.get_or_create(self._user_id)
        stats = self._collector.get_stats()
        memories = self._memory.get_all(limit=5)

        # Build briefing
        parts = ["Chào buổi sáng! Đây là tóm tắt cho hôm nay:\n"]

        if stats.get("total_records", 0) > 0:
            parts.append(f"- Hôm qua có {stats.get('recent_records', 0)} cuộc trò chuyện")

        if memories:
            parts.append("- Bộ nhớ gần đây:")
            for m in memories[:3]:
                parts.append(f"  • {m['content'][:80]}")

        import json
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        if topics:
            top_topics = sorted(topics.items(), key=lambda x: -x[1])[:3]
            topic_str = ", ".join(t[0] for t in top_topics)
            parts.append(f"- Chủ đề quan tâm: {topic_str}")

        insight = ProactiveInsight(
            type="briefing",
            title="Briefing buổi sáng",
            content="\n".join(parts),
            priority=0.6,
        )

        await self._notify(insight)

        # Generate daily digest (news search in background)
        asyncio.create_task(self._generate_digest())

    async def _generate_digest(self) -> None:
        """Generate and send daily news digest based on user interests."""
        if self._digest.already_generated_today():
            return

        try:
            digest = await self._digest.generate(user_id=self._user_id)
            if not digest.items:
                log.info("digest_empty", msg="No news items found")
                return

            insight = ProactiveInsight(
                type="digest",
                title="Daily Digest",
                content=digest.to_markdown(),
                priority=0.5,
                metadata={
                    "items": len(digest.items),
                    "topics": digest.topics_searched,
                    "format": "markdown",
                },
            )
            await self._notify(insight)
        except Exception as e:
            log.error("digest_generation_error", error=str(e))

    async def generate_digest_now(self, topics: list[str] | None = None) -> str:
        """Generate digest on demand (for /digest command).

        Returns markdown-formatted digest text.
        """
        digest = await self._digest.generate(
            user_id=self._user_id,
            topics=topics,
        )
        if not digest.items:
            return "Không tìm thấy tin tức nào cho các chủ đề của bạn."
        return digest.to_markdown()

    async def _generate_idle_insight(self) -> None:
        """Generate insight during idle time."""
        # Get user's interests from model
        model = self._user_model.get_or_create(self._user_id)
        import json
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]

        if not topics:
            return

        top_topic = max(topics, key=topics.get)

        # Avoid sending same insight twice
        insight_key = f"idle_{top_topic}_{datetime.now().date()}"
        if insight_key in self._insights_sent:
            return

        insight = ProactiveInsight(
            type="insight",
            title=f"Gợi ý về {top_topic}",
            content=f"Bạn có muốn tìm hiểu thêm về {top_topic}? "
                    f"Tôi có thể tìm kiếm tin tức mới nhất hoặc phân tích xu hướng.",
            priority=0.3,
        )

        self._insights_sent.append(insight_key)
        # Keep dedup list reasonable
        if len(self._insights_sent) > 100:
            self._insights_sent = self._insights_sent[-50:]

        await self._notify(insight)

    async def generate_followup(self, last_topic: str) -> ProactiveInsight | None:
        """Generate a follow-up suggestion based on previous conversation.

        Called externally (e.g., after a conversation ends).
        """
        if not last_topic:
            return None

        # Search memory for related unfinished items
        results = await self._memory.search(last_topic, top_k=3)

        if not results:
            return None

        insight = ProactiveInsight(
            type="followup",
            title="Tiếp tục chủ đề",
            content=f"Lần trước chúng ta đang nói về '{last_topic}'. Bạn muốn tiếp tục không?",
            priority=0.4,
            metadata={"topic": last_topic},
        )

        return insight

    async def check_alerts(self) -> list[ProactiveInsight]:
        """Check for system alerts to push to user.

        Called by health monitor or other systems.
        """
        alerts = []

        # Check disk space
        import shutil
        disk = shutil.disk_usage("/")
        free_gb = disk.free / (1024 ** 3)
        if free_gb < 5:
            alerts.append(ProactiveInsight(
                type="alert",
                title="Cảnh báo dung lượng",
                content=f"Ổ đĩa còn {free_gb:.1f}GB trống. Cần dọn dẹp!",
                priority=0.9,
            ))

        return alerts

    async def _notify(self, insight: ProactiveInsight) -> None:
        """Send insight to all registered callbacks."""
        for cb in self._callbacks:
            try:
                await cb(insight)
            except Exception as e:
                log.error("proactive_notify_error", error=str(e))

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "last_briefing": str(self._last_briefing) if self._last_briefing else None,
            "insights_sent": len(self._insights_sent),
            "idle_minutes": int((time.monotonic() - self._last_interaction) / 60),
            "digest": self._digest.get_stats(),
        }
