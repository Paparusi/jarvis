"""Explainer Engine — Translate reasoning traces into human-readable text.

Makes JARVIS transparent: users can understand WHY a decision was made.
Translates technical traces (confidence scores, model routing, skill matching)
into natural Vietnamese explanations.
"""

from __future__ import annotations

from src.metacognition.tracer import ReasoningTracer
from src.utils.logging import get_logger

log = get_logger("metacognition.explainer")


class ExplainerEngine:
    """Translate technical reasoning traces into user-friendly explanations."""

    def __init__(self, tracer: ReasoningTracer) -> None:
        self._tracer = tracer

    def explain_last(self, session_id: str) -> str:
        """Get a human-readable explanation of the last request's reasoning."""
        traces = self._tracer.get_recent(limit=10)

        # Find the most recent trace for this session
        trace = None
        for t in traces:
            if t.get("session_id") == session_id:
                trace = t
                break

        if not trace:
            return "Không tìm thấy lịch sử reasoning cho phiên này."

        return self._format_trace(trace)

    def explain_recent(self, limit: int = 3) -> str:
        """Explain the last N requests across all sessions."""
        traces = self._tracer.get_recent(limit=limit)
        if not traces:
            return "Chưa có lịch sử reasoning."

        lines = ["📊 **Reasoning gần đây:**\n"]
        for trace in traces:
            lines.append(self._format_trace_brief(trace))
        return "\n".join(lines)

    def _format_trace(self, trace: dict) -> str:
        """Format a single trace into detailed explanation."""
        import json

        lines = ["🧠 **Cách JARVIS xử lý request cuối:**\n"]

        # Query
        query = trace.get("user_message", "")[:100]
        lines.append(f"📝 **Query:** {query}")

        # Complexity
        complexity = trace.get("complexity", "")
        if complexity:
            complexity_vi = {
                "simple": "đơn giản",
                "medium": "trung bình",
                "complex": "phức tạp",
            }.get(complexity, complexity)
            lines.append(f"📊 **Độ phức tạp:** {complexity_vi}")

        # Skills matched
        skills = trace.get("skills_matched", "[]")
        if isinstance(skills, str):
            try:
                skills = json.loads(skills)
            except (json.JSONDecodeError, TypeError):
                skills = []
        if skills:
            lines.append(f"🎯 **Skills matched:** {', '.join(skills)}")

        # Model used
        model = trace.get("model_used", "")
        if model:
            model_label = _model_label(model)
            lines.append(f"🤖 **Model:** {model_label}")

        # Confidence
        confidence = trace.get("confidence_score", 0)
        if confidence > 0:
            emoji = "🟢" if confidence >= 0.75 else "🟡" if confidence >= 0.4 else "🔴"
            lines.append(f"{emoji} **Confidence:** {confidence:.0%}")

        # Escalation
        if trace.get("was_escalated"):
            lines.append("⬆️ **Escalated:** Local model không đủ tốt → chuyển sang cloud")

        # Latency
        latency = trace.get("total_latency_ms", 0)
        if latency:
            lines.append(f"⏱️ **Thời gian:** {latency}ms")

        # Steps detail
        steps_raw = trace.get("steps", "[]")
        if isinstance(steps_raw, str):
            try:
                steps = json.loads(steps_raw)
            except (json.JSONDecodeError, TypeError):
                steps = []
        else:
            steps = steps_raw

        if steps:
            lines.append("\n**Các bước:**")
            for step in steps[:8]:
                step_name = step.get("step", "")
                action = step.get("action", "")
                result = step.get("result", "")
                ts = step.get("timestamp_ms", 0)
                desc = _step_description(step_name, action, result)
                if desc:
                    lines.append(f"  {ts}ms → {desc}")

        return "\n".join(lines)

    def _format_trace_brief(self, trace: dict) -> str:
        """Format a single trace into a one-line summary."""
        query = trace.get("user_message", "")[:40]
        model = _model_label(trace.get("model_used", ""))
        latency = trace.get("total_latency_ms", 0)
        confidence = trace.get("confidence_score", 0)
        escalated = "⬆️" if trace.get("was_escalated") else ""

        conf_str = f"{confidence:.0%}" if confidence > 0 else "N/A"
        return f"• `{query}` → {model} ({latency}ms, conf={conf_str}) {escalated}"


def _model_label(model: str) -> str:
    """Convert model ID to friendly name."""
    if "cached" in model:
        return "Cache ⚡"
    if "ollama" in model or "qwen" in model:
        return "Local (Qwen3) 🏠"
    if "claude" in model:
        return "Cloud (Claude) ☁️"
    return model


def _step_description(step: str, action: str, result: str) -> str:
    """Convert a trace step to a Vietnamese description."""
    descriptions = {
        ("cache", "check", "hit"): "✅ Tìm thấy trong cache",
        ("cache", "check", "miss"): "❌ Không có trong cache",
        ("cache", "check", "skip_personalized"): "⏭️ Bỏ qua cache (tin nhắn cá nhân hóa)",
        ("classify", "complexity", "simple"): "📊 Phân loại: đơn giản",
        ("classify", "complexity", "medium"): "📊 Phân loại: trung bình",
        ("classify", "complexity", "complex"): "📊 Phân loại: phức tạp",
        ("route", "try_local", ""): "🏠 Thử local model",
        ("route", "cloud", ""): "☁️ Gọi cloud model",
        ("escalate", "local_failed", ""): "⬆️ Local thất bại → escalate lên cloud",
        ("feedback_loop", "escalate", ""): "⬆️ Feedback loop: queries tương tự từng bị 👎",
    }

    key = (step, action, result)
    if key in descriptions:
        return descriptions[key]

    # Generic fallback
    if step == "confidence":
        return f"🎯 Confidence check: {result} (score={action})"
    if step == "tool_call":
        return f"🔧 Gọi tool: {action} → {result}"
    if step == "error":
        return f"❌ Lỗi: {action}"

    return ""
