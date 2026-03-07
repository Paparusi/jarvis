"""Tests for Data Processor — quality filter, dedup, SFT/DPO formatting."""

from src.brain.processor import DataProcessor


class TestQualityFilter:
    def setup_method(self):
        self.processor = DataProcessor()

    def test_filter_error_response(self):
        records = [
            {"user_message": "hello", "model_response": "Xin lỗi, tôi gặp lỗi khi xử lý"},
        ]
        result = self.processor._quality_filter(records)
        assert len(result) == 0

    def test_filter_too_short_response(self):
        records = [
            {"user_message": "hello", "model_response": "ok"},
        ]
        result = self.processor._quality_filter(records)
        assert len(result) == 0

    def test_keep_good_response(self):
        records = [
            {"user_message": "hello", "model_response": "Xin chào! Tôi có thể giúp gì cho bạn?"},
        ]
        result = self.processor._quality_filter(records)
        assert len(result) == 1

    def test_filter_empty_user_message(self):
        records = [
            {"user_message": "", "model_response": "This is a good response here"},
        ]
        result = self.processor._quality_filter(records)
        assert len(result) == 0


class TestDeduplicate:
    def setup_method(self):
        self.processor = DataProcessor()

    def test_remove_duplicates(self):
        records = [
            {"user_message": "Hello", "model_response": "Hi there!"},
            {"user_message": "hello", "model_response": "Hey!"},
            {"user_message": "world", "model_response": "What about it?"},
        ]
        result = self.processor._deduplicate(records)
        assert len(result) == 2

    def test_no_duplicates(self):
        records = [
            {"user_message": "one", "model_response": "1"},
            {"user_message": "two", "model_response": "2"},
        ]
        result = self.processor._deduplicate(records)
        assert len(result) == 2


class TestFormatSFT:
    def setup_method(self):
        self.processor = DataProcessor()

    def test_chatml_format(self):
        records = [
            {
                "user_message": "Xin chào",
                "model_response": "Chào bạn!",
                "model_used": "qwen3:4b",
                "timestamp": "2026-03-04T00:00:00Z",
                "tokens_in": 10,
                "tokens_out": 5,
            },
        ]
        result = self.processor._format_sft(records)
        assert len(result) == 1
        entry = result[0]
        assert len(entry["messages"]) == 3
        assert entry["messages"][0]["role"] == "system"
        assert entry["messages"][1]["role"] == "user"
        assert entry["messages"][1]["content"] == "Xin chào"
        assert entry["messages"][2]["role"] == "assistant"
        assert entry["messages"][2]["content"] == "Chào bạn!"


class TestGenerateDPO:
    def setup_method(self):
        self.processor = DataProcessor()

    def test_dpo_pair_creation(self):
        records = [
            {
                "user_message": "What is Python?",
                "model_response": "Python is lang",
                "model_used": "ollama/qwen3:4b",
            },
            {
                "user_message": "What is Python?",
                "model_response": "Python is a high-level programming language...",
                "model_used": "claude-sonnet-4-20250514",
            },
        ]
        result = self.processor._generate_dpo_pairs(records)
        assert len(result) == 1
        pair = result[0]
        assert "Python" in pair["prompt"]
        assert "high-level" in pair["chosen"]
        assert "lang" in pair["rejected"]
        assert "claude" in pair["chosen_model"]
        assert "ollama" in pair["rejected_model"]

    def test_no_dpo_same_model(self):
        records = [
            {
                "user_message": "Hello",
                "model_response": "Hi 1",
                "model_used": "ollama/qwen3:4b",
            },
            {
                "user_message": "Hello",
                "model_response": "Hi 2",
                "model_used": "ollama/qwen3:4b",
            },
        ]
        result = self.processor._generate_dpo_pairs(records)
        assert len(result) == 0
