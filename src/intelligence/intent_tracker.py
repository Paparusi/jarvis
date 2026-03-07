"""Intent Tracker — Conversation-level intent & topic awareness.

Tracks the conversation arc across messages:
- Current topic / primary intent
- Topic transitions (detect when user switches context)
- Conversation goal (e.g., "debugging", "learning", "building")
- Predicted follow-up areas

Injects conversation-level context into prompts so JARVIS
understands the bigger picture beyond individual messages.
"""

from __future__ import annotations

import re
from collections import Counter

from src.utils.logging import get_logger

log = get_logger("intelligence.intent_tracker")

# Intent categories with trigger patterns
_INTENT_PATTERNS: dict[str, list[str]] = {
    "debugging": [
        "lỗi", "error", "bug", "fix", "không chạy", "failed", "traceback",
        "exception", "crash", "broken", "sửa", "debug", "tại sao",
    ],
    "learning": [
        "giải thích", "explain", "là gì", "what is", "how does", "thế nào",
        "tại sao", "why", "học", "learn", "tutorial", "hướng dẫn", "concept",
    ],
    "building": [
        "viết code", "write code", "tạo", "create", "build", "implement",
        "thêm", "add", "new feature", "tính năng", "function", "class",
    ],
    "researching": [
        "tìm", "search", "tra cứu", "so sánh", "compare", "review",
        "best", "top", "recommend", "nên dùng", "which", "option",
    ],
    "analyzing": [
        "phân tích", "analyze", "data", "số liệu", "thống kê", "chart",
        "trend", "pattern", "dữ liệu", "report", "báo cáo",
    ],
    "planning": [
        "kế hoạch", "plan", "roadmap", "steps", "các bước", "strategy",
        "chiến lược", "timeline", "todo", "checklist", "workflow",
    ],
    "chatting": [
        "xin chào", "hello", "hi", "cảm ơn", "thanks", "bye",
        "tạm biệt", "khỏe không", "how are you",
    ],
}

# Topic categories (more granular than user_model topics)
_TOPIC_PATTERNS: dict[str, list[str]] = {
    "python": ["python", "pip", "django", "flask", "fastapi", "pytest"],
    "javascript": ["javascript", "js", "node", "react", "vue", "typescript", "npm"],
    "devops": ["docker", "kubernetes", "ci/cd", "deploy", "nginx", "server"],
    "database": ["sql", "postgres", "mongodb", "redis", "database", "query"],
    "ai_ml": ["ai", "ml", "model", "training", "llm", "neural", "gpt", "transformer"],
    "trading": ["trading", "forex", "xauusd", "crypto", "market", "giá", "chart"],
    "security": ["security", "bảo mật", "vulnerability", "ssl", "auth", "token"],
    "general": [],  # fallback
}


