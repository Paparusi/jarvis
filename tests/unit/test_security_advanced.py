"""Tests for src.tools.security_advanced — high-value bug bounty tools."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.security_advanced import (
    subdomain_takeover_check,
    js_secrets_scan,
    open_redirect_test,
    nuclei_scan,
    _TAKEOVER_FINGERPRINTS,
    _SECRET_PATTERNS,
    _REDIRECT_PARAMS,
    subdomain_takeover_tool,
    js_secrets_scan_tool,
    open_redirect_tool,
    nuclei_scan_tool,
)
from src.tools.base import ToolResult


# ---------------------------------------------------------------------------
# Subdomain Takeover
# ---------------------------------------------------------------------------

class TestSubdomainTakeover:
    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await subdomain_takeover_check(subdomains="")
        assert result.success is False
        assert "empty" in result.error.lower() or "no subdomains" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_cname_found(self):
        """Subdomain with no CNAME should not be flagged."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await subdomain_takeover_check(subdomains="safe.example.com")
        assert result.success is True
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_takeover_detected(self):
        """Subdomain with dangling CNAME to S3 should be flagged."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"old-bucket.s3.amazonaws.com.\n", b"")
        )
        mock_proc.returncode = 0

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "NoSuchBucket"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await subdomain_takeover_check(subdomains="vuln.example.com")

        assert result.success is True
        assert result.data["count"] >= 1
        assert len(result.data["vulnerable"]) >= 1
        vuln = result.data["vulnerable"][0]
        assert vuln["service"] == "AWS S3"

    @pytest.mark.asyncio
    async def test_multiple_subdomains(self):
        """Should handle multiple comma-separated subdomains."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await subdomain_takeover_check(subdomains="a.example.com,b.example.com,c.example.com")
        assert result.success is True
        assert result.data["subdomains_checked"] == 3

    @pytest.mark.asyncio
    async def test_dig_timeout(self):
        """Timeout on dig should not crash, just skip."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.returncode = 1
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await subdomain_takeover_check(subdomains="timeout.example.com")
        assert result.success is True
        assert result.data["count"] == 0

    def test_fingerprints_populated(self):
        """Should have multiple takeover fingerprints."""
        assert len(_TAKEOVER_FINGERPRINTS) >= 20
        # Check key services exist
        for svc in ["s3.amazonaws.com", "herokuapp.com", "github.io"]:
            assert svc in _TAKEOVER_FINGERPRINTS

    def test_tool_definition(self):
        assert subdomain_takeover_tool.name == "subdomain_takeover"
        assert subdomain_takeover_tool.timeout_seconds == 120


# ---------------------------------------------------------------------------
# JS Secrets Scan
# ---------------------------------------------------------------------------

class TestJsSecretsScan:
    @pytest.mark.asyncio
    async def test_no_scripts(self):
        """Page with no script tags should find no secrets."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Hello</body></html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await js_secrets_scan(url="https://example.com")
        assert result.success is True
        assert result.data["secrets_found"] is False

    @pytest.mark.asyncio
    async def test_aws_key_found(self):
        """Should detect AWS access key in JS file."""
        html = '<html><script src="/app.js"></script></html>'
        js_content = 'var key = "AKIAIOSFODNN7EXAMPLE"; var secret = "wJalrXUtnFEMI";'

        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = html

        js_resp = MagicMock()
        js_resp.status_code = 200
        js_resp.text = js_content
        js_resp.headers = {"content-length": "100"}

        mock_client = AsyncMock()
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return html_resp
            return js_resp

        mock_client.get = AsyncMock(side_effect=side_effect)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await js_secrets_scan(url="https://example.com")
        assert result.success is True
        assert result.data["secrets_found"] is True
        assert any("aws" in f["pattern"].lower() for f in result.data["findings"])

    @pytest.mark.asyncio
    async def test_relative_url_resolved(self):
        """Script src with relative URL should be resolved properly."""
        html = '<html><script src="./static/app.js"></script></html>'

        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = html

        js_resp = MagicMock()
        js_resp.status_code = 200
        js_resp.text = 'console.log("clean");'
        js_resp.headers = {"content-length": "30"}

        mock_client = AsyncMock()
        call_count = 0

        async def side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return html_resp
            assert "static/app.js" in url
            return js_resp

        mock_client.get = AsyncMock(side_effect=side_effect)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await js_secrets_scan(url="https://example.com/page")
        assert result.success is True

    def test_secret_patterns_populated(self):
        """Should have multiple secret patterns."""
        assert len(_SECRET_PATTERNS) >= 15
        # Key patterns exist (names use title case with spaces)
        pattern_names_lower = [n.lower() for n in _SECRET_PATTERNS]
        for keyword in ["aws", "github", "stripe", "jwt"]:
            assert any(keyword in n for n in pattern_names_lower)

    def test_tool_definition(self):
        assert js_secrets_scan_tool.name == "js_secrets_scan"
        assert js_secrets_scan_tool.timeout_seconds == 60


# ---------------------------------------------------------------------------
# Open Redirect
# ---------------------------------------------------------------------------

