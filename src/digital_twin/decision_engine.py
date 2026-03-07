"""Decision Engine — Personalize JARVIS responses using Digital Twin data.

Uses collected user model data to make runtime decisions:
1. Response style (length, formality, language mix, emoji)
2. Proactive context injection (remind user of related past topics)
3. Expertise-aware explanations (skip basics for experts, explain for beginners)
4. Time-aware behavior (greetings, urgency detection based on active hours)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.digital_twin.user_model import UserModel
from src.utils.logging import get_logger

log = get_logger("digital_twin.decision_engine")


class DecisionEngine:
    """Make personalized decisions based on Digital Twin user model."""

    def __init__(self, user_model: UserModel) -> None:
        self._user_model = user_model

    def personalize_context(self, user_id: str, user_message: str) -> str:
        """Build personalized context to inject into system prompt.

        Returns a compact directive string that guides JARVIS's response style
        based on what we've learned about this user.
        """
        model = self._user_model.get_or_create(user_id)

        if model["total_messages"] < 5:
            return ""  # Not enough data for personalization

        directives: list[str] = []

        # 1. Response length preference
        length_hint = self._decide_response_length(model, user_message)
        if length_hint:
            directives.append(length_hint)

        # 2. Formality level
        formality = self._decide_formality(model)
        if formality:
            directives.append(formality)

        # 3. Expertise-aware explanation depth
        expertise_hint = self._decide_expertise_depth(model, user_message)
        if expertise_hint:
            directives.append(expertise_hint)

        # 4. Time-aware behavior
        time_hint = self._decide_time_context(model)
        if time_hint:
            directives.append(time_hint)

        # 5. Related topics from history
        topic_hint = self._suggest_related_topics(model, user_message)
        if topic_hint:
            directives.append(topic_hint)

        if not directives:
            return ""

        return "# PERSONALIZATION (based on user profile)\n" + "\n".join(
            f"- {d}" for d in directives
        )

    def _decide_response_length(self, model: dict, message: str) -> str:
        """Decide optimal response length based on user's message patterns."""
        avg_len = model.get("avg_msg_length", 0)
        style = self._get_style(model)

        # Short messages from user → they prefer concise responses
        if avg_len < 30:
            return "User gửi tin ngắn → trả lời ngắn gọn, đi thẳng vấn đề"

        # Long messages → user likes detailed communication
        if avg_len > 150:
            return "User thường gửi tin dài, chi tiết → có thể trả lời chi tiết hơn"

        # Check if user has shown preference via feedback
        if style.get("prefers_short"):
            return "User thích câu trả lời ngắn gọn"
        if style.get("prefers_detailed"):
            return "User thích câu trả lời chi tiết, có ví dụ"

        return ""

    def _decide_formality(self, model: dict) -> str:
        """Decide formality level based on user's communication style."""
        style = self._get_style(model)
        formality = model.get("formality", "casual")

        emoji_rate = style.get("emoji_rate", 0)
        slang_count = style.get("slang_count", 0)

        if formality == "formal" or emoji_rate < 0.02:
            return "User giao tiếp formal → trả lời chuyên nghiệp, hạn chế emoji"

        if emoji_rate > 0.15 or slang_count > 5:
            return "User dùng nhiều emoji/slang → trả lời thân thiện, dùng emoji phù hợp"

        return ""

    def _decide_expertise_depth(self, model: dict, message: str) -> str:
        """Decide explanation depth based on user's expertise."""
        expertise = self._get_json(model, "expertise")
        topics = self._get_json(model, "topics")
        msg_lower = message.lower()

        if not expertise and not topics:
            return ""

        # Check if message relates to a topic user is expert in
        for topic, level in expertise.items():
            if topic.lower() in msg_lower:
                if level in ("expert", "advanced"):
                    return f"User có expertise '{topic}' ở mức {level} → bỏ qua giải thích cơ bản, đi sâu vào chi tiết kỹ thuật"
                elif level == "beginner":
                    return f"User mới với '{topic}' → giải thích rõ ràng, có ví dụ cụ thể"

        # Check frequently discussed topics
        high_freq_topics = [t for t, c in topics.items() if c >= 5]
        matching = [t for t in high_freq_topics if t.lower() in msg_lower]
        if matching:
            return f"User thường xuyên hỏi về {', '.join(matching)} → có thể reference context trước đó"

        return ""

    def _decide_time_context(self, model: dict) -> str:
        """Add time-aware behavior based on user's activity patterns."""
        active_hours = self._get_json(model, "active_hours")
        if not active_hours:
            return ""

        now = datetime.now(timezone.utc)
        current_hour = str(now.hour)

        # Find user's peak hours
        if active_hours:
            peak_hour = max(active_hours, key=lambda h: active_hours[h])
            peak_count = active_hours[peak_hour]

            current_count = active_hours.get(current_hour, 0)

            # User is messaging outside their normal hours → might be urgent
            if current_count == 0 and peak_count > 3:
                return "User đang nhắn tin ngoài giờ hoạt động bình thường → có thể urgent"

        # Late night / early morning
        if now.hour >= 22 or now.hour < 6:
            return "Đêm khuya → trả lời ngắn gọn, hỏi nếu cần hỗ trợ gấp"

        return ""

    def _suggest_related_topics(self, model: dict, message: str) -> str:
        """Suggest related topics from user's history."""
        topics = self._get_json(model, "topics")
        if not topics or len(topics) < 3:
            return ""

        msg_lower = message.lower()

        # Find topics related to current message that user has discussed before
        # but aren't directly mentioned
        mentioned = {t for t in topics if t.lower() in msg_lower}
        if not mentioned:
            return ""

        # Find co-occurring topics (topics user discussed in same sessions)
        related = []
        for topic in topics:
            if topic not in mentioned and topics[topic] >= 3:
                # Topics with high count that aren't in current message
                for m in mentioned:
                    # Simple co-occurrence heuristic
                    if abs(topics.get(topic, 0) - topics.get(m, 0)) < 5:
                        related.append(topic)
                        break

        if related:
            return f"User cũng quan tâm đến: {', '.join(related[:3])} (có thể liên quan)"

        return ""

    @staticmethod
    def _get_style(model: dict) -> dict:
        style = model.get("communication_style", "{}")
        if isinstance(style, str):
            try:
                return json.loads(style)
            except (json.JSONDecodeError, TypeError):
                return {}
        return style or {}

    @staticmethod
    def _get_json(model: dict, key: str) -> dict:
        val = model.get(key, "{}")
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return {}
        return val or {}
