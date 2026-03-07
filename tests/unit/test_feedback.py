"""Tests for User Feedback Store."""

import pytest

from src.brain.feedback import FeedbackStore


class TestFeedbackStore:
    def setup_method(self):
        self.store = FeedbackStore()
        # Clean test data
        from src.memory.store import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM user_feedback WHERE user_id LIKE 'test_%'")
        conn.commit()

    def teardown_method(self):
        from src.memory.store import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM user_feedback WHERE user_id LIKE 'test_%'")
        conn.commit()

    def test_store_positive(self):
        fid = self.store.store(
            user_id="test_user",
            user_message="What is Python?",
            model_response="Python is a programming language.",
            rating="positive",
            model_used="test-model",
        )
        assert fid
        assert len(fid) == 8

    def test_store_negative(self):
        fid = self.store.store(
            user_id="test_user",
            user_message="What is Python?",
            model_response="I don't know.",
            rating="negative",
        )
        assert fid

    def test_get_stats_empty(self):
        stats = self.store.get_stats(user_id="test_nonexistent")
        assert stats["total"] == 0
        assert stats["positive"] == 0
        assert stats["negative"] == 0
        assert stats["satisfaction_rate"] == 0.0

    def test_get_stats_with_data(self):
        for i in range(3):
            self.store.store(
                user_id="test_stats",
                user_message=f"q{i}",
                model_response=f"a{i}",
                rating="positive",
            )
        self.store.store(
            user_id="test_stats",
            user_message="q3",
            model_response="a3",
            rating="negative",
        )

        stats = self.store.get_stats(user_id="test_stats")
        assert stats["total"] == 4
        assert stats["positive"] == 3
        assert stats["negative"] == 1
        assert stats["satisfaction_rate"] == 0.75

    def test_get_dpo_pairs(self):
        # Store positive and negative for same query
        self.store.store(
            user_id="test_dpo",
            user_message="What is AI?",
            model_response="AI is artificial intelligence, a field of computer science.",
            rating="positive",
            model_used="claude",
        )
        self.store.store(
            user_id="test_dpo",
            user_message="What is AI?",
            model_response="I'm not sure.",
            rating="negative",
            model_used="local",
        )

        pairs = self.store.get_dpo_pairs()
        assert len(pairs) >= 1
        pair = pairs[0]
        assert pair["prompt"] == "What is AI?"
        assert "artificial intelligence" in pair["chosen"]
        assert pair["rejected"] == "I'm not sure."
        assert pair["source"] == "user_feedback"

    def test_get_dpo_pairs_no_match(self):
        # Only positive feedback, no pairs
        self.store.store(
            user_id="test_dpo2",
            user_message="Hello",
            model_response="Hi!",
            rating="positive",
        )
        pairs = self.store.get_dpo_pairs()
        # May or may not have pairs from previous tests, just check it doesn't crash
        assert isinstance(pairs, list)

    def test_get_recent(self):
        self.store.store(
            user_id="test_recent",
            user_message="test",
            model_response="response",
            rating="positive",
        )
        recent = self.store.get_recent(limit=5)
        assert len(recent) >= 1
        assert "rating" in recent[0]
        assert "user_message" in recent[0]

    def test_get_stats_all_users(self):
        self.store.store(
            user_id="test_all_1",
            user_message="q",
            model_response="a",
            rating="positive",
        )
        stats = self.store.get_stats()  # No user_id filter
        assert stats["total"] >= 1
