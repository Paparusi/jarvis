"""Core data models for JARVIS Gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid4())


class Channel(str, Enum):
    CLI = "cli"
    TELEGRAM = "telegram"
    WEB = "web"
    WHATSAPP = "whatsapp"


class MessageEnvelope(BaseModel):
    """Unified message format — mọi channel adapter đều convert thành format này."""

    id: str = Field(default_factory=_uuid)
    channel: Channel
    session_id: str = Field(default_factory=_uuid)
    user_id: str = ""
    username: str = ""

    # Content
    content: str
    attachments: list[str] = Field(default_factory=list)

    # Metadata
    timestamp: datetime = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)

    # Reply context (for Telegram reply-to, etc.)
    reply_to_message_id: str | None = None


class AgentResponse(BaseModel):
    """Response from the intelligence layer."""

    id: str = Field(default_factory=_uuid)
    request_id: str  # links to MessageEnvelope.id
    session_id: str

    # Content
    content: str
    model_used: str = ""
    reasoning_trace: str | None = None

    # Metrics
    confidence_score: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0

    timestamp: datetime = Field(default_factory=_now)


class SessionState(BaseModel):
    """Tracks conversation state for a session."""

    session_id: str = Field(default_factory=_uuid)
    channel: Channel
    user_id: str = ""
    username: str = ""

    # Conversation history (working memory)
    messages: list[dict] = Field(default_factory=list)
    max_history: int = 50

    created_at: datetime = Field(default_factory=_now)
    last_active: datetime = Field(default_factory=_now)

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self._trim()
        self.last_active = _now()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self._trim()
        self.last_active = _now()

    def get_history(self) -> list[dict]:
        return list(self.messages)

    def _trim(self) -> None:
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]
