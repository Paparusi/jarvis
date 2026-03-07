"""Web Channel — FastAPI + WebSocket chat interface for JARVIS.

Provides:
- REST API for status/health/skills
- WebSocket for real-time chat with streaming responses
- Static file serving for the chat UI
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.brain.collector import DataCollector
from src.brain.processor import DataProcessor
from src.digital_twin.user_model import UserModel
from src.gateway.event_bus import get_event_bus
from src.gateway.models import AgentResponse, Channel, MessageEnvelope
from src.gateway.session import SessionManager
from src.intelligence.router import LLMRouter
from src.memory.manager import MemoryManager
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry
from src.skills.router import SkillRouter
from src.tools.base import ToolRegistry
from src.tools.browser import browse_web_tool, deep_search_tool, screenshot_tool
from src.tools.code_exec import code_exec_tool
from src.tools.document import ingest_document_tool, query_documents_tool
from src.tools.tts import tts_tool
from src.tools.vision import analyze_image_tool, ocr_tool
from src.tools.file_ops import list_dir_tool, read_file_tool, write_file_tool
from src.tools.shell import shell_tool
from src.tools.web_search import fetch_url_tool, web_search_tool
from src.tools.http_client import http_request_tool
from src.tools.network import dns_lookup_tool, ping_tool, port_scan_tool, traceroute_tool
from src.tools.git_ops import (
    git_branch_tool, git_commit_tool, git_diff_tool,
    git_log_tool, git_status_tool,
)
from src.tools.docker_ops import (
    docker_compose_tool, docker_exec_tool, docker_images_tool,
    docker_logs_tool, docker_ps_tool,
)
from src.tools.crypto_utils import (
    base64_tool, hash_tool, url_encode_tool, jwt_decode_tool,
    hex_convert_tool, regex_test_tool, timestamp_tool,
    ip_info_tool, whois_tool, ssl_check_tool,
    generate_password_tool, cidr_calc_tool,
)
from src.tools.recon import (
    subdomain_enum_tool, http_headers_tool, cve_lookup_tool,
    reverse_dns_tool, tech_detect_tool,
)
from src.tools.code_analysis import (
    ast_analyze_tool, complexity_check_tool, dependency_graph_tool,
    code_search_tool, diff_summary_tool,
)
from src.tools.data_tools import (
    csv_analyze_tool, json_query_tool, sqlite_query_tool,
    text_stats_tool, json_transform_tool,
)
from src.tools.osint import (
    google_dork_tool, username_search_tool, email_harvest_tool,
    wayback_lookup_tool, github_leaks_tool,
)
from src.tools.web_attack import (
    dir_bruteforce_tool, sqli_test_tool, xss_scan_tool,
    cors_check_tool, waf_detect_tool, lfi_test_tool, header_audit_tool,
)
from src.tools.crypto_attack import (
    hash_identify_tool, hash_crack_tool, cipher_decode_tool,
    encoding_chain_tool,
)
from src.tools.exploit import (
    exploit_search_tool, reverse_shell_gen_tool, payload_encode_tool,
    gtfobins_lookup_tool,
)
from src.tools.forensics import (
    file_metadata_tool, stego_detect_tool, ioc_extract_tool,
    log_analyze_tool,
)
from src.tools.threat_intel import (
    virustotal_lookup_tool, abuseipdb_check_tool,
    malware_hash_check_tool, shodan_search_tool,
)
from src.utils.logging import get_logger

log = get_logger("web")

# Static files directory
STATIC_DIR = Path(__file__).parent.parent.parent.parent / "web" / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="JARVIS", version="2.0")
    adapter = WebAdapter()

    # --- WebSocket chat ---
    @app.websocket("/ws/chat")
    async def websocket_chat(ws: WebSocket):
        await ws.accept()
        session_id = None
        try:
            while True:
                data = await ws.receive_json()
                msg_type = data.get("type", "message")

                if msg_type == "message":
                    text = data.get("text", "").strip()
                    if not text:
                        continue

                    session_id = data.get("session_id", session_id)

                    # Stream response back
                    async for event in adapter.handle_message_stream(text, session_id):
                        await ws.send_json(event)

                elif msg_type == "feedback":
                    rating = data.get("rating", "")
                    message_id = data.get("message_id", "")
                    if rating and message_id:
                        adapter.handle_feedback(message_id, rating)

                elif msg_type == "command":
                    cmd = data.get("command", "")
                    result = await adapter.handle_command(cmd)
                    await ws.send_json({"type": "command_result", "data": result})

        except WebSocketDisconnect:
            log.info("ws_disconnected", session=session_id)
        except Exception as e:
            log.error("ws_error", error=str(e))
            try:
                await ws.send_json({"type": "error", "text": str(e)})
            except Exception:
                pass

    # --- REST API ---
    @app.get("/")
    async def index():
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>JARVIS Web UI</h1><p>Static files not found.</p>")

    @app.get("/api/status")
    async def api_status():
        return JSONResponse(adapter.get_status())

    @app.get("/api/health")
    async def api_health():
        return JSONResponse(await adapter.get_health())

    @app.get("/api/skills")
    async def api_skills():
        return JSONResponse(adapter.get_skills())

    @app.get("/api/memory")
    async def api_memory():
        return JSONResponse(adapter.get_memories())

    # Mount static files (CSS, JS, etc.)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


class WebAdapter:
    """Web channel adapter — manages state for web clients."""

    def __init__(self) -> None:
        self._sessions = SessionManager()
        self._collector = DataCollector()
        self._processor = DataProcessor()
        self._memory = MemoryManager()
        self._user_model = UserModel()
        self._skill_loader = SkillLoader()
        self._skill_router = SkillRouter(self._skill_loader)
        self._bus = get_event_bus()

        # Load skills
        self._skill_loader.load_all()
        self._skill_registry = SkillRegistry(self._skill_loader)
        self._skill_registry._apply_metrics()

        # Register tools
        self._tool_registry = ToolRegistry()
        for tool in [
            # Core
            web_search_tool, fetch_url_tool, shell_tool,
            read_file_tool, write_file_tool, list_dir_tool,
            code_exec_tool,
            # HTTP & Browser
            http_request_tool, browse_web_tool, deep_search_tool, screenshot_tool,
            # Network & Security
            port_scan_tool, dns_lookup_tool, ping_tool, traceroute_tool,
            # Git
            git_status_tool, git_diff_tool, git_log_tool,
            git_commit_tool, git_branch_tool,
            # Docker
            docker_ps_tool, docker_logs_tool, docker_exec_tool,
            docker_images_tool, docker_compose_tool,
            # Crypto & Utility
            base64_tool, hash_tool, url_encode_tool, jwt_decode_tool,
            hex_convert_tool, regex_test_tool, timestamp_tool,
            ip_info_tool, whois_tool, ssl_check_tool,
            generate_password_tool, cidr_calc_tool,
            # Security Recon
            subdomain_enum_tool, http_headers_tool, cve_lookup_tool,
            reverse_dns_tool, tech_detect_tool,
            # Code Analysis
            ast_analyze_tool, complexity_check_tool, dependency_graph_tool,
            code_search_tool, diff_summary_tool,
            # Data Tools
            csv_analyze_tool, json_query_tool, sqlite_query_tool,
            text_stats_tool, json_transform_tool,
            # Vision & Document & TTS
            analyze_image_tool, ocr_tool,
            ingest_document_tool, query_documents_tool,
            tts_tool,
            # OSINT
            google_dork_tool, username_search_tool, email_harvest_tool,
            wayback_lookup_tool, github_leaks_tool,
            # Web Attack
            dir_bruteforce_tool, sqli_test_tool, xss_scan_tool,
            cors_check_tool, waf_detect_tool, lfi_test_tool, header_audit_tool,
            # Crypto Attack
            hash_identify_tool, hash_crack_tool, cipher_decode_tool,
            encoding_chain_tool,
            # Exploit
            exploit_search_tool, reverse_shell_gen_tool, payload_encode_tool,
            gtfobins_lookup_tool,
            # Forensics
            file_metadata_tool, stego_detect_tool, ioc_extract_tool,
            log_analyze_tool,
            # Threat Intelligence
            virustotal_lookup_tool, abuseipdb_check_tool,
            malware_hash_check_tool, shodan_search_tool,
        ]:
            self._tool_registry.register(tool)

        # Init router
        skill_summary = self._skill_loader.get_metadata_summary()
        self._router = LLMRouter(
            skill_summary=skill_summary,
            tool_registry=self._tool_registry,
        )

        self._user_id = "web_user"
        self._session = self._sessions.get_or_create(
            Channel.WEB, self._user_id, "Web User"
        )

    async def handle_message_stream(
        self, text: str, session_id: str | None = None,
    ):
        """Handle a chat message, yielding streaming events."""
        session_key = self._memory.session_key("web", self._user_id)

        # Send "thinking" indicator
        yield {"type": "thinking", "text": ""}

        try:
            # 1. Process through memory
            await self._memory.process_user_message(session_key, text)

            # 2. Update user model
            self._user_model.update_from_message(self._user_id, text)

            # 3. Get context
            memory_context = await self._memory.get_relevant_context(text, session_key)
            user_context = self._user_model.build_context(self._user_id)
            if user_context:
                memory_context = f"{user_context}\n\n{memory_context}" if memory_context else user_context

            # 4. Find skills
            matched_skills = []
            skill_context = ""
            try:
                matched_skills = await self._skill_router.find_skills(text)
                skill_context = self._skill_router.build_skill_context(matched_skills)
            except Exception as e:
                log.warning("skill_routing_error", error=str(e))

            # 5. Route through LLM with streaming
            start = time.monotonic()
            response: AgentResponse = await self._router.route(
                self._session, text,
                memory_context=memory_context,
                skill_context=skill_context,
            )
            elapsed = int((time.monotonic() - start) * 1000)

            # 6. Update state
            self._session.add_user_message(text)
            self._session.add_assistant_message(response.content)
            await self._memory.process_assistant_response(session_key, response.content)
            for skill in matched_skills:
                self._skill_router.update_usage(skill.metadata.name)

            # 7. Log
            envelope = MessageEnvelope(
                channel=Channel.WEB,
                session_id=self._session.session_id,
                user_id=self._user_id,
                username="Web User",
                content=text,
            )
            skills_used = [s.metadata.name for s in matched_skills]
            await self._collector.log_interaction(envelope, response, skills_used=skills_used)

            # 8. Stream response
            model_short = response.model_used.split("/")[-1] if "/" in response.model_used else response.model_used
            yield {
                "type": "response",
                "text": response.content,
                "model": model_short,
                "latency_ms": response.latency_ms or elapsed,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "cost": f"${response.cost_usd:.4f}" if response.cost_usd else "free",
                "tools_used": json.loads(response.reasoning_trace) if response.reasoning_trace else [],
            }

        except Exception as e:
            log.error("web_message_error", error=str(e))
            yield {"type": "error", "text": f"Lỗi: {e}"}

    def handle_feedback(self, message_id: str, rating: str) -> None:
        """Handle user feedback on a response."""
        log.info("web_feedback", message_id=message_id, rating=rating)

    async def handle_command(self, cmd: str) -> dict:
        """Handle slash commands via WebSocket."""
        if cmd == "status":
            return self.get_status()
        elif cmd == "health":
            return await self.get_health()
        elif cmd == "skills":
            return self.get_skills()
        elif cmd == "memory":
            return self.get_memories()
        elif cmd == "reset":
            self._session.messages.clear()
            return {"message": "Session reset"}
        return {"error": f"Unknown command: {cmd}"}

    def get_status(self) -> dict:
        """Get system status."""
        router_stats = self._router.get_stats()
        cost = router_stats["cost"]
        return {
            "status": "online",
            "version": "2.0",
            "skills": len(self._skill_loader.get_all_metadata()),
            "tools": len(self._tool_registry.get_all()),
            "local_model": router_stats.get("local_model", ""),
            "cloud_model": router_stats.get("cloud_model", ""),
            "local_enabled": router_stats.get("local_enabled", False),
            "total_calls": cost.get("total_calls", 0),
            "local_ratio": f"{cost.get('local_ratio', 0) * 100:.0f}%",
            "total_cost": f"${cost.get('total_cost_usd', 0):.4f}",
            "cache_entries": router_stats.get("cache", {}).get("cached_entries", 0),
        }

    async def get_health(self) -> dict:
        """Run health checks."""
        try:
            from src.metacognition.diagnostics import SelfDiagnostics
            diag = SelfDiagnostics()
            checks = await diag.run_all()
            return {
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status.value if hasattr(c.status, 'value') else str(c.status),
                        "message": c.message,
                        "latency_ms": c.latency_ms,
                    }
                    for c in checks
                ]
            }
        except Exception as e:
            return {"error": str(e)}

    def get_skills(self) -> dict:
        """Get all skills."""
        skills = self._skill_loader.get_all_metadata()
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description[:100],
                    "version": s.version,
                    "category": s.category,
                    "emoji": s.emoji or "",
                    "priority": s.priority,
                    "success_rate": s.success_rate,
                    "usage_count": s.usage_count,
                }
                for s in sorted(skills, key=lambda x: -x.priority)
            ]
        }

    def get_memories(self) -> dict:
        """Get recent memories."""
        memories = self._memory.semantic.get_all(limit=20)
        return {
            "memories": [
                {
                    "content": m["content"],
                    "category": m.get("category", ""),
                    "importance": m.get("importance", 0),
                }
                for m in memories
            ]
        }
