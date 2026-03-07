"""Tests for UserModel — Digital Twin adaptive style."""

import json
import uuid

import pytest

from src.digital_twin.user_model import UserModel


@pytest.fixture
def model():
    return UserModel()


def _uid():
    """Generate unique user ID per test."""
    return f"test-{uuid.uuid4().hex[:8]}"


class TestUserModelBasic:
    def test_create_user(self, model):
        uid = _uid()
        result = model.get_or_create(uid, "TestUser")
        assert result["user_id"] == uid
        assert result["display_name"] == "TestUser"
        assert result["total_messages"] == 0

    def test_update_from_message(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        model.update_from_message(uid, "hello how are you")
        result = model.get_or_create(uid)
        assert result["total_messages"] == 1
        assert result["avg_msg_length"] > 0


class TestLanguageDetection:
    def test_detect_vietnamese(self, model):
        assert model._detect_language("xin chào tôi là Bi") == "vi"

    def test_detect_english(self, model):
        assert model._detect_language("hello how are you") == "en"


class TestTopicExtraction:
    def test_extract_programming(self, model):
        topics = model._extract_topics("help me debug this python code")
        assert "programming" in topics

    def test_extract_trading(self, model):
        topics = model._extract_topics("xauusd giá hôm nay")
        assert "trading" in topics

    def test_extract_multiple(self, model):
        topics = model._extract_topics("python machine learning model training")
        assert "programming" in topics
        assert "ai/ml" in topics

    def test_no_topics(self, model):
        topics = model._extract_topics("hello there")
        assert topics == []


class TestCommunicationStyle:
    def test_analyze_short_messages(self, model):
        style = {}
        for i in range(5):
            style = model._analyze_style("ok", style, i + 1)
        assert style["short_messages"] == 5
        assert style["prefers_short"] is True

    def test_analyze_long_messages(self, model):
        style = {}
        long_msg = "x" * 150
        for i in range(5):
            style = model._analyze_style(long_msg, style, i + 1)
        assert style["long_messages"] == 5

    def test_emoji_tracking(self, model):
        style = {}
        for i in range(4):
            style = model._analyze_style("hello 😀 world", style, i + 1)
        assert style["emoji_messages"] == 4
        assert style["uses_emoji"] is True


class TestBuildContext:
    def test_no_context_few_messages(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        ctx = model.build_context(uid)
        assert ctx == ""  # < 3 messages

    def test_context_with_data(self, model):
        uid = _uid()
        model.get_or_create(uid, "CtxUser")
        for i in range(5):
            model.update_from_message(uid, "help me with python code debug")
        ctx = model.build_context(uid)
        assert "Thông tin User" in ctx
        assert "CtxUser" in ctx


class TestStyleHints:
    def test_hints_with_emoji(self, model):
        style = {"uses_emoji": True}
        m = {"topics": "{}", "total_messages": 10, "language": "vi"}
        hints = model._build_style_hints(style, m)
        assert "emoji" in hints

    def test_hints_short_preference(self, model):
        style = {"prefers_short": True}
        m = {"topics": "{}", "total_messages": 10, "language": "vi"}
        hints = model._build_style_hints(style, m)
        assert "ngắn gọn" in hints

    def test_hints_code_preference(self, model):
        style = {}
        topics = json.dumps({"programming": 5, "web": 3})
        m = {"topics": topics, "total_messages": 10, "language": "vi"}
        hints = model._build_style_hints(style, m)
        assert "code" in hints


class TestFeedbackUpdate:
    def test_positive_feedback_short(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        model.update_from_feedback(uid, "Short reply", "positive")
        result = model.get_or_create(uid)
        style = json.loads(result["communication_style"]) if isinstance(result["communication_style"], str) else result["communication_style"]
        assert style.get("liked_short", 0) == 1

    def test_positive_feedback_with_code(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        model.update_from_feedback(uid, "Here is ```code block```", "positive")
        result = model.get_or_create(uid)
        style = json.loads(result["communication_style"]) if isinstance(result["communication_style"], str) else result["communication_style"]
        assert style.get("liked_code", 0) == 1

    def test_negative_feedback_long(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        long_response = "x" * 900
        model.update_from_feedback(uid, long_response, "negative")
        result = model.get_or_create(uid)
        style = json.loads(result["communication_style"]) if isinstance(result["communication_style"], str) else result["communication_style"]
        assert style.get("disliked_long", 0) == 1


class TestPreferencesAndExpertise:
    def test_add_preference(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        model.add_preference(uid, "code_style", "concise")
        result = model.get_or_create(uid)
        prefs = json.loads(result["preferences"]) if isinstance(result["preferences"], str) else result["preferences"]
        assert prefs["code_style"] == "concise"

    def test_add_expertise(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        model.add_expertise(uid, "Python", "expert")
        result = model.get_or_create(uid)
        expertise = json.loads(result["expertise"]) if isinstance(result["expertise"], str) else result["expertise"]
        assert expertise["Python"] == "expert"

    def test_get_stats(self, model):
        uid = _uid()
        model.get_or_create(uid, "Test")
        model.update_from_message(uid, "hello")
        stats = model.get_stats(uid)
        assert stats["total_messages"] == 1
        assert "language" in stats
