"""Tests for Digital Twin — User Model."""

import json
import pytest

from src.digital_twin.user_model import UserModel


class TestUserModel:
    def setup_method(self):
        self.model = UserModel()
        self.test_user = "test_user_dt_001"
        # Clean up test user if exists
        from src.memory.store import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM user_model WHERE user_id = ?", (self.test_user,))
        conn.commit()

    def teardown_method(self):
        from src.memory.store import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM user_model WHERE user_id = ?", (self.test_user,))
        conn.commit()

    def test_get_or_create_new(self):
        result = self.model.get_or_create(self.test_user, "Test User")
        assert result["user_id"] == self.test_user
        assert result["display_name"] == "Test User"
        assert result["total_messages"] == 0

    def test_get_or_create_existing(self):
        self.model.get_or_create(self.test_user, "Test User")
        result = self.model.get_or_create(self.test_user, "Different Name")
        assert result["display_name"] == "Test User"  # Original name kept

    def test_update_from_message(self):
        self.model.get_or_create(self.test_user)
        self.model.update_from_message(self.test_user, "Hello, how are you?")
        result = self.model.get_or_create(self.test_user)
        assert result["total_messages"] == 1
        assert result["avg_msg_length"] > 0

    def test_update_multiple_messages(self):
        self.model.get_or_create(self.test_user)
        for msg in ["Hi", "How are you?", "Tell me about Python programming"]:
            self.model.update_from_message(self.test_user, msg)
        result = self.model.get_or_create(self.test_user)
        assert result["total_messages"] == 3

    def test_topic_detection(self):
        self.model.get_or_create(self.test_user)
        self.model.update_from_message(self.test_user, "Help me write Python code for API")
        result = self.model.get_or_create(self.test_user)
        topics = json.loads(result["topics"]) if isinstance(result["topics"], str) else result["topics"]
        assert "programming" in topics
        assert "web" in topics  # "API" matches web topic

    def test_language_detection_vi(self):
        self.model.get_or_create(self.test_user)
        self.model.update_from_message(self.test_user, "Xin chào, tôi là Bi")
        result = self.model.get_or_create(self.test_user)
        assert result["language"] == "vi"

    def test_language_detection_en(self):
        self.model.get_or_create(self.test_user)
        self.model.update_from_message(self.test_user, "Hello, how are you")
        result = self.model.get_or_create(self.test_user)
        assert result["language"] == "en"

    def test_build_context_insufficient_data(self):
        self.model.get_or_create(self.test_user)
        # Less than 3 messages → no context
        self.model.update_from_message(self.test_user, "Hello")
        context = self.model.build_context(self.test_user)
        assert context == ""

    def test_build_context_with_data(self):
        self.model.get_or_create(self.test_user, "Bi")
        for msg in [
            "Help me with Python code",
            "Debug this function",
            "Write unit tests for API",
            "Deploy Docker container",
        ]:
            self.model.update_from_message(self.test_user, msg)
        context = self.model.build_context(self.test_user)
        assert "Bi" in context
        assert "Thông tin User" in context

    def test_add_preference(self):
        self.model.get_or_create(self.test_user)
        self.model.add_preference(self.test_user, "editor", "vim")
        result = self.model.get_or_create(self.test_user)
        prefs = json.loads(result["preferences"]) if isinstance(result["preferences"], str) else result["preferences"]
        assert prefs["editor"] == "vim"

    def test_add_expertise(self):
        self.model.get_or_create(self.test_user)
        self.model.add_expertise(self.test_user, "python", "expert")
        result = self.model.get_or_create(self.test_user)
        expertise = json.loads(result["expertise"]) if isinstance(result["expertise"], str) else result["expertise"]
        assert expertise["python"] == "expert"

    def test_get_stats(self):
        self.model.get_or_create(self.test_user)
        self.model.update_from_message(self.test_user, "Test message about AI")
        stats = self.model.get_stats(self.test_user)
        assert stats["total_messages"] == 1
        assert "language" in stats
        assert "topics_tracked" in stats

    def test_communication_style_short(self):
        """Test detection of short message preference."""
        self.model.get_or_create(self.test_user)
        for _ in range(10):
            self.model.update_from_message(self.test_user, "ok")
        result = self.model.get_or_create(self.test_user)
        style = json.loads(result["communication_style"]) if isinstance(result["communication_style"], str) else result["communication_style"]
        assert style.get("prefers_short") is True

    def test_extract_topics_static(self):
        topics = UserModel._extract_topics("Help me debug my Python code")
        assert "programming" in topics

    def test_detect_language_static(self):
        assert UserModel._detect_language("Xin chào") == "vi"
        assert UserModel._detect_language("Hello world") == "en"