class TestOpenRedirect:
    @pytest.mark.asyncio
    async def test_no_redirect(self):
        """No redirect found should return vulnerable=False."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = "<html>OK</html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await open_redirect_test(url="https://example.com")
        assert result.success is True
        assert result.data["vulnerable"] is False

    @pytest.mark.asyncio
    async def test_redirect_found(self):
        """302 redirect to evil.com should be flagged."""
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "https://evil.com"}
        mock_resp.text = ""

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await open_redirect_test(url="https://example.com")
        assert result.success is True
        assert result.data["vulnerable"] is True
        assert len(result.data["findings"]) >= 1

    @pytest.mark.asyncio
    async def test_meta_refresh_redirect(self):
        """Meta http-equiv refresh with evil.com should be detected."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = '<meta http-equiv="refresh" content="0;url=https://evil.com">'

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await open_redirect_test(url="https://example.com")
        assert result.success is True
        assert result.data["vulnerable"] is True

    def test_redirect_params_populated(self):
        assert len(_REDIRECT_PARAMS) >= 20
        for param in ["redirect", "next", "url", "callback", "return"]:
            assert param in _REDIRECT_PARAMS

    def test_tool_definition(self):
        assert open_redirect_tool.name == "open_redirect_test"
        assert open_redirect_tool.timeout_seconds == 90


# ---------------------------------------------------------------------------
# Nuclei Scan
# ---------------------------------------------------------------------------

class TestNucleiScan:
    @pytest.mark.asyncio
    async def test_nuclei_not_installed(self):
        """Should return error when nuclei binary is not found."""
        with patch("os.path.isfile", return_value=False):
            result = await nuclei_scan(url="https://example.com")
        assert result.success is False
        assert "nuclei" in result.error.lower() or "not found" in result.error.lower() or "not installed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_nuclei_findings_parsed(self):
        """Should parse nuclei JSONL output into findings."""
        jsonl = json.dumps({
            "info": {"name": "Apache HTTPD Version", "severity": "info"},
            "template-id": "apache-detect",
            "matched-at": "https://example.com",
        }) + "\n" + json.dumps({
            "info": {"name": "Open Redirect", "severity": "medium", "description": "Redirect found"},
            "template-id": "open-redirect",
            "matched-at": "https://example.com/redir?url=evil.com",
        }) + "\n"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(jsonl.encode(), b""))
        mock_proc.returncode = 0

        with patch("os.path.isfile", return_value=True), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await nuclei_scan(url="https://example.com")

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["findings"][0]["template_id"] == "apache-detect"
        assert result.data["findings"][1]["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_nuclei_no_findings(self):
        """Should handle clean scan with no findings."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("os.path.isfile", return_value=True), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await nuclei_scan(url="https://example.com")

        assert result.success is True
        assert result.data["count"] == 0

    def test_tool_definition(self):
        assert nuclei_scan_tool.name == "nuclei_scan"
        assert nuclei_scan_tool.timeout_seconds == 300


# ---------------------------------------------------------------------------
# Scanner Integration (extract_findings for new tools)
# ---------------------------------------------------------------------------

class TestScannerExtractFindings:
    """Test VulnScanner._extract_findings() for new tool types."""

    def setup_method(self):
        from src.bounty.scanner import VulnScanner
        reg = MagicMock()
        self.scanner = VulnScanner(reg)

    def test_js_secrets_finding(self):
        tr = ToolResult(
            success=True, output="found",
            data={
                "secrets_found": True,
                "findings": [
                    {"pattern": "AWS Access Key", "match": "AKIAIOSFODNN7EXAMPLE", "source": "/app.js"},
                    {"pattern": "Stripe Secret Key", "match": "sk_live_xxx", "source": "/checkout.js"},
                ],
            },
        )
        findings = self.scanner._extract_findings("js_secrets_scan", tr, "https://test.com")
        assert len(findings) == 2
        assert findings[0].vuln_type == "js_secrets"
        assert findings[0].severity == "CRITICAL"  # AWS key
        assert findings[1].severity == "HIGH"  # Stripe

    def test_js_secrets_no_secrets(self):
        tr = ToolResult(success=True, output="clean", data={"secrets_found": False, "findings": []})
        findings = self.scanner._extract_findings("js_secrets_scan", tr, "https://test.com")
        assert len(findings) == 0

    def test_open_redirect_finding(self):
        tr = ToolResult(
            success=True, output="vulnerable",
            data={
                "vulnerable": True,
                "findings": [
                    {"param": "redirect", "payload": "//evil.com", "status": 302, "location": "//evil.com"},
                ],
            },
        )
        findings = self.scanner._extract_findings("open_redirect_test", tr, "https://test.com")
        assert len(findings) == 1
        assert findings[0].vuln_type == "open_redirect"
        assert findings[0].severity == "MEDIUM"

    def test_open_redirect_not_vulnerable(self):
        tr = ToolResult(success=True, output="safe", data={"vulnerable": False})
        findings = self.scanner._extract_findings("open_redirect_test", tr, "https://test.com")
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Registry Integration
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    def test_all_tools_registered(self):
        from src.tools.registry_all import ALL_TOOLS
        names = [t.name for t in ALL_TOOLS]
        assert "subdomain_takeover" in names
        assert "js_secrets_scan" in names
        assert "open_redirect_test" in names
        assert "nuclei_scan" in names

    def test_bounty_wordlist_exists(self):
        from src.tools.web_attack import _WORDLISTS
        assert "bounty" in _WORDLISTS
        assert len(_WORDLISTS["bounty"]) >= 100


import asyncio
