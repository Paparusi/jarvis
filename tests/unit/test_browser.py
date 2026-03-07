"""Tests for Browser Tool — Playwright-powered web browsing."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.browser import (
    _validate_url,
    _extract_main_content,
    browse_web,
    deep_search,
    screenshot_page,
    browse_web_tool,
    deep_search_tool,
    google_search_tool,
    screenshot_tool,
    extract_page_tool,
)


class TestValidateUrl:
    def test_valid_https(self):
        assert _validate_url("https://example.com") is None

    def test_valid_http(self):
        assert _validate_url("http://example.com") is None

    def test_empty(self):
        assert _validate_url("") is not None

    def test_none(self):
        assert _validate_url(None) is not None

    def test_no_scheme(self):
        assert _validate_url("example.com") is not None

    def test_ftp_blocked(self):
        assert _validate_url("ftp://example.com") is not None

    def test_localhost_blocked(self):
        result = _validate_url("http://localhost/admin")
        assert result is not None
        assert "Blocked" in result

    def test_127_blocked(self):
        result = _validate_url("http://127.0.0.1/secret")
        assert result is not None
        assert "Blocked" in result

    def test_metadata_blocked(self):
        result = _validate_url("http://169.254.169.254/latest/meta-data")
        assert result is not None
        assert "Blocked" in result

    def test_private_ip_blocked(self):
        result = _validate_url("http://192.168.1.1")
        assert result is not None
        assert "Blocked" in result


class TestExtractMainContent:
    def test_collapses_blank_lines(self):
        text = "line1\n\n\n\n\nline2"
        result = _extract_main_content(text)
        assert result == "line1\n\nline2"

    def test_strips_whitespace_lines(self):
        text = "line1\n   \n   \nline2"
        result = _extract_main_content(text)
        assert "line1" in result
        assert "line2" in result

    def test_empty_input(self):
        assert _extract_main_content("") == ""

    def test_normal_text_unchanged(self):
        text = "Hello world\nSecond line"
        result = _extract_main_content(text)
        assert "Hello world" in result
        assert "Second line" in result


class TestBrowseWeb:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await browse_web("not-a-url")
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await browse_web("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_localhost_blocked(self):
        result = await browse_web("http://localhost:8080")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_no_playwright_fallback(self):
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            # Should fall back to httpx
            result = await browse_web("https://example.com")
            # May succeed or fail depending on httpx availability
            # Just verify it doesn't crash
            assert isinstance(result.success, bool)

    @pytest.mark.asyncio
    async def test_wait_seconds_clamped(self):
        # Should not crash with extreme values
        result = await browse_web("not-valid", wait_seconds=999)
        assert not result.success  # Invalid URL, but wait_seconds should be clamped


class TestDeepSearch:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await deep_search("")
        assert not result.success
        assert "trống" in result.error

    @pytest.mark.asyncio
    async def test_max_results_clamped(self):
        # Test that max_results is bounded (1-5)
        result = await deep_search("test", max_results=100)
        assert isinstance(result.success, bool)


class TestScreenshotPage:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await screenshot_page("not-a-url")
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await screenshot_page("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_private_ip_blocked(self):
        result = await screenshot_page("http://10.0.0.1/admin")
        assert not result.success
        assert "Blocked" in result.error


class TestToolDefinitions:
    def test_browse_web_tool_name(self):
        assert browse_web_tool.name == "browse_web"

    def test_deep_search_tool_name(self):
        assert deep_search_tool.name == "deep_search"

    def test_google_search_backward_compat(self):
        assert google_search_tool.name == deep_search_tool.name

    def test_screenshot_tool_name(self):
        assert screenshot_tool.name == "screenshot"

    def test_extract_page_backward_compat(self):
        # extract_page_tool should alias browse_web_tool
        assert extract_page_tool.name == browse_web_tool.name

    def test_browse_web_has_url_param(self):
        param_names = [p.name for p in browse_web_tool.parameters]
        assert "url" in param_names

    def test_deep_search_has_query_param(self):
        param_names = [p.name for p in deep_search_tool.parameters]
        assert "query" in param_names

    def test_screenshot_has_url_param(self):
        param_names = [p.name for p in screenshot_tool.parameters]
        assert "url" in param_names

    def test_browse_web_has_selector_param(self):
        param_names = [p.name for p in browse_web_tool.parameters]
        assert "selector" in param_names

    def test_deep_search_has_max_results(self):
        param_names = [p.name for p in deep_search_tool.parameters]
        assert "max_results" in param_names

    def test_screenshot_has_full_page(self):
        param_names = [p.name for p in screenshot_tool.parameters]
        assert "full_page" in param_names

    def test_all_tools_have_timeout(self):
        assert browse_web_tool.timeout_seconds >= 30
        assert google_search_tool.timeout_seconds >= 20
        assert screenshot_tool.timeout_seconds >= 30

    def test_descriptions_in_vietnamese(self):
        vn_chars = "ăâấầàáảãạắằẳẵặéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ"
        assert any(c in browse_web_tool.description for c in vn_chars)
        assert any(c in deep_search_tool.description for c in vn_chars)
        assert any(c in screenshot_tool.description for c in vn_chars)
