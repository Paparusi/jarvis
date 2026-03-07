"""Tests for Confidence Calibrator — meta-cognitive assessment."""

from src.metacognition.confidence import ConfidenceCalibrator


class TestConfidenceCalibrator:
    def setup_method(self):
        self.calibrator = ConfidenceCalibrator(
            accept_threshold=0.75,
            escalate_threshold=0.40,
        )

    def test_good_response_accepted(self):
        report = self.calibrator.assess(
            query="Python là gì?",
            response="Python là ngôn ngữ lập trình bậc cao, dễ học, được sử dụng rộng rãi trong nhiều lĩnh vực như web development, data science, AI, automation.",
            complexity="medium",
        )
        assert report.decision == "accept"
        assert report.overall_score >= 0.75

    def test_empty_response_rejected(self):
        report = self.calibrator.assess(
            query="Viết code Python",
            response="",
            complexity="complex",
        )
        assert report.should_escalate

    def test_short_response_for_complex_query(self):
        report = self.calibrator.assess(
            query="Phân tích chi tiết kiến trúc microservices",
            response="ok",
            complexity="complex",
        )
        assert report.should_escalate

    def test_language_mismatch(self):
        report = self.calibrator.assess(
            query="Chào bạn, hôm nay thế nào?",
            response="I am doing fine, thank you for asking!",
            complexity="simple",
        )
        # Language mismatch should lower confidence
        assert report.overall_score < 0.9

    def test_code_expected_but_missing(self):
        report = self.calibrator.assess(
            query="viết code Python sort array",
            response="Bạn có thể sử dụng hàm sort để sắp xếp mảng.",
            complexity="complex",
        )
        # Should have lower confidence because no code block
        assert report.overall_score < 0.8

    def test_code_expected_and_present(self):
        report = self.calibrator.assess(
            query="viết code Python sort array",
            response="Đây là code sắp xếp mảng:\n```python\ndef sort_array(arr):\n    return sorted(arr)\n```",
            complexity="complex",
        )
        assert report.decision == "accept"

    def test_repetitive_response(self):
        report = self.calibrator.assess(
            query="Giải thích machine learning",
            response=" ".join(["Machine learning là"] * 20),
            complexity="medium",
        )
        assert report.should_escalate

    def test_filler_phrases(self):
        report = self.calibrator.assess(
            query="Giải thích quantum computing",
            response="Tôi không biết. Tôi không chắc. As an AI, tôi không thể giải thích.",
            complexity="complex",
        )
        assert report.should_escalate

    def test_simple_greeting_accepted(self):
        report = self.calibrator.assess(
            query="xin chào",
            response="Xin chào! Tôi có thể giúp gì cho bạn?",
            complexity="simple",
        )
        assert report.decision == "accept"

    def test_truncated_response(self):
        report = self.calibrator.assess(
            query="Giải thích Docker",
            response="Docker là một nền tảng containerization cho phép bạn đóng gói ứng dụng cùng với tất cả các dependencies vào một container, giúp đảm bảo ứng dụng chạy nhất quán trên mọi môi trư",
            complexity="medium",
        )
        # Truncated response should have lower completeness score
        signals = {s.name: s.score for s in report.signals}
        assert signals.get("completeness", 1.0) < 1.0
