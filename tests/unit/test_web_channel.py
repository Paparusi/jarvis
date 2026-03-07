"""Tests for Web Channel — FastAPI + WebSocket chat interface."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestWebStaticFiles:
    def test_index_html_exists(self):
        html_path = Path(__file__).parent.parent.parent / "web" / "static" / "index.html"
        assert html_path.exists(), "index.html should exist"

    def test_index_html_has_chat_ui(self):
        html_path = Path(__file__).parent.parent.parent / "web" / "static" / "index.html"
        content = html_path.read_text()
        assert "JARVIS" in content
        assert "WebSocket" in content
        assert "chatContainer" in content
        assert "inputBox" in content

    def test_index_html_has_markdown_renderer(self):
        html_path = Path(__file__).parent.parent.parent / "web" / "static" / "index.html"
        content = html_path.read_text()
        assert "renderMarkdown" in content

    def test_index_html_has_status_panel(self):
        html_path = Path(__file__).parent.parent.parent / "web" / "static" / "index.html"
        content = html_path.read_text()
        assert "sidePanel" in content
        assert "Status" in content
        assert "Skills" in content


class TestWebAdapterImport:
    def test_can_import_web_adapter(self):
        from src.gateway.channels.web import WebAdapter
        assert WebAdapter is not None

    def test_can_import_create_app(self):
        from src.gateway.channels.web import create_app
        assert callable(create_app)


class TestWebAdapterInit:
    @patch("src.gateway.channels.web.SkillLoader")
    @patch("src.gateway.channels.web.SkillRegistry")
    @patch("src.gateway.channels.web.SkillRouter")
    @patch("src.gateway.channels.web.MemoryManager")
    @patch("src.gateway.channels.web.UserModel")
    @patch("src.gateway.channels.web.DataCollector")
    @patch("src.gateway.channels.web.DataProcessor")
    @patch("src.gateway.channels.web.SessionManager")
    @patch("src.gateway.channels.web.LLMRouter")
    @patch("src.gateway.channels.web.get_event_bus")
    def test_web_adapter_initializes(self, *mocks):
        from src.gateway.channels.web import WebAdapter
        adapter = WebAdapter()
        assert adapter._user_id == "web_user"

    @patch("src.gateway.channels.web.SkillLoader")
    @patch("src.gateway.channels.web.SkillRegistry")
    @patch("src.gateway.channels.web.SkillRouter")
    @patch("src.gateway.channels.web.MemoryManager")
    @patch("src.gateway.channels.web.UserModel")
    @patch("src.gateway.channels.web.DataCollector")
    @patch("src.gateway.channels.web.DataProcessor")
    @patch("src.gateway.channels.web.SessionManager")
    @patch("src.gateway.channels.web.LLMRouter")
    @patch("src.gateway.channels.web.get_event_bus")
    def test_get_status(self, *mocks):
        from src.gateway.channels.web import WebAdapter
        adapter = WebAdapter()

        # Mock router stats
        adapter._router = MagicMock()
        adapter._router.get_stats.return_value = {
            "cost": {"total_calls": 10, "local_ratio": 0.5, "total_cost_usd": 0.02},
            "local_model": "jarvis-brain",
            "cloud_model": "claude-sonnet",
            "local_enabled": True,
            "cache": {"cached_entries": 5},
        }
        adapter._skill_loader = MagicMock()
        adapter._skill_loader.get_all_metadata.return_value = [MagicMock() for _ in range(10)]
        adapter._tool_registry = MagicMock()
        adapter._tool_registry.get_all.return_value = [MagicMock() for _ in range(20)]

        status = adapter.get_status()
        assert status["status"] == "online"
        assert status["skills"] == 10
        assert status["tools"] == 20


class TestCreateApp:
    @patch("src.gateway.channels.web.WebAdapter")
    def test_create_app_returns_fastapi(self, mock_adapter):
        from src.gateway.channels.web import create_app
        app = create_app()
        assert app.title == "JARVIS"

    @patch("src.gateway.channels.web.WebAdapter")
    def test_create_app_has_routes(self, mock_adapter):
        from src.gateway.channels.web import create_app
        app = create_app()
        routes = [r.path for r in app.routes if hasattr(r, 'path')]
        assert "/" in routes
        assert "/api/status" in routes
        assert "/api/health" in routes
        assert "/api/skills" in routes
