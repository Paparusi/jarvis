"""Tests for Discovery Tools — ProjectDiscovery binary wrappers.

Tests the 5 PD binary tool wrappers (subfinder, httpx, katana, gau, ffuf)
in src/tools/discovery.py with mocked subprocess calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.base import ToolDefinition, ToolResult
from src.tools.discovery import (
    subfinder_enum_tool,
    httpx_probe_tool,
    katana_crawl_tool,
    gau_urls_tool,
    ffuf_fuzz_tool,
    subfinder_enum,
    httpx_probe,
    katana_crawl,
    gau_urls,
    ffuf_fuzz,
    _validate_domain,
    _validate_url,
    _run_binary,
    _parse_jsonl,
    _DANGEROUS_CHARS,
    BIN_DIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc_mock(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Create an AsyncMock mimicking asyncio.create_subprocess_exec return."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout, stderr))
    mock_proc.returncode = returncode
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()
    return mock_proc


def _patch_binary_exists(name: str):
    """Patch _get_binary to pretend the binary exists."""
    return patch(
        "src.tools.discovery._get_binary",
        return_value=os.path.join(BIN_DIR, name),
    )


def _patch_binary_missing():
    """Patch _get_binary to pretend the binary does NOT exist."""
    return patch("src.tools.discovery._get_binary", return_value=None)


# ---------------------------------------------------------------------------
# 1. subfinder_enum
# ---------------------------------------------------------------------------

class TestSubfinderEnum:
    @pytest.mark.asyncio
    async def test_subfinder_success(self):
        """Mock subprocess returning JSONL with 3 subdomains, verify parsed output."""
        stdout = (
            b'{"host":"api.example.com","source":"crtsh"}\n'
            b'{"host":"www.example.com","source":"hackertarget"}\n'
            b'{"host":"mail.example.com","source":"crtsh"}\n'
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("subfinder"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await subfinder_enum_tool.handler(domain="example.com")

        assert result.success is True
        assert result.data["count"] == 3
        assert "api.example.com" in result.data["subdomains"]
        assert "www.example.com" in result.data["subdomains"]
        assert "mail.example.com" in result.data["subdomains"]
        assert result.data["sources"]["crtsh"] == 2
        assert result.data["sources"]["hackertarget"] == 1
        assert "3 unique subdomains" in result.output

    @pytest.mark.asyncio
    async def test_subfinder_empty_result(self):
        """No output lines should return success with 0 subdomains."""
        mock_proc = _make_proc_mock(stdout=b"")

        with _patch_binary_exists("subfinder"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await subfinder_enum_tool.handler(domain="example.com")

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["subdomains"] == []

    @pytest.mark.asyncio
    async def test_subfinder_invalid_domain(self):
        """Injection characters in domain should be rejected."""
        result = await subfinder_enum_tool.handler(domain="example.com; rm -rf /")
        assert result.success is False
        assert "disallowed" in result.error.lower() or "Invalid" in result.error

    @pytest.mark.asyncio
    async def test_subfinder_timeout(self):
        """Subprocess timeout should return a timeout error."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with _patch_binary_exists("subfinder"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await subfinder_enum_tool.handler(domain="example.com")

        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_subfinder_binary_missing(self):
        """When binary is not found, should return an error."""
        with _patch_binary_missing():
            result = await subfinder_enum_tool.handler(domain="example.com")

        assert result.success is False
        assert "not found" in result.error.lower()
        assert "subfinder" in result.error


# ---------------------------------------------------------------------------
# 2. httpx_probe
# ---------------------------------------------------------------------------

