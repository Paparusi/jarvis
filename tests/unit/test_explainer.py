"""Tests for ExplainerEngine — reasoning trace explanations."""

import pytest

from src.metacognition.explainer import ExplainerEngine, _model_label, _step_description


class FakeTracer:
    """Mock tracer that returns preset traces."""

    def __init__(self, traces=None):
        self._traces = traces or []

    def get_recent(self, limit=10):
        return self._traces[:limit]


class TestExplainerEngine:
    def test_explain_last_no_trace(self):
        tracer = FakeTracer([])
        engine = ExplainerEngine(tracer)
        result = engine.explain_last("session-1")
        assert "Không tìm thấy" in result

    def test_explain_last_finds_session(self):
        tracer = FakeTracer([
            {
                "session_id": "session-1",
                "user_message": "Hello world",
                "model_used": "ollama/qwen3",
                "confidence_score": 0.85,
                "total_latency_ms": 200,
            },
            {
                "session_id": "session-2",
                "user_message": "Other message",
            },
        ])
        engine = ExplainerEngine(tracer)
        result = engine.explain_last("session-1")
        assert "Hello world" in result
        assert "Local" in result
        assert "85%" in result

    def test_explain_last_wrong_session(self):
        tracer = FakeTracer([
            {"session_id": "session-2", "user_message": "test"},
        ])
        engine = ExplainerEngine(tracer)
        result = engine.explain_last("session-1")
        assert "Không tìm thấy" in result

    def test_explain_recent_empty(self):
        tracer = FakeTracer([])
        engine = ExplainerEngine(tracer)
        result = engine.explain_recent()
        assert "Chưa có" in result

    def test_explain_recent_with_traces(self):
        tracer = FakeTracer([
            {
                "session_id": "s1",
                "user_message": "debug python",
                "model_used": "cached",
                "total_latency_ms": 50,
                "confidence_score": 0.9,
            },
            {
                "session_id": "s2",
                "user_message": "tìm kiếm web",
                "model_used": "claude-3",
                "total_latency_ms": 1500,
                "confidence_score": 0.7,
            },
        ])
        engine = ExplainerEngine(tracer)
        result = engine.explain_recent(limit=2)
        assert "Reasoning gần đây" in result
        assert "debug python" in result
        assert "tìm kiếm web" in result

    def test_format_trace_with_steps(self):
        import json
        tracer = FakeTracer([
            {
                "session_id": "s1",
                "user_message": "test query",
                "complexity": "simple",
                "skills_matched": json.dumps(["code-assistant"]),
                "model_used": "ollama/qwen3",
                "confidence_score": 0.92,
                "total_latency_ms": 300,
                "steps": json.dumps([
                    {"step": "cache", "action": "check", "result": "miss", "timestamp_ms": 10},
                    {"step": "classify", "action": "complexity", "result": "simple", "timestamp_ms": 20},
                ]),
            },
        ])
        engine = ExplainerEngine(tracer)
        result = engine.explain_last("s1")
        assert "đơn giản" in result
        assert "code-assistant" in result
        assert "Các bước" in result

    def test_format_trace_escalation(self):
        tracer = FakeTracer([
            {
                "session_id": "s1",
                "user_message": "complex task",
                "model_used": "claude-3",
                "was_escalated": True,
                "confidence_score": 0.3,
                "total_latency_ms": 2000,
            },
        ])
        engine = ExplainerEngine(tracer)
        result = engine.explain_last("s1")
        assert "Escalated" in result
        assert "🔴" in result  # Low confidence


class TestModelLabel:
    def test_cached(self):
        assert "Cache" in _model_label("cached")

    def test_ollama(self):
        assert "Local" in _model_label("ollama/qwen3")

    def test_qwen(self):
        assert "Local" in _model_label("qwen3-4b")

    def test_claude(self):
        assert "Cloud" in _model_label("claude-3-sonnet")

    def test_unknown(self):
        assert _model_label("gpt-4") == "gpt-4"


class TestStepDescription:
    def test_cache_hit(self):
        result = _step_description("cache", "check", "hit")
        assert "cache" in result.lower()

    def test_cache_miss(self):
        result = _step_description("cache", "check", "miss")
        assert "Không có" in result

    def test_classify_simple(self):
        result = _step_description("classify", "complexity", "simple")
        assert "đơn giản" in result

    def test_confidence_step(self):
        result = _step_description("confidence", "0.85", "pass")
        assert "Confidence" in result

    def test_tool_call_step(self):
        result = _step_description("tool_call", "web_search", "success")
        assert "web_search" in result

    def test_error_step(self):
        result = _step_description("error", "timeout", "")
        assert "timeout" in result

    def test_unknown_step(self):
        result = _step_description("unknown", "unknown", "unknown")
        assert result == ""
