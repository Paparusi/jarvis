"""Tests for Session Manager — recovery from episodic memory."""

import pytest

from src.gateway.models import Channel
from src.gateway.session import SessionManager
from src.memory.episodic import EpisodicMemory


class TestSessionManager:
    @pytest.fixture
    def manager(self):
        return SessionManager()

    def test_create_new_session(self, manager):
        session = manager.get_or_create(Channel.CLI, "user1", "Test")
        assert session.user_id == "user1"
        assert session.channel == Channel.CLI

    def test_get_existing_session(self, manager):
        s1 = manager.get_or_create(Channel.CLI, "user1")
        s2 = manager.get_or_create(Channel.CLI, "user1")
        assert s1.session_id == s2.session_id

    def test_different_users(self, manager):
        s1 = manager.get_or_create(Channel.CLI, "user1")
        s2 = manager.get_or_create(Channel.CLI, "user2")
        assert s1.session_id != s2.session_id

    def test_different_channels(self, manager):
        s1 = manager.get_or_create(Channel.CLI, "user1")
        s2 = manager.get_or_create(Channel.TELEGRAM, "user1")
        assert s1.session_id != s2.session_id

    def test_count(self, manager):
        assert manager.count() == 0
        manager.get_or_create(Channel.CLI, "user1")
        assert manager.count() == 1

    def test_get_nonexistent(self, manager):
        assert manager.get(Channel.CLI, "nonexistent") is None

    def test_recovery_from_episodic(self):
        """New sessions should recover history from episodic memory."""
        ep = EpisodicMemory()
        session_key = "cli:recovery_test_user"

        # Pre-populate episodic memory
        ep.save_message(session_key, "user", "hello from before restart")
        ep.save_message(session_key, "assistant", "hi there!")

        # Create new session manager (simulating restart)
        manager = SessionManager()
        session = manager.get_or_create(Channel.CLI, "recovery_test_user")

        # Should have recovered history
        history = session.get_history()
        assert len(history) >= 2
        assert any("hello from before restart" in m["content"] for m in history)
        assert any("hi there!" in m["content"] for m in history)
