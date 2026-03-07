"""Digital Twin — User Model Store.

Tự động học và xây dựng mô hình người dùng từ conversations:
1. Communication style (language, formality, emoji usage, msg length)
2. Expertise areas (topics user frequently discusses)
3. Interaction patterns (active hours, frequency)
4. Preferences (stated and inferred)

User Model tự tiến hóa — không cần user cấu hình thủ công.
Dữ liệu được tóm tắt và inject vào system prompt.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone

from pathlib import Path

from src.memory.store import get_connection
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("digital_twin.user_model")


class UserModel:
    """Self-evolving user model for personalization."""

    def __init__(self) -> None:
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_model (
                user_id TEXT PRIMARY KEY,
                display_name TEXT DEFAULT '',
                language TEXT DEFAULT 'vi',
                formality TEXT DEFAULT 'casual',
                avg_msg_length REAL DEFAULT 0,
                total_messages INTEGER DEFAULT 0,
                topics TEXT DEFAULT '{}',
                expertise TEXT DEFAULT '{}',
                active_hours TEXT DEFAULT '{}',
                preferences TEXT DEFAULT '{}',
                communication_style TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    def get_or_create(self, user_id: str, display_name: str = "") -> dict:
        """Get or create user model."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM user_model WHERE user_id = ?", (user_id,)
        ).fetchone()

        if row:
            return self._row_to_dict(row)

        conn.execute(
            "INSERT INTO user_model (user_id, display_name) VALUES (?, ?)",
            (user_id, display_name),
        )
        conn.commit()
        log.info("user_model_created", user_id=user_id, name=display_name)
        return self.get_or_create(user_id, display_name)

    def update_from_message(self, user_id: str, message: str) -> None:
        """Update user model based on a new user message.

        Called on every message — lightweight analysis, incremental update.
        """
        model = self.get_or_create(user_id)
        conn = get_connection()

        # 1. Update message stats
        total = model["total_messages"] + 1
        avg_len = (model["avg_msg_length"] * model["total_messages"] + len(message)) / total

        # 2. Detect language
        language = self._detect_language(message)

        # 3. Update topic counts
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        detected_topics = self._extract_topics(message)
        for topic in detected_topics:
            topics[topic] = topics.get(topic, 0) + 1

        # 4. Update active hours
        active_hours = json.loads(model["active_hours"]) if isinstance(model["active_hours"], str) else model["active_hours"]
        hour = str(datetime.now(timezone.utc).hour)
        active_hours[hour] = active_hours.get(hour, 0) + 1

        # 5. Update communication style
        style = json.loads(model["communication_style"]) if isinstance(model["communication_style"], str) else model["communication_style"]
        style = self._analyze_style(message, style, total)

        # 6. Persist
        conn.execute(
            """UPDATE user_model SET
                total_messages = ?,
                avg_msg_length = ?,
                language = ?,
                topics = ?,
                active_hours = ?,
                communication_style = ?,
                updated_at = datetime('now')
            WHERE user_id = ?""",
            (
                total,
                round(avg_len, 1),
                language,
                json.dumps(topics, ensure_ascii=False),
                json.dumps(active_hours),
                json.dumps(style, ensure_ascii=False),
                user_id,
            ),
        )
        conn.commit()

    def build_context(self, user_id: str) -> str:
        """Build a context string about the user for prompt injection.

        Returns compact summary for PromptAssembler.
        Includes adaptive response style hints.
        """
        model = self.get_or_create(user_id)

        if model["total_messages"] < 3:
            return ""  # Not enough data yet

        parts = []

        # Name
        if model["display_name"]:
            parts.append(f"User tên: {model['display_name']}")

        # Communication style → adaptive response hints
        style = json.loads(model["communication_style"]) if isinstance(model["communication_style"], str) else model["communication_style"]
        style_hints = self._build_style_hints(style, model)
        if style_hints:
            parts.append(f"Phong cách trả lời phù hợp: {style_hints}")

        # Top topics
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        if topics:
            sorted_topics = sorted(topics.items(), key=lambda x: -x[1])[:5]
            topic_list = ", ".join(t[0] for t in sorted_topics)
            parts.append(f"Chủ đề thường hỏi: {topic_list}")

        # Expertise (adapt explanation depth)
        expertise = json.loads(model["expertise"]) if isinstance(model["expertise"], str) else model["expertise"]
        if expertise:
            exp_items = [f"{k} ({v})" for k, v in list(expertise.items())[:5]]
            parts.append(f"Kinh nghiệm: {', '.join(exp_items)}")

        # Preferences
        prefs = json.loads(model["preferences"]) if isinstance(model["preferences"], str) else model["preferences"]
        if prefs:
            pref_items = [f"{k}: {v}" for k, v in list(prefs.items())[:5]]
            parts.append("Sở thích: " + ", ".join(pref_items))

        if not parts:
            return ""

        return "## Thông tin User:\n" + "\n".join(f"- {p}" for p in parts)

    @staticmethod
    def _build_style_hints(style: dict, model: dict) -> str:
        """Build adaptive response style hints from learned patterns."""
        hints = []

        if style.get("uses_emoji"):
            hints.append("dùng emoji")
        if style.get("prefers_short"):
            hints.append("ngắn gọn, đi thẳng vấn đề")
        elif style.get("prefers_detailed"):
            hints.append("chi tiết, giải thích rõ ràng")

        # Infer code preference from topics
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        code_topics = sum(
            topics.get(t, 0) for t in ["programming", "web", "ai_ml", "devops"]
        )
        total = model.get("total_messages", 0)
        if total > 5 and code_topics > total * 0.3:
            hints.append("thường cần code examples")

        # Language preference
        lang = model.get("language", "vi")
        if lang == "vi":
            hints.append("trả lời tiếng Việt")

        return ", ".join(hints) if hints else ""

    def add_preference(self, user_id: str, key: str, value: str) -> None:
        """Explicitly add a user preference."""
        model = self.get_or_create(user_id)
        conn = get_connection()
        prefs = json.loads(model["preferences"]) if isinstance(model["preferences"], str) else model["preferences"]
        prefs[key] = value
        conn.execute(
            "UPDATE user_model SET preferences = ?, updated_at = datetime('now') WHERE user_id = ?",
            (json.dumps(prefs, ensure_ascii=False), user_id),
        )
        conn.commit()

    def add_expertise(self, user_id: str, topic: str, level: str = "familiar") -> None:
        """Track user expertise in a topic."""
        model = self.get_or_create(user_id)
        conn = get_connection()
        expertise = json.loads(model["expertise"]) if isinstance(model["expertise"], str) else model["expertise"]
        expertise[topic] = level
        conn.execute(
            "UPDATE user_model SET expertise = ?, updated_at = datetime('now') WHERE user_id = ?",
            (json.dumps(expertise, ensure_ascii=False), user_id),
        )
        conn.commit()

    def update_from_feedback(
        self, user_id: str, response: str, rating: str,
    ) -> None:
        """Update style preferences based on feedback on a response."""
        model = self.get_or_create(user_id)
        conn = get_connection()
        style = json.loads(model["communication_style"]) if isinstance(model["communication_style"], str) else model["communication_style"]

        is_positive = rating == "positive"
        resp_len = len(response)

        # Track format preferences from feedback
        has_bullets = "- " in response or "• " in response
        has_code = "```" in response
        is_short = resp_len < 200
        is_long = resp_len > 800

        if is_positive:
            if has_bullets:
                style["liked_bullets"] = style.get("liked_bullets", 0) + 1
            if has_code:
                style["liked_code"] = style.get("liked_code", 0) + 1
            if is_short:
                style["liked_short"] = style.get("liked_short", 0) + 1
            if is_long:
                style["liked_long"] = style.get("liked_long", 0) + 1
        else:
            if is_short:
                style["disliked_short"] = style.get("disliked_short", 0) + 1
            if is_long:
                style["disliked_long"] = style.get("disliked_long", 0) + 1

        conn.execute(
            "UPDATE user_model SET communication_style = ?, updated_at = datetime('now') WHERE user_id = ?",
            (json.dumps(style, ensure_ascii=False), user_id),
        )
        conn.commit()

    def get_stats(self, user_id: str) -> dict:
        """Get user model stats for /status command."""
        model = self.get_or_create(user_id)
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        return {
            "total_messages": model["total_messages"],
            "avg_msg_length": model["avg_msg_length"],
            "language": model["language"],
            "topics_tracked": len(topics),
        }

    def export_user_md(self, user_id: str) -> Path:
        """Export user model to USER.md for the .md-based identity system.

        Called during Dreamtime to keep USER.md in sync with learned data.
        Returns path to the generated file.
        """
        model = self.get_or_create(user_id)
        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        expertise = json.loads(model["expertise"]) if isinstance(model["expertise"], str) else model["expertise"]
        prefs = json.loads(model["preferences"]) if isinstance(model["preferences"], str) else model["preferences"]
        style = json.loads(model["communication_style"]) if isinstance(model["communication_style"], str) else model["communication_style"]
        active_hours = json.loads(model["active_hours"]) if isinstance(model["active_hours"], str) else model["active_hours"]

        name = model.get("display_name", "User") or "User"
        lang = model.get("language", "vi")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        sections = [
            f"# USER — Digital Twin Profile\n",
            f"> Auto-generated by JARVIS Digital Twin Engine.",
            f"> Last updated: {now}\n",
            "---\n",
            "## Identity\n",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Name** | {name} |",
            f"| **Language** | {'Vietnamese' if lang == 'vi' else 'English'} (primary) |",
            f"| **Total interactions** | {model.get('total_messages', 0)} |",
            f"| **Avg message length** | {model.get('avg_msg_length', 0):.0f} chars |",
            "",
        ]

        # Communication style
        sections.append("---\n\n## Communication Style\n")
        style_items = []
        if style.get("uses_emoji"):
            style_items.append("- Uses emoji regularly")
        if style.get("prefers_short"):
            style_items.append("- Prefers short, concise responses")
        elif style.get("prefers_detailed"):
            style_items.append("- Prefers detailed explanations")
        if lang == "vi":
            style_items.append("- Primary language: Vietnamese")
        if style_items:
            sections.extend(style_items)
        else:
            sections.append("- Still learning communication preferences...")
        sections.append("")

        # Topics
        if topics:
            sections.append("---\n\n## Frequent Topics\n")
            sorted_topics = sorted(topics.items(), key=lambda x: -x[1])[:10]
            for topic, count in sorted_topics:
                sections.append(f"- **{topic}**: {count} mentions")
            sections.append("")

        # Expertise
        if expertise:
            sections.append("---\n\n## Expertise Areas\n")
            for area, level in sorted(expertise.items()):
                sections.append(f"- **{area}**: {level}")
            sections.append("")

        # Preferences
        if prefs:
            sections.append("---\n\n## Stated Preferences\n")
            for key, value in sorted(prefs.items()):
                sections.append(f"- **{key}**: {value}")
            sections.append("")

        # Active hours
        if active_hours:
            sections.append("---\n\n## Activity Pattern\n")
            sorted_hours = sorted(active_hours.items(), key=lambda x: -x[1])[:5]
            peak_hours = ", ".join(f"{h}:00 UTC" for h, _ in sorted_hours)
            sections.append(f"- Peak hours: {peak_hours}")
            sections.append("")

        sections.extend([
            "---\n",
            "*This profile is auto-updated by the Digital Twin Engine based on interaction patterns.*",
        ])

        md_path = get_project_root() / "workspace" / "USER.md"
        md_path.write_text("\n".join(sections), encoding="utf-8")
        log.info("user_md_exported", user_id=user_id, path=str(md_path))
        return md_path

    # --- Internal Analysis Methods ---

    @staticmethod
    def _detect_language(text: str) -> str:
        """Simple language detection: vi or en."""
        vi_chars = re.compile(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
            re.I,
        )
        return "vi" if vi_chars.search(text) else "en"

    @staticmethod
    def _extract_topics(text: str) -> list[str]:
        """Extract topic keywords from message."""
        text_lower = text.lower()
        # Topic → keyword patterns
        topic_patterns = {
            "programming": ["code", "python", "javascript", "lập trình", "bug", "debug", "api", "git", "docker"],
            "ai/ml": ["ai", "machine learning", "deep learning", "model", "training", "neural", "llm"],
            "trading": ["trading", "forex", "xauusd", "crypto", "bitcoin", "thị trường", "giá"],
            "security": ["security", "bảo mật", "hack", "vulnerability", "ssl", "firewall"],
            "system": ["server", "linux", "deploy", "devops", "cloud", "aws", "database"],
            "web": ["web", "html", "css", "react", "frontend", "backend", "api"],
            "data": ["data", "csv", "json", "phân tích", "analyze", "chart", "dashboard"],
            "writing": ["viết", "email", "báo cáo", "report", "document", "tóm tắt"],
        }

        detected = []
        for topic, keywords in topic_patterns.items():
            if any(kw in text_lower for kw in keywords):
                detected.append(topic)

        return detected

    @staticmethod
    def _analyze_style(message: str, current_style: dict, total_msgs: int) -> dict:
        """Analyze communication style incrementally."""
        # Emoji usage
        has_emoji = bool(re.search(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF]", message))
        emoji_count = current_style.get("emoji_messages", 0) + (1 if has_emoji else 0)
        current_style["emoji_messages"] = emoji_count
        current_style["uses_emoji"] = emoji_count > total_msgs * 0.3

        # Message length preference
        msg_len = len(message)
        if msg_len < 20:
            current_style["short_messages"] = current_style.get("short_messages", 0) + 1
        elif msg_len > 100:
            current_style["long_messages"] = current_style.get("long_messages", 0) + 1

        short_ratio = current_style.get("short_messages", 0) / max(total_msgs, 1)
        long_ratio = current_style.get("long_messages", 0) / max(total_msgs, 1)
        current_style["prefers_short"] = short_ratio > 0.6
        current_style["prefers_detailed"] = long_ratio > 0.4

        return current_style

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {key: row[key] for key in row.keys()}
