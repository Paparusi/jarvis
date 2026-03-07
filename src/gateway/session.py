"""Session Manager — quản lý sessions theo channel + user.

Supports session recovery: when a session is first created, it loads
recent conversation history from episodic memory so JARVIS remembers
context across restarts.
"""

from __future__ import annotations

from src.gateway.models import Channel, SessionState
from src.memory.episodic import EpisodicMemory
from src.utils.logging import get_logger

log = get_logger("session")

# How many messages to recover from persistent storage
_RECOVERY_MESSAGES = 20


class SessionManager:
    """Manages conversation sessions, keyed by (channel, user_id)."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._episodic = EpisodicMemory()

    def _key(self, channel: Channel, user_id: str) -> str:
        return f"{channel.value}:{user_id}"

    def _session_key(self, channel: Channel, user_id: str) -> str:
        """Build the episodic memory session key (must match MemoryManager)."""
        return f"{channel.value}:{user_id}"

    def get_or_create(self, channel: Channel, user_id: str, username: str = "") -> SessionState:
        """Get existing session or create a new one.

        On first access after restart, recovers recent conversation
        history from episodic memory for continuity.
        """
        key = self._key(channel, user_id)
        if key not in self._sessions:
            session = SessionState(
                channel=channel,
                user_id=user_id,
                username=username,
            )

            # Recover conversation history from persistent storage
            ep_key = self._session_key(channel, user_id)
            recovered = self._episodic.load_history(ep_key, limit=_RECOVERY_MESSAGES)
            if recovered:
                session.messages = recovered
                log.info("session_recovered", session_id=session.session_id,
                         channel=channel.value, user_id=user_id,
                         messages_recovered=len(recovered))
            else:
                log.info("session_created", session_id=session.session_id,
                         channel=channel.value, user_id=user_id)

            self._sessions[key] = session
        return self._sessions[key]

    def get(self, channel: Channel, user_id: str) -> SessionState | None:
        return self._sessions.get(self._key(channel, user_id))

    def count(self) -> int:
        return len(self._sessions)
