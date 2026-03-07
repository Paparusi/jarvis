"""Reasoning Tracer — Chain-of-thought logging for every request.

Ghi lại toàn bộ decision chain:
1. Query classification (complexity)
2. Model selection + reasoning
3. Skills matched + context injected
4. Confidence assessment
5. Escalation decision
6. Final outcome

Traces are stored for analysis and future training data.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("metacognition.tracer")


@dataclass
class TraceStep:
    """A single step in the reasoning chain."""
    step: str
    action: str
    result: str
    metadata: dict = field(default_factory=dict)
    timestamp_ms: int = 0


@dataclass
class ReasoningTrace:
    """Complete trace of a request's decision chain."""
    trace_id: str = field(default_factory=lambda: str(uuid4())[:12])
    session_id: str = ""
    user_message: str = ""
    steps: list[TraceStep] = field(default_factory=list)
    start_time: float = 0.0

    # Outcomes
    final_model: str = ""
    was_escalated: bool = False
    confidence_score: float = 0.0
    total_latency_ms: int = 0

    def add_step(self, step: str, action: str, result: str, **metadata) -> None:
        self.steps.append(TraceStep(
            step=step,
            action=action,
            result=result,
            metadata=metadata,
            timestamp_ms=int((time.monotonic() - self.start_time) * 1000) if self.start_time else 0,
        ))


class ReasoningTracer:
    """Record and persist reasoning traces."""

    def __init__(self) -> None:
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reasoning_traces (
                trace_id TEXT PRIMARY KEY,
                session_id TEXT,
                user_message TEXT,
                complexity TEXT,
                skills_matched TEXT DEFAULT '[]',
                model_used TEXT,
                was_escalated INTEGER DEFAULT 0,
                confidence_score REAL DEFAULT 0,
                total_latency_ms INTEGER DEFAULT 0,
                steps TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trace_session ON reasoning_traces(session_id)"
        )
        conn.commit()

    def start_trace(self, session_id: str, user_message: str) -> ReasoningTrace:
        """Start a new reasoning trace."""
        trace = ReasoningTrace(
            session_id=session_id,
            user_message=user_message,
            start_time=time.monotonic(),
        )
        return trace

    def save_trace(self, trace: ReasoningTrace) -> None:
        """Persist a completed trace."""
        trace.total_latency_ms = int(
            (time.monotonic() - trace.start_time) * 1000
        ) if trace.start_time else 0

        conn = get_connection()

        # Extract complexity from steps
        complexity = ""
        skills = []
        for step in trace.steps:
            if step.step == "classify":
                complexity = step.result
            if step.step == "skills":
                skills = step.metadata.get("matched", [])

        conn.execute(
            """INSERT OR REPLACE INTO reasoning_traces
               (trace_id, session_id, user_message, complexity, skills_matched,
                model_used, was_escalated, confidence_score, total_latency_ms, steps)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace.trace_id,
                trace.session_id,
                trace.user_message[:500],
                complexity,
                json.dumps(skills),
                trace.final_model,
                1 if trace.was_escalated else 0,
                trace.confidence_score,
                trace.total_latency_ms,
                json.dumps([asdict(s) for s in trace.steps], ensure_ascii=False),
            ),
        )
        conn.commit()

        log.info(
            "trace_saved",
            trace_id=trace.trace_id,
            model=trace.final_model,
            escalated=trace.was_escalated,
            confidence=f"{trace.confidence_score:.2f}",
            latency_ms=trace.total_latency_ms,
            steps=len(trace.steps),
        )

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get recent traces for analysis."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM reasoning_traces ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_escalation_rate(self, days: int = 7) -> dict:
        """Get escalation statistics."""
        conn = get_connection()
        row = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(was_escalated) as escalated,
                AVG(confidence_score) as avg_confidence,
                AVG(total_latency_ms) as avg_latency_ms
            FROM reasoning_traces
            WHERE created_at > datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()

        total = row["total"] or 0
        escalated = row["escalated"] or 0

        return {
            "total_requests": total,
            "escalated": escalated,
            "escalation_rate": f"{escalated/max(total,1):.1%}",
            "avg_confidence": round(row["avg_confidence"] or 0, 2),
            "avg_latency_ms": round(row["avg_latency_ms"] or 0),
        }
