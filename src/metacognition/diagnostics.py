"""Self-Diagnostics — JARVIS health monitoring and self-assessment.

Checks system health: Ollama, API keys, disk, database, tools, memory.
Can be triggered via /health command or self-diagnostics skill.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.utils.config import get_env, get_project_root
from src.utils.logging import get_logger

log = get_logger("metacognition.diagnostics")


@dataclass
class HealthCheck:
    """Result of a single health check."""

    name: str
    status: str  # "ok", "warning", "error"
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def emoji(self) -> str:
        return {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(self.status, "❓")


class SelfDiagnostics:
    """Run health checks on all JARVIS systems."""

    async def run_all(self) -> list[HealthCheck]:
        """Run all diagnostics and return results."""
        checks = await asyncio.gather(
            self.check_ollama(),
            self.check_api_keys(),
            self.check_disk_space(),
            self.check_database(),
            self.check_training_data(),
            return_exceptions=True,
        )

        results = []
        for check in checks:
            if isinstance(check, Exception):
                results.append(HealthCheck(
                    name="unknown",
                    status="error",
                    message=f"Check failed: {check}",
                ))
            else:
                results.append(check)

        return results

    async def check_ollama(self) -> HealthCheck:
        """Check if Ollama is running and responsive."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    has_qwen = any("qwen" in m.lower() for m in models)
                    return HealthCheck(
                        name="Ollama",
                        status="ok" if has_qwen else "warning",
                        message=f"{len(models)} models loaded" + ("" if has_qwen else " (Qwen3 not found!)"),
                        details={"models": models},
                    )
                return HealthCheck(name="Ollama", status="error", message=f"HTTP {resp.status_code}")
        except ImportError:
            return HealthCheck(name="Ollama", status="warning", message="httpx not installed")
        except Exception as e:
            return HealthCheck(name="Ollama", status="error", message=f"Not reachable: {e}")

    async def check_api_keys(self) -> HealthCheck:
        """Check required API keys are set."""
        anthropic = get_env("ANTHROPIC_API_KEY")
        telegram = get_env("TELEGRAM_BOT_TOKEN")

        missing = []
        if not anthropic:
            missing.append("ANTHROPIC_API_KEY")
        if not telegram:
            missing.append("TELEGRAM_BOT_TOKEN")

        if not missing:
            return HealthCheck(name="API Keys", status="ok", message="All keys set")
        return HealthCheck(
            name="API Keys",
            status="error",
            message=f"Missing: {', '.join(missing)}",
        )

    async def check_disk_space(self) -> HealthCheck:
        """Check available disk space."""
        root = get_project_root()
        usage = shutil.disk_usage(root)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = (usage.used / usage.total) * 100

        # Check project size
        project_size = sum(
            f.stat().st_size for f in root.rglob("*") if f.is_file()
        ) / (1024 ** 2)  # MB

        if free_gb < 1:
            status = "error"
        elif free_gb < 5:
            status = "warning"
        else:
            status = "ok"

        return HealthCheck(
            name="Disk Space",
            status=status,
            message=f"{free_gb:.1f}GB free / {total_gb:.1f}GB total ({used_pct:.0f}% used), project: {project_size:.0f}MB",
            details={"free_gb": free_gb, "project_mb": project_size},
        )

    async def check_database(self) -> HealthCheck:
        """Check SQLite database health."""
        db_path = get_project_root() / "data" / "jarvis.db"
        if not db_path.exists():
            return HealthCheck(name="Database", status="warning", message="DB file not found")

        size_mb = db_path.stat().st_size / (1024 ** 2)

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Count tables
            cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
            table_count = cursor.fetchone()[0]

            # Count semantic memories
            try:
                cursor.execute("SELECT count(*) FROM semantic_memories")
                memory_count = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                memory_count = 0

            conn.close()

            return HealthCheck(
                name="Database",
                status="ok",
                message=f"{size_mb:.1f}MB, {table_count} tables, {memory_count} memories",
                details={"size_mb": size_mb, "tables": table_count, "memories": memory_count},
            )
        except Exception as e:
            return HealthCheck(name="Database", status="error", message=f"Error: {e}")

    async def check_training_data(self) -> HealthCheck:
        """Check training data collection status."""
        data_dir = get_project_root() / "training" / "data" / "raw"
        if not data_dir.exists():
            return HealthCheck(name="Training Data", status="warning", message="No data directory")

        files = list(data_dir.glob("interactions-*.jsonl"))
        total_records = 0
        for f in files:
            with open(f) as fh:
                total_records += sum(1 for _ in fh)

        total_size = sum(f.stat().st_size for f in files) / 1024  # KB

        return HealthCheck(
            name="Training Data",
            status="ok" if total_records > 0 else "warning",
            message=f"{total_records} records in {len(files)} files ({total_size:.0f}KB)",
            details={"records": total_records, "files": len(files)},
        )

    def format_report(self, checks: list[HealthCheck]) -> str:
        """Format health checks into a readable report."""
        lines = ["🏥 **JARVIS Health Report**\n"]
        for check in checks:
            lines.append(f"{check.emoji} **{check.name}**: {check.message}")

        ok_count = sum(1 for c in checks if c.status == "ok")
        total = len(checks)
        lines.append(f"\n{'✅' if ok_count == total else '⚠️'} {ok_count}/{total} checks passed")

        return "\n".join(lines)
