"""Tests for LLM Router — complexity classification, thinking tags, quality check."""

from src.intelligence.router import (
    check_response_quality,
    classify_complexity,
    strip_thinking,
)


class TestClassifyComplexity:
    def test_simple_greeting(self):
        assert classify_complexity("xin chào") == "simple"

    def test_simple_hi(self):
        assert classify_complexity("hello") == "simple"

    def test_simple_thanks(self):
        assert classify_complexity("cảm ơn") == "simple"

    def test_complex_code_request(self):
        assert classify_complexity("viết code Python sort array") == "complex"

    def test_complex_analysis(self):
        assert classify_complexity("phân tích tình hình thị trường XAUUSD") == "complex"

    def test_complex_long_message(self):
        text = " ".join(["word"] * 60)
        assert classify_complexity(text) == "complex"

    def test_complex_code_block(self):
        assert classify_complexity("fix this:\n```python\ndef foo():\n  pass\n```") == "complex"

    def test_medium_default(self):
        assert classify_complexity("thời tiết hôm nay thế nào") == "medium"

    def test_medium_question(self):
        assert classify_complexity("Python là gì vậy bạn ơi") == "medium"


class TestStripThinking:
    def test_strip_thinking_tags(self):
        text = "<think>I need to think about this...</think>Hello world!"
        assert strip_thinking(text) == "Hello world!"

    def test_strip_multiline_thinking(self):
        text = "<think>\nLet me analyze:\n- point 1\n- point 2\n</think>\nHere is my answer."
        assert strip_thinking(text) == "Here is my answer."

    def test_no_thinking_tags(self):
        text = "Just a normal response."
        assert strip_thinking(text) == "Just a normal response."

    def test_empty_thinking(self):
        text = "<think></think>Answer."
        assert strip_thinking(text) == "Answer."

    def test_multiple_thinking_tags(self):
        text = "<think>first</think>Part 1 <think>second</think>Part 2"
        assert strip_thinking(text) == "Part 1 Part 2"


class TestCheckResponseQuality:
    def test_good_response(self):
        assert check_response_quality("Xin chào! Tôi có thể giúp gì cho bạn?")

    def test_too_short(self):
        assert not check_response_quality("ok")

    def test_empty(self):
        assert not check_response_quality("")

    def test_whitespace_only(self):
        assert not check_response_quality("   ")

    def test_repetitive(self):
        text = " ".join(["xin"] * 20)
        assert not check_response_quality(text)

    def test_question_only(self):
        assert not check_response_quality("Bạn muốn gì?")

    def test_normal_with_question_mark(self):
        # Multi-line response with question mark is fine
        assert check_response_quality("Câu trả lời đây.\nBạn cần thêm gì không?")