class ConversationTracker:
    """Track conversation intent and topic across messages."""

    def __init__(self) -> None:
        # Per-session tracking (session_key → state)
        self._sessions: dict[str, _SessionState] = {}

    def update(self, session_key: str, message: str) -> None:
        """Update conversation tracking with a new user message."""
        state = self._sessions.get(session_key)
        if state is None:
            state = _SessionState()
            self._sessions[session_key] = state

        intent = self._detect_intent(message)
        topic = self._detect_topic(message)

        # Track transitions
        if state.current_intent and intent != state.current_intent and intent != "chatting":
            state.intent_transitions.append(
                (state.current_intent, intent)
            )

        if state.current_topic and topic != state.current_topic and topic != "general":
            state.topic_transitions.append(
                (state.current_topic, topic)
            )

        # Update current state
        if intent != "chatting":
            state.current_intent = intent
        if topic != "general":
            state.current_topic = topic

        state.intent_history.append(intent)
        state.topic_history.append(topic)
        state.message_count += 1

        # Keep history bounded
        if len(state.intent_history) > 50:
            state.intent_history = state.intent_history[-30:]
            state.topic_history = state.topic_history[-30:]

    def build_context(self, session_key: str) -> str:
        """Build conversation context string for prompt injection."""
        state = self._sessions.get(session_key)
        if not state or state.message_count < 2:
            return ""

        parts = []

        # Current conversation focus
        if state.current_intent:
            intent_label = _INTENT_LABELS.get(state.current_intent, state.current_intent)
            parts.append(f"Mục tiêu hiện tại: {intent_label}")

        if state.current_topic and state.current_topic != "general":
            parts.append(f"Chủ đề: {state.current_topic}")

        # Recent topic transition
        if state.topic_transitions:
            prev, curr = state.topic_transitions[-1]
            parts.append(f"Vừa chuyển từ {prev} → {curr}")

        # Conversation pattern
        pattern = self._detect_pattern(state)
        if pattern:
            parts.append(f"Pattern: {pattern}")

        if not parts:
            return ""

        return "## Ngữ cảnh hội thoại:\n" + "\n".join(f"- {p}" for p in parts)

    def get_state(self, session_key: str) -> dict:
        """Get current tracking state for a session."""
        state = self._sessions.get(session_key)
        if not state:
            return {}
        return {
            "current_intent": state.current_intent,
            "current_topic": state.current_topic,
            "message_count": state.message_count,
            "intent_transitions": len(state.intent_transitions),
            "topic_transitions": len(state.topic_transitions),
        }

    def reset(self, session_key: str) -> None:
        """Reset tracking for a session."""
        self._sessions.pop(session_key, None)

    @staticmethod
    def _detect_intent(message: str) -> str:
        """Detect primary intent from message."""
        text_lower = message.lower()
        scores: dict[str, int] = {}
        for intent, patterns in _INTENT_PATTERNS.items():
            score = sum(1 for p in patterns if p in text_lower)
            if score > 0:
                scores[intent] = score

        if not scores:
            return "chatting"
        return max(scores, key=scores.get)

    @staticmethod
    def _detect_topic(message: str) -> str:
        """Detect topic from message."""
        text_lower = message.lower()
        scores: dict[str, int] = {}
        for topic, patterns in _TOPIC_PATTERNS.items():
            if topic == "general":
                continue
            score = sum(1 for p in patterns if p in text_lower)
            if score > 0:
                scores[topic] = score

        if not scores:
            return "general"
        return max(scores, key=scores.get)

    @staticmethod
    def _detect_pattern(state: _SessionState) -> str:
        """Detect conversation pattern from history."""
        if state.message_count < 3:
            return ""

        recent_intents = state.intent_history[-5:]
        counts = Counter(recent_intents)
        dominant = counts.most_common(1)[0]

        if dominant[1] >= 3:
            label = _INTENT_LABELS.get(dominant[0], dominant[0])
            return f"User đang tập trung {label}"

        if len(state.intent_transitions) >= 3:
            return "User đang khám phá nhiều chủ đề"

        return ""


class _SessionState:
    """Internal state for a single conversation session."""

    __slots__ = (
        "current_intent", "current_topic", "message_count",
        "intent_history", "topic_history",
        "intent_transitions", "topic_transitions",
    )

    def __init__(self) -> None:
        self.current_intent: str = ""
        self.current_topic: str = ""
        self.message_count: int = 0
        self.intent_history: list[str] = []
        self.topic_history: list[str] = []
        self.intent_transitions: list[tuple[str, str]] = []
        self.topic_transitions: list[tuple[str, str]] = []


# Human-readable labels for intents
_INTENT_LABELS = {
    "debugging": "debug/sửa lỗi",
    "learning": "học/tìm hiểu",
    "building": "xây dựng/viết code",
    "researching": "tìm kiếm/nghiên cứu",
    "analyzing": "phân tích dữ liệu",
    "planning": "lập kế hoạch",
    "chatting": "trò chuyện",
}