class TestHttpxProbe:
    @pytest.mark.asyncio
    async def test_httpx_success(self):
        """Mock JSONL output with status_code, title, tech."""
        stdout = (
            b'{"url":"https://api.example.com","status_code":200,'
            b'"title":"API","tech":["Nginx","Go"],"content_length":1234}\n'
            b'{"url":"https://www.example.com","status_code":301,'
            b'"title":"Redirect","tech":[],"content_length":0}\n'
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("httpx"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await httpx_probe_tool.handler(
                targets="api.example.com\nwww.example.com"
            )

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["alive"][0]["url"] == "https://api.example.com"
        assert result.data["alive"][0]["status_code"] == 200
        assert result.data["alive"][0]["tech"] == ["Nginx", "Go"]
        assert "Alive: 2" in result.output

    @pytest.mark.asyncio
    async def test_httpx_empty_targets(self):
        """Empty string input should return an error."""
        result = await httpx_probe_tool.handler(targets="")
        assert result.success is False
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_httpx_timeout(self):
        """Subprocess timeout should return an error."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with _patch_binary_exists("httpx"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await httpx_probe_tool.handler(targets="example.com")

        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_httpx_temp_file_cleanup(self):
        """Verify temp file is cleaned up after httpx runs."""
        mock_proc = _make_proc_mock(stdout=b'{"url":"https://example.com","status_code":200}\n')
        created_files = []

        original_named_temp = __import__("tempfile").NamedTemporaryFile

        def tracking_temp(*args, **kwargs):
            tmp = original_named_temp(*args, **kwargs)
            created_files.append(tmp.name)
            return tmp

        with _patch_binary_exists("httpx"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.tempfile.NamedTemporaryFile", side_effect=tracking_temp):
            result = await httpx_probe_tool.handler(targets="example.com")

        assert result.success is True
        # All temp files should have been cleaned up
        for f in created_files:
            assert not os.path.exists(f), f"Temp file was not cleaned up: {f}"


# ---------------------------------------------------------------------------
# 3. katana_crawl
# ---------------------------------------------------------------------------

class TestKatanaCrawl:
    @pytest.mark.asyncio
    async def test_katana_success(self):
        """Mock JSONL with URLs, JS files, endpoints."""
        stdout = (
            b'{"request":{"endpoint":"https://example.com/page1"}}\n'
            b'{"request":{"endpoint":"https://example.com/app.js"}}\n'
            b'{"request":{"endpoint":"https://example.com/api/v1/users"}}\n'
            b'{"request":{"endpoint":"https://example.com/style.css"}}\n'
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("katana"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await katana_crawl_tool.handler(url="https://example.com")

        assert result.success is True
        assert result.data["url_count"] == 4
        assert result.data["js_count"] == 1
        assert "https://example.com/app.js" in result.data["js_files"]
        assert "https://example.com/api/v1/users" in result.data["endpoints"]

    @pytest.mark.asyncio
    async def test_katana_with_depth(self):
        """Verify depth parameter is passed to the binary."""
        mock_proc = _make_proc_mock(stdout=b"")
        created_args = []

        async def capture_exec(*args, **kwargs):
            created_args.extend(args)
            return mock_proc

        with _patch_binary_exists("katana"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", side_effect=capture_exec):
            result = await katana_crawl_tool.handler(
                url="https://example.com", depth=4
            )

        assert result.success is True
        # Verify -d 4 was passed
        assert "-d" in created_args
        d_index = created_args.index("-d")
        assert created_args[d_index + 1] == "4"

    @pytest.mark.asyncio
    async def test_katana_js_file_detection(self):
        """Test JS file classification for .js and .mjs extensions."""
        stdout = (
            b'{"endpoint":"https://example.com/bundle.js"}\n'
            b'{"endpoint":"https://example.com/module.mjs"}\n'
            b'{"endpoint":"https://example.com/script.js?v=123"}\n'
            b'{"endpoint":"https://example.com/page.html"}\n'
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("katana"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await katana_crawl_tool.handler(url="https://example.com")

        assert result.success is True
        # .js, .mjs, and .js?query should all be classified as JS
        assert result.data["js_count"] == 3
        assert "https://example.com/bundle.js" in result.data["js_files"]
        assert "https://example.com/module.mjs" in result.data["js_files"]
        assert "https://example.com/script.js?v=123" in result.data["js_files"]

    @pytest.mark.asyncio
    async def test_katana_timeout(self):
        """Subprocess timeout should return an error."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with _patch_binary_exists("katana"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await katana_crawl_tool.handler(url="https://example.com")

        assert result.success is False
        assert "timed out" in result.error.lower()


# ---------------------------------------------------------------------------
# 4. gau_urls
# ---------------------------------------------------------------------------

class TestGauUrls:
    @pytest.mark.asyncio
    async def test_gau_success(self):
        """Mock stdout with URLs, verify parsing."""
        stdout = (
            b"https://example.com/page1\n"
            b"https://example.com/page2\n"
            b"https://example.com/api/endpoint\n"
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("gau"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await gau_urls_tool.handler(domain="example.com")

        assert result.success is True
        assert result.data["url_count"] == 3
        assert "https://example.com/page1" in result.data["urls"]
        assert "https://example.com/api/endpoint" in result.data["urls"]

    @pytest.mark.asyncio
    async def test_gau_filters_static(self):
        """Images/CSS/fonts should be filtered out."""
        stdout = (
            b"https://example.com/page\n"
            b"https://example.com/logo.png\n"
            b"https://example.com/style.css\n"
            b"https://example.com/font.woff2\n"
            b"https://example.com/icon.ico\n"
            b"https://example.com/photo.jpg\n"
            b"https://example.com/image.gif\n"
            b"https://example.com/bg.svg\n"
            b"https://example.com/real-page\n"
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("gau"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await gau_urls_tool.handler(domain="example.com")

        assert result.success is True
        # Only non-static URLs should remain
        assert result.data["url_count"] == 2
        assert "https://example.com/page" in result.data["urls"]
        assert "https://example.com/real-page" in result.data["urls"]
        # Static assets should NOT be present
        for url in result.data["urls"]:
            assert not url.endswith((".png", ".css", ".woff2", ".ico", ".jpg", ".gif", ".svg"))

    @pytest.mark.asyncio
    async def test_gau_js_detection(self):
        """JS files should be separated into js_files list."""
        stdout = (
            b"https://example.com/app.js\n"
            b"https://example.com/vendor.mjs\n"
            b"https://example.com/page\n"
        )
        mock_proc = _make_proc_mock(stdout=stdout)

        with _patch_binary_exists("gau"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await gau_urls_tool.handler(domain="example.com")

        assert result.success is True
        assert result.data["js_count"] == 2
        assert "https://example.com/app.js" in result.data["js_files"]
        assert "https://example.com/vendor.mjs" in result.data["js_files"]
        # JS files should NOT be in the urls list
        assert "https://example.com/app.js" not in result.data["urls"]
        assert result.data["url_count"] == 1

    @pytest.mark.asyncio
    async def test_gau_timeout(self):
        """Subprocess timeout should return an error."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with _patch_binary_exists("gau"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await gau_urls_tool.handler(domain="example.com")

        assert result.success is False
        assert "timed out" in result.error.lower()


# ---------------------------------------------------------------------------
# 5. ffuf_fuzz
# ---------------------------------------------------------------------------

class TestFfufFuzz:
    @pytest.mark.asyncio
    async def test_ffuf_success(self):
        """Mock JSON output with results."""
        ffuf_output = json.dumps({
            "results": [
                {"url": "https://example.com/admin", "status": 200, "length": 5000,
                 "input": {"FUZZ": "admin"}},
                {"url": "https://example.com/.env", "status": 200, "length": 120,
                 "input": {"FUZZ": ".env"}},
                {"url": "https://example.com/backup", "status": 403, "length": 0,
                 "input": {"FUZZ": "backup"}},
            ]
        })

        mock_proc = _make_proc_mock(stdout=b"")

        # We need to mock the output file reading since ffuf writes to a file
        with _patch_binary_exists("ffuf"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("builtins.open", create=True) as mock_open, \
             patch("src.tools.discovery.os.path.isfile", return_value=True), \
             patch("src.tools.discovery.os.path.exists", return_value=True), \
             patch("src.tools.discovery.os.unlink"):
            # Mock the output file read
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            mock_open.return_value.read = MagicMock(return_value=ffuf_output)

            result = await ffuf_fuzz_tool.handler(url="https://example.com")

        assert result.success is True
        assert result.data["count"] == 3
        assert any(e["path"] == "/admin" for e in result.data["found"])

    @pytest.mark.asyncio
    async def test_ffuf_custom_wordlist(self):
        """Test wordlist parameter with a non-default wordlist."""
        mock_proc = _make_proc_mock(stdout=b"")

        with _patch_binary_exists("ffuf"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.os.path.isfile", return_value=False), \
             patch("src.tools.discovery.os.path.exists", return_value=True), \
             patch("src.tools.discovery.os.unlink"):
            result = await ffuf_fuzz_tool.handler(
                url="https://example.com", wordlist="small"
            )

        assert result.success is True
        assert "small" in result.output

    @pytest.mark.asyncio
    async def test_ffuf_default_wordlist(self):
        """Default 'bounty' wordlist should be used when none specified."""
        mock_proc = _make_proc_mock(stdout=b"")

        with _patch_binary_exists("ffuf"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.os.path.isfile", return_value=False), \
             patch("src.tools.discovery.os.path.exists", return_value=True), \
             patch("src.tools.discovery.os.unlink"):
            result = await ffuf_fuzz_tool.handler(url="https://example.com")

        assert result.success is True
        assert "bounty" in result.output

    @pytest.mark.asyncio
    async def test_ffuf_timeout(self):
        """Subprocess timeout should return an error."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with _patch_binary_exists("ffuf"), \
             patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.asyncio.wait_for", side_effect=asyncio.TimeoutError()), \
             patch("src.tools.discovery.os.path.exists", return_value=False):
            result = await ffuf_fuzz_tool.handler(url="https://example.com")

        assert result.success is False
        assert "timed out" in result.error.lower()

    def test_ffuf_requires_confirmation(self):
        """Verify the tool has requires_confirmation=True."""
        assert ffuf_fuzz_tool.requires_confirmation is True


# ---------------------------------------------------------------------------
# General / Helpers
# ---------------------------------------------------------------------------

class TestRunBinary:
    @pytest.mark.asyncio
    async def test_run_binary_stderr_on_failure(self):
        """Test that _run_binary returns stderr on non-zero return code."""
        mock_proc = _make_proc_mock(
            stdout=b"some output",
            stderr=b"error: something went wrong",
            returncode=1,
        )

        with patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc):
            stdout, stderr, rc = await _run_binary(["/usr/bin/fake"], timeout=10)

        assert stdout == "some output"
        assert stderr == "error: something went wrong"
        assert rc == 1

    @pytest.mark.asyncio
    async def test_run_binary_timeout_kills_process(self):
        """Test that _run_binary kills the process on timeout."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.stdin = None

        with patch("src.tools.discovery.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("src.tools.discovery.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            with pytest.raises(asyncio.TimeoutError):
                await _run_binary(["/usr/bin/fake"], timeout=1)


class TestAllToolsRegistered:
    def test_all_tools_registered(self):
        """Import ALL_TOOLS and verify all 5 discovery tools are present."""
        from src.tools.registry_all import ALL_TOOLS

        tool_names = {t.name for t in ALL_TOOLS}
        expected = {
            "subfinder_enum",
            "httpx_probe",
            "katana_crawl",
            "gau_urls",
            "ffuf_fuzz",
        }
        for name in expected:
            assert name in tool_names, f"Tool '{name}' not found in ALL_TOOLS"

    def test_all_5_tool_definitions_exist(self):
        """Verify all 5 tool definitions are proper ToolDefinition instances."""
        tools = [
            subfinder_enum_tool,
            httpx_probe_tool,
            katana_crawl_tool,
            gau_urls_tool,
            ffuf_fuzz_tool,
        ]
        assert len(tools) == 5
        for tool in tools:
            assert isinstance(tool, ToolDefinition)
            assert tool.name
            assert tool.description
            assert tool.handler is not None
            assert tool.timeout_seconds > 0
            assert len(tool.parameters) >= 1


class TestInputSanitization:
    """All tools should reject command injection characters."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("char", [";", "|", "&", "$", "`", "(", ")"])
    async def test_subfinder_rejects_injection(self, char):
        result = await subfinder_enum(domain=f"example.com{char}whoami")
        assert result.success is False
        assert "disallowed" in result.error.lower() or "Invalid" in result.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("char", [";", "|", "`", "$"])
    async def test_httpx_rejects_injection(self, char):
        result = await httpx_probe(targets=f"example.com{char}whoami")
        assert result.success is False
        assert "disallowed" in result.error.lower() or "Invalid" in result.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("char", [";", "|", "`", "$"])
    async def test_katana_rejects_injection(self, char):
        result = await katana_crawl(url=f"https://example.com/{char}whoami")
        assert result.success is False
        assert "disallowed" in result.error.lower() or "Invalid" in result.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("char", [";", "|", "&", "$", "`", "(", ")"])
    async def test_gau_rejects_injection(self, char):
        result = await gau_urls(domain=f"example.com{char}whoami")
        assert result.success is False
        assert "disallowed" in result.error.lower() or "Invalid" in result.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("char", [";", "|", "`", "$"])
    async def test_ffuf_rejects_injection(self, char):
        result = await ffuf_fuzz(url=f"https://example.com/{char}whoami")
        assert result.success is False
        assert "disallowed" in result.error.lower() or "Invalid" in result.error


class TestValidationHelpers:
    """Tests for the internal validation functions."""

    def test_validate_domain_valid(self):
        assert _validate_domain("example.com") is None
        assert _validate_domain("sub.example.com") is None
        assert _validate_domain("a-b.example.com") is None

    def test_validate_domain_empty(self):
        assert _validate_domain("") is not None
        assert _validate_domain("  ") is not None

    def test_validate_domain_injection(self):
        assert _validate_domain("example.com; ls") is not None
        assert _validate_domain("example.com|cat /etc/passwd") is not None
        assert _validate_domain("$(whoami).example.com") is not None

    def test_validate_url_valid(self):
        assert _validate_url("https://example.com") is None
        assert _validate_url("http://example.com/path") is None

    def test_validate_url_invalid(self):
        assert _validate_url("") is not None
        assert _validate_url("ftp://example.com") is not None
        assert _validate_url("https://example.com;ls") is not None

    def test_parse_jsonl_valid(self):
        raw = '{"a":1}\n{"b":2}\n'
        result = _parse_jsonl(raw)
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"b": 2}

    def test_parse_jsonl_skips_invalid(self):
        raw = '{"a":1}\nnot json\n{"b":2}\n'
        result = _parse_jsonl(raw)
        assert len(result) == 2

    def test_parse_jsonl_empty(self):
        assert _parse_jsonl("") == []
        assert _parse_jsonl("\n\n") == []


class TestToolDefinitionProperties:
    """Verify ToolDefinition properties for all 5 tools."""

    def test_subfinder_tool_properties(self):
        assert subfinder_enum_tool.name == "subfinder_enum"
        assert subfinder_enum_tool.handler is subfinder_enum
        assert subfinder_enum_tool.requires_confirmation is False

    def test_httpx_tool_properties(self):
        assert httpx_probe_tool.name == "httpx_probe"
        assert httpx_probe_tool.handler is httpx_probe
        assert httpx_probe_tool.requires_confirmation is False

    def test_katana_tool_properties(self):
        assert katana_crawl_tool.name == "katana_crawl"
        assert katana_crawl_tool.handler is katana_crawl
        assert katana_crawl_tool.requires_confirmation is False
        # Should have optional depth parameter
        param_names = [p.name for p in katana_crawl_tool.parameters]
        assert "depth" in param_names
        depth_param = next(p for p in katana_crawl_tool.parameters if p.name == "depth")
        assert depth_param.required is False
        assert depth_param.default == 2

    def test_gau_tool_properties(self):
        assert gau_urls_tool.name == "gau_urls"
        assert gau_urls_tool.handler is gau_urls
        assert gau_urls_tool.requires_confirmation is False

    def test_ffuf_tool_properties(self):
        assert ffuf_fuzz_tool.name == "ffuf_fuzz"
        assert ffuf_fuzz_tool.handler is ffuf_fuzz
        assert ffuf_fuzz_tool.requires_confirmation is True
        # Should have optional wordlist parameter
        param_names = [p.name for p in ffuf_fuzz_tool.parameters]
        assert "wordlist" in param_names
        wordlist_param = next(p for p in ffuf_fuzz_tool.parameters if p.name == "wordlist")
        assert wordlist_param.required is False
        assert wordlist_param.default == "bounty"
        assert wordlist_param.enum is not None
        assert "bounty" in wordlist_param.enum
