"""Inter-Agent Message Bus — Live communication between swarm agents.

Agents can publish findings mid-execution and query shared knowledge.
This avoids duplicate work (e.g., two research agents fetching the same URL)
and enables live collaboration between parallel agents.

Design: async message queue backed by in-memory dict, scoped per swarm run.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.utils.logging import get_logger

log = get_logger("swarm.message_bus")


@dataclass
class AgentMessage:
    """A message published by an agent to the bus."""

    agent_id: str
    topic: str  # e.g. "finding", "url_fetched", "error", "question"
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class SwarmMessageBus:
    """Shared message bus for agents within a single swarm execution.

    Features:
    - Topic-based pub/sub (agents subscribe to topics of interest)
    - Shared knowledge store (agents can read findings from peers)
    - URL dedup (prevents multiple agents fetching same URL)
    - Scoped per swarm run (create new bus per execute() call)
    """

    def __init__(self) -> None:
        self._messages: list[AgentMessage] = []
        self._by_topic: dict[str, list[AgentMessage]] = defaultdict(list)
        self._shared_knowledge: dict[str, str] = {}  # key → value
        self._fetched_urls: set[str] = set()
        self._listeners: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def publish(self, agent_id: str, topic: str, content: str, **data: Any) -> None:
        """Publish a message to the bus (non-blocking)."""
        msg = AgentMessage(
            agent_id=agent_id,
            topic=topic,
            content=content,
            data=data,
        )
        self._messages.append(msg)
        self._by_topic[topic].append(msg)

        # Notify waiting listeners
        for queue in self._listeners.get(topic, []):
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # Drop if listener is full

    def share_knowledge(self, key: str, value: str) -> None:
        """Store a piece of shared knowledge accessible to all agents."""
        self._shared_knowledge[key] = value

    def get_knowledge(self, key: str) -> str | None:
        """Retrieve shared knowledge by key."""
        return self._shared_knowledge.get(key)

    def get_all_knowledge(self) -> dict[str, str]:
        """Get all shared knowledge entries."""
        return dict(self._shared_knowledge)

    def mark_url_fetched(self, url: str, content: str = "") -> None:
        """Mark a URL as fetched to prevent duplicate fetches."""
        self._fetched_urls.add(url)
        if content:
            self.share_knowledge(f"url:{url}", content[:4000])

    def is_url_fetched(self, url: str) -> bool:
        """Check if a URL has already been fetched by another agent."""
        return url in self._fetched_urls

    def get_messages(
        self,
        topic: str | None = None,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentMessage]:
        """Get messages, optionally filtered by topic or agent."""
        msgs = self._messages
        if topic:
            msgs = self._by_topic.get(topic, [])
        if agent_id:
            msgs = [m for m in msgs if m.agent_id == agent_id]
        return msgs[-limit:]

    def get_findings(self, exclude_agent: str | None = None) -> list[str]:
        """Get all 'finding' messages from other agents."""
        findings = self._by_topic.get("finding", [])
        if exclude_agent:
            findings = [f for f in findings if f.agent_id != exclude_agent]
        return [f.content for f in findings]

    def get_stats(self) -> dict:
        """Get bus statistics."""
        return {
            "total_messages": len(self._messages),
            "topics": {k: len(v) for k, v in self._by_topic.items()},
            "shared_knowledge_entries": len(self._shared_knowledge),
            "urls_fetched": len(self._fetched_urls),
        }
