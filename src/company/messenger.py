"""Company Messenger — High-level messaging API for JARVIS agent-to-agent communication.

This is the single coordination point that CEO, DepartmentHead, and Worker all use
for delegation, escalation, collaboration, meetings, and general messaging.

Usage:
    messenger = get_messenger()
    await messenger.send("ceo", "ceo", "dept.finance", "department", "directive", "Run Q4 report")
    await messenger.delegate("ceo", "dept.finance", "Prepare quarterly earnings analysis")
    await messenger.escalate("finance.trader", "Position risk exceeds threshold")
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from src.company.message_store import MessageStore, MeetingStore
from src.gateway.event_bus import get_event_bus
from src.utils.logging import get_logger

log = get_logger("company.messenger")

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_messenger: CompanyMessenger | None = None


def get_messenger() -> CompanyMessenger:
    """Get the global CompanyMessenger singleton."""
    global _messenger
    if _messenger is None:
        _messenger = CompanyMessenger()
    return _messenger


# ---------------------------------------------------------------------------
# CompanyMessenger
# ---------------------------------------------------------------------------


class CompanyMessenger:
    """Unified messaging layer for all JARVIS Company agent communication."""

    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._message_store = MessageStore()
        self._meeting_store = MeetingStore()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(self, agent_id: str, agent_type: str, instance: Any) -> None:
        """Register an agent so it can be looked up by id."""
        self._agents[agent_id] = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "instance": instance,
        }
        log.info("agent_registered", agent_id=agent_id, agent_type=agent_type)

    def get_agent(self, agent_id: str) -> Any | None:
        """Look up a registered agent instance by id."""
        entry = self._agents.get(agent_id)
        return entry["instance"] if entry else None

    # ------------------------------------------------------------------
    # Core Messaging
    # ------------------------------------------------------------------

    async def send(
        self,
        sender_id: str,
        sender_type: str,
        recipient_id: str,
        recipient_type: str,
        message_type: str,
        content: str,
        thread_id: str | None = None,
        subject: str = "",
        context: dict[str, Any] | None = None,
        priority: int = 5,
    ) -> int:
        """Send a message between agents.

        If *thread_id* is ``None`` a new thread is generated automatically.
        Returns the persisted message id.
        """
        if thread_id is None:
            thread_id = f"thr_{uuid4().hex[:12]}"

        message_id = self._message_store.send_message(
            thread_id=thread_id,
            sender_id=sender_id,
            sender_type=sender_type,
            recipient_id=recipient_id,
            recipient_type=recipient_type,
            message_type=message_type,
            content=content,
            subject=subject,
            context=context,
            priority=priority,
        )

        self._fire_event(
            "company_msg_sent",
            {
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "message_type": message_type,
                "subject": subject,
                "content_preview": content[:100],
                "thread_id": thread_id,
            },
        )

        return message_id

    # ------------------------------------------------------------------
    # Delegation  (CEO -> Dept -> Worker)
    # ------------------------------------------------------------------

    async def delegate(
        self,
        from_id: str,
        to_id: str,
        instruction: str,
        reasoning_chain: str = "",
        thread_id: str | None = None,
    ) -> str:
        """Delegate a task from one agent to another.

        Returns the *thread_id* used (created if not provided).
        """
        if thread_id is None:
            thread_id = f"thr_{uuid4().hex[:12]}"

        await self.send(
            sender_id=from_id,
            sender_type=self._infer_type(from_id),
            recipient_id=to_id,
            recipient_type=self._infer_type(to_id),
            message_type="delegation",
            content=instruction,
            thread_id=thread_id,
            context={"reasoning": reasoning_chain},
        )

        return thread_id

    # ------------------------------------------------------------------
    # Escalation  (Worker -> DeptHead / CEO)
    # ------------------------------------------------------------------

    async def escalate(
        self,
        worker_id: str,
        reason: str,
        task_context: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> int:
        """Escalate an issue from a worker to its department head.

        The recipient is derived from the worker_id:
        ``"finance.trader"`` -> ``"dept.finance"``.

        Returns the message id.
        """
        # Derive department from worker_id  (e.g. "finance.trader" -> "dept.finance")
        department = worker_id.split(".")[0] if "." in worker_id else worker_id
        recipient_id = f"dept.{department}"

        message_id = await self.send(
            sender_id=worker_id,
            sender_type="worker",
            recipient_id=recipient_id,
            recipient_type="department",
            message_type="escalation",
            content=reason,
            thread_id=thread_id,
            priority=2,
            context=task_context,
        )

        self._fire_event(
            "company_msg_escalation",
            {
                "worker_id": worker_id,
                "recipient_id": recipient_id,
                "reason_preview": reason[:100],
            },
        )

        return message_id

    # ------------------------------------------------------------------
    # Cross-Department Collaboration
    # ------------------------------------------------------------------

    async def request_collaboration(
        self,
        from_dept: str,
        to_dept: str,
        request: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Request cross-department collaboration.

        Creates a collaboration thread, sends a message, and creates a task
        in the target department's task queue.

        Returns the *thread_id*.
        """
        thread_id = f"collab_{uuid4().hex[:12]}"

        await self.send(
            sender_id=f"dept.{from_dept}",
            sender_type="department",
            recipient_id=f"dept.{to_dept}",
            recipient_type="department",
            message_type="collaboration_request",
            content=request,
            thread_id=thread_id,
            context=context,
        )

        # Create a task in the target department's queue
        from src.company.worker_store import TaskStore

        task_store = TaskStore()
        task_store.create_task(
            department=to_dept,
            instruction=f"Cross-department request from {from_dept}:\n\n{request}",
            priority=3,
            context={
                "source": "collaboration",
                "from_dept": from_dept,
                "thread_id": thread_id,
                **(context or {}),
            },
        )

        self._fire_event(
            "company_msg_collaboration",
            {
                "from_dept": from_dept,
                "to_dept": to_dept,
                "thread_id": thread_id,
                "request_preview": request[:100],
            },
        )

        return thread_id

    # ------------------------------------------------------------------
    # Meetings
    # ------------------------------------------------------------------

    async def call_meeting(
        self,
        initiator_id: str,
        participant_ids: list[str],
        topic: str,
        max_rounds: int = 3,
    ) -> str:
        """Initiate a multi-agent meeting.

        Creates the meeting record, sends an opening message from the
        initiator, and publishes a ``company_meeting_started`` event.

        Returns the *thread_id*.
        """
        thread_id = f"meeting_{uuid4().hex[:12]}"

        self._meeting_store.create_meeting(
            thread_id=thread_id,
            topic=topic,
            initiated_by=initiator_id,
            participants=participant_ids,
            max_rounds=max_rounds,
        )

        # Opening statement from the initiator
        await self.send(
            sender_id=initiator_id,
            sender_type=self._infer_type(initiator_id),
            recipient_id=thread_id,
            recipient_type="meeting",
            message_type="meeting_turn",
            content=f"Meeting called: {topic}",
            thread_id=thread_id,
        )

        self._fire_event(
            "company_meeting_started",
            {
                "topic": topic,
                "participants": participant_ids,
                "thread_id": thread_id,
                "initiated_by": initiator_id,
            },
        )

        return thread_id

    async def run_meeting_round(self, thread_id: str) -> list[dict]:
        """Prepare prompts for the next round of a meeting.

        This method does **not** call any LLM. It builds a list of
        ``{participant_id, prompt, shared_context}`` dicts so the caller
        can invoke each agent's LLM independently.

        Returns the list of meeting turn prompts.
        """
        meeting = self._meeting_store.get_meeting(thread_id)
        if meeting is None:
            log.warning("run_meeting_round_no_meeting", thread_id=thread_id)
            return []

        topic = meeting["topic"]
        participants = meeting["participants"]

        # Gather all messages in the thread to build shared context
        thread_messages = self._message_store.get_thread(thread_id)
        context_parts: list[str] = []
        for msg in thread_messages:
            context_parts.append(f"[{msg['sender_id']}]: {msg['content']}")
        shared_context = "\n\n".join(context_parts)

        # Build prompt for each participant
        prompts: list[dict] = []
        for participant_id in participants:
            prompt = (
                f"Meeting topic: {topic}\n\n"
                f"Previous discussion:\n{shared_context}\n\n"
                f"Your turn. Provide your department's perspective."
            )
            prompts.append(
                {
                    "participant_id": participant_id,
                    "prompt": prompt,
                    "shared_context": shared_context,
                }
            )

        # Advance the round counter
        self._meeting_store.add_round(thread_id)

        return prompts

    async def record_meeting_turn(
        self,
        thread_id: str,
        speaker_id: str,
        content: str,
    ) -> int:
        """Record a single speaker's contribution to a meeting.

        Returns the message id.
        """
        message_id = await self.send(
            sender_id=speaker_id,
            sender_type=self._infer_type(speaker_id),
            recipient_id=thread_id,
            recipient_type="meeting",
            message_type="meeting_turn",
            content=content,
            thread_id=thread_id,
        )

        self._fire_event(
            "company_meeting_turn",
            {
                "thread_id": thread_id,
                "speaker_id": speaker_id,
                "content_preview": content[:100],
            },
        )

        return message_id

    async def conclude_meeting(self, thread_id: str, summary: str) -> None:
        """Conclude a meeting with a summary."""
        self._meeting_store.conclude_meeting(thread_id, summary)

        self._fire_event(
            "company_meeting_concluded",
            {
                "thread_id": thread_id,
                "summary_preview": summary[:200],
            },
        )

    # ------------------------------------------------------------------
    # Inbox / Thread Access
    # ------------------------------------------------------------------

    def get_inbox(self, agent_id: str, unread_only: bool = True) -> list[dict]:
        """Get an agent's inbox messages."""
        return self._message_store.get_inbox(agent_id, unread_only=unread_only)

    def get_thread(self, thread_id: str) -> list[dict]:
        """Get all messages in a thread."""
        return self._message_store.get_thread(thread_id)

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        """Get recent messages across all agents."""
        return self._message_store.get_recent(limit=limit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_type(agent_id: str) -> str:
        """Best-effort inference of agent type from its id."""
        if agent_id == "ceo":
            return "ceo"
        if agent_id.startswith("dept."):
            return "department"
        # Anything with a dot is assumed to be a worker  (e.g. "finance.trader")
        if "." in agent_id:
            return "worker"
        return "unknown"

    @staticmethod
    def _fire_event(event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to the EventBus using fire-and-forget."""
        try:
            bus = get_event_bus()
            asyncio.create_task(bus.publish(event_type, data, source="company.messenger"))
        except Exception:
            pass
