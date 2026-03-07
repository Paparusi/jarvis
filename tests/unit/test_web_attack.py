"""Tests for Web Attack Tools — pentest/security testing."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.web_attack import (
    _validate_url,
    _WORDLISTS,
    _SQLI_PAYLOADS,
    _SQLI_ERROR_PATTERNS,
    _XSS_PAYLOADS,
    _WAF_SIGNATURES,
    _LFI_PAYLOADS,
    _LFI_SUCCESS_PATTERNS,
    _AUDIT_HEADERS,
    dir_bruteforce,
    sqli_test,
    xss_scan,
    cors_check,
    waf_detect,
    lfi_test,
    header_audit,
    dir_bruteforce_tool,
    sqli_test_tool,
    xss_scan_tool,
    cors_check_tool,
    waf_detect_tool,
    lfi_test_tool,
    header_audit_tool,
)


# ---------------------------------------------------------------------------
# URL Validation / SSRF Protection
# ---------------------------------------------------------------------------

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

    def test_10_network_blocked(self):
        result = _validate_url("http://10.0.0.1")
        assert result is not None
        assert "Blocked" in result

    def test_172_16_blocked(self):
        result = _validate_url("http://172.16.0.1")
        assert result is not None
        assert "Blocked" in result

    def test_disallowed_chars(self):
        assert _validate_url("http://example.com;ls") is not None
        assert _validate_url("http://example.com|cat") is not None

    def test_missing_netloc(self):
        result = _validate_url("http://")
        assert result is not None


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_all_tools_defined(self):
        tools = [
            dir_bruteforce_tool, sqli_test_tool, xss_scan_tool,
            cors_check_tool, waf_detect_tool, lfi_test_tool,
            header_audit_tool,
        ]
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.handler is not None
            assert tool.timeout_seconds > 0

    def test_tool_names(self):
        assert dir_bruteforce_tool.name == "dir_bruteforce"
        assert sqli_test_tool.name == "sqli_test"
        assert xss_scan_tool.name == "xss_scan"
        assert cors_check_tool.name == "cors_check"
        assert waf_detect_tool.name == "waf_detect"
        assert lfi_test_tool.name == "lfi_test"
        assert header_audit_tool.name == "header_audit"

    def test_descriptions_in_vietnamese(self):
        tools = [
            dir_bruteforce_tool, sqli_test_tool, xss_scan_tool,
            cors_check_tool, waf_detect_tool, lfi_test_tool,
            header_audit_tool,
        ]
        for tool in tools:
            # All descriptions should mention authorized use in Vietnamese
            assert "CTF" in tool.description or "pentest" in tool.description

    def test_tool_parameters(self):
        # dir_bruteforce has url + wordlist
        param_names = [p.name for p in dir_bruteforce_tool.parameters]
        assert "url" in param_names
        assert "wordlist" in param_names

        # sqli_test has url + method + params
        param_names = [p.name for p in sqli_test_tool.parameters]
        assert "url" in param_names
        assert "method" in param_names
        assert "params" in param_names

        # lfi_test has url + param
        param_names = [p.name for p in lfi_test_tool.parameters]
        assert "url" in param_names
        assert "param" in param_names


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

class TestWordlists:
    def test_all_wordlists_present(self):
        assert "common" in _WORDLISTS
        assert "small" in _WORDLISTS
        assert "medium" in _WORDLISTS
        assert "api" in _WORDLISTS
        assert "backup" in _WORDLISTS

    def test_wordlists_not_empty(self):
        for name, paths in _WORDLISTS.items():
            assert len(paths) > 0, f"Wordlist '{name}' is empty"

    def test_common_wordlist_has_expected_paths(self):
        paths = _WORDLISTS["common"]
        assert "/admin" in paths
        assert "/.env" in paths
        assert "/robots.txt" in paths

    def test_api_wordlist_has_api_paths(self):
        paths = _WORDLISTS["api"]
        assert "/api/v1" in paths
        assert "/swagger" in paths
        assert "/graphql" in paths

    def test_all_paths_start_with_slash(self):
        for name, paths in _WORDLISTS.items():
            for path in paths:
                assert path.startswith("/") or path.startswith("."), \
                    f"Path '{path}' in wordlist '{name}' does not start with /"


class TestSqliPatterns:
    def test_payloads_not_empty(self):
        assert len(_SQLI_PAYLOADS) > 0

    def test_error_patterns_have_db_type(self):
        for pattern, db_type in _SQLI_ERROR_PATTERNS:
            assert db_type in ("MySQL", "PostgreSQL", "SQLite", "MSSQL", "Oracle", "Generic SQL")

    def test_covers_major_databases(self):
        db_types = {db for _, db in _SQLI_ERROR_PATTERNS}
        assert "MySQL" in db_types
        assert "PostgreSQL" in db_types
        assert "SQLite" in db_types
        assert "MSSQL" in db_types


class TestWafSignatures:
    def test_major_wafs_present(self):
        assert "Cloudflare" in _WAF_SIGNATURES
        assert "AWS WAF" in _WAF_SIGNATURES
        assert "ModSecurity" in _WAF_SIGNATURES
        assert "Sucuri" in _WAF_SIGNATURES

    def test_signature_structure(self):
        for waf_name, sigs in _WAF_SIGNATURES.items():
            assert "headers" in sigs, f"WAF '{waf_name}' missing headers"
            assert "cookies" in sigs, f"WAF '{waf_name}' missing cookies"
            assert "body_patterns" in sigs, f"WAF '{waf_name}' missing body_patterns"


# ---------------------------------------------------------------------------
# Helper to create mock httpx responses
# ---------------------------------------------------------------------------

class _MockHeaders:
    """Mock httpx Headers that supports .get(), .items(), .raw, and .get_list()."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def items(self):
        return self._data.items()

    @property
    def raw(self):
        return [(k.lower().encode(), v.encode()) for k, v in self._data.items()]

    def get_list(self, key: str) -> list[str]:
        return [v for k, v in self._data.items() if k.lower() == key.lower()]


def _mock_response(
    status_code: int = 200,
    text: str = "",
    headers: dict | None = None,
    cookies: dict | None = None,
) -> MagicMock:
    """Create a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = text.encode()
    resp.headers = _MockHeaders(headers)
    resp.cookies = cookies or {}
    return resp


def _mock_client(responses=None, side_effect=None):
    """Create a mock httpx.AsyncClient context manager."""
    client = AsyncMock()
    if responses is not None:
        if isinstance(responses, list):
            client.get = AsyncMock(side_effect=responses)
            client.head = AsyncMock(side_effect=responses)
            client.post = AsyncMock(side_effect=responses)
        else:
            client.get = AsyncMock(return_value=responses)
            client.head = AsyncMock(return_value=responses)
            client.post = AsyncMock(return_value=responses)
    if side_effect:
        client.get = AsyncMock(side_effect=side_effect)
        client.head = AsyncMock(side_effect=side_effect)
    return client


# ---------------------------------------------------------------------------
# 1. Dir Bruteforce Tests
# ---------------------------------------------------------------------------

class TestDirBruteforce:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await dir_bruteforce("")
        assert not result.success
        assert result.error

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await dir_bruteforce("http://127.0.0.1")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_invalid_wordlist(self):
        result = await dir_bruteforce("https://example.com", wordlist="nonexistent")
        assert not result.success
        assert "Invalid wordlist" in result.error

    @pytest.mark.asyncio
    async def test_found_paths(self):
        resp_200 = _mock_response(status_code=200)
        resp_404 = _mock_response(status_code=404)
        resp_403 = _mock_response(status_code=403)

        call_count = 0

        async def mock_head(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/admin" in url:
                return resp_200
            if "/.env" in url:
                return resp_403
            return resp_404

        client = AsyncMock()
        client.head = mock_head

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dir_bruteforce("https://example.com", wordlist="small")

        assert result.success
        assert "Found" in result.output
        assert result.data["count"] > 0

    @pytest.mark.asyncio
    async def test_no_paths_found(self):
        resp_404 = _mock_response(status_code=404)

        client = AsyncMock()
        client.head = AsyncMock(return_value=resp_404)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dir_bruteforce("https://example.com", wordlist="small")

        assert result.success
        assert "No accessible paths found" in result.output
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_redirect_detected(self):
        resp_301 = _mock_response(status_code=301)

        client = AsyncMock()
        client.head = AsyncMock(return_value=resp_301)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dir_bruteforce("https://example.com", wordlist="small")

        assert result.success
        assert result.data["count"] > 0
        assert "REDIRECT" in result.output


# ---------------------------------------------------------------------------
# 2. SQLi Test Tests
# ---------------------------------------------------------------------------

class TestSqliTest:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await sqli_test("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await sqli_test("http://192.168.1.1/search?q=test")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_invalid_method(self):
        result = await sqli_test("https://example.com/search?q=test", method="DELETE")
        assert not result.success
        assert "Method must be GET or POST" in result.error

    @pytest.mark.asyncio
    async def test_no_params_get(self):
        result = await sqli_test("https://example.com/search")
        assert not result.success
        assert "No query parameters" in result.error

    @pytest.mark.asyncio
    async def test_no_params_post(self):
        result = await sqli_test("https://example.com/search", method="POST")
        assert not result.success
        assert "POST method requires params" in result.error

    @pytest.mark.asyncio
    async def test_vulnerable_detected(self):
        normal_resp = _mock_response(status_code=200, text="Normal page")
        error_resp = _mock_response(
            status_code=500,
            text="Warning: mysql_fetch_array() expects parameter 1 to be resource",
        )

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return normal_resp
            return error_resp

        client = AsyncMock()
        client.get = mock_get

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await sqli_test("https://example.com/search?q=test")

        assert result.success
        assert result.data["vulnerable"]
        assert "VULNERABLE" in result.output
        assert "MySQL" in result.output

    @pytest.mark.asyncio
    async def test_not_vulnerable(self):
        normal_resp = _mock_response(status_code=200, text="Normal page content")

        client = AsyncMock()
        client.get = AsyncMock(return_value=normal_resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await sqli_test("https://example.com/search?q=test")

        assert result.success
        assert not result.data["vulnerable"]
        assert "No SQL injection indicators" in result.output

    @pytest.mark.asyncio
    async def test_post_method(self):
        normal_resp = _mock_response(status_code=200, text="Normal page")
        error_resp = _mock_response(
            status_code=200,
            text="PostgreSQL ERROR: syntax error at or near",
        )

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return normal_resp

        async def mock_post(url, **kwargs):
            return error_resp

        client = AsyncMock()
        client.get = mock_get
        client.post = mock_post

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await sqli_test(
                "https://example.com/login",
                method="POST",
                params="username,password",
            )

        assert result.success
        assert result.data["vulnerable"]


# ---------------------------------------------------------------------------
# 3. XSS Scan Tests
# ---------------------------------------------------------------------------

class TestXssScan:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await xss_scan("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await xss_scan("http://10.0.0.1/search?q=test")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_no_params(self):
        result = await xss_scan("https://example.com/page")
        assert not result.success
        assert "No parameters" in result.error

    @pytest.mark.asyncio
    async def test_vulnerable_detected(self):
        async def mock_get(url, **kwargs):
            # Reflect all XSS payloads back unescaped (simulates vulnerable app)
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            q_value = params.get("q", [""])[0]
            return _mock_response(
                status_code=200,
                text=f"<html>Search results for: {q_value}</html>",
            )

        client = AsyncMock()
        client.get = mock_get

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await xss_scan("https://example.com/search?q=test")

        assert result.success
        assert result.data["vulnerable"]
        assert "VULNERABLE" in result.output

    @pytest.mark.asyncio
    async def test_not_vulnerable(self):
        resp = _mock_response(
            status_code=200,
            text="<html>Search results for: &lt;script&gt;alert(1)&lt;/script&gt;</html>",
        )

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await xss_scan("https://example.com/search?q=test")

        assert result.success
        assert not result.data["vulnerable"]
        assert "No reflected XSS detected" in result.output

    @pytest.mark.asyncio
    async def test_custom_params(self):
        resp = _mock_response(status_code=200, text="<html>Safe</html>")

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await xss_scan("https://example.com/search", params="q,name")

        assert result.success


# ---------------------------------------------------------------------------
# 4. CORS Check Tests
# ---------------------------------------------------------------------------

class TestCorsCheck:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await cors_check("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await cors_check("http://127.0.0.1")
        assert not result.success

    @pytest.mark.asyncio
    async def test_no_cors_headers(self):
        resp = _mock_response(status_code=200, headers={})

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cors_check("https://example.com")

        assert result.success
        assert not result.data["vulnerable"]
        assert "No CORS misconfigurations" in result.output

    @pytest.mark.asyncio
    async def test_wildcard_cors(self):
        resp = _mock_response(
            status_code=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cors_check("https://example.com")

        assert result.success
        assert result.data["vulnerable"]
        assert "wildcard_origin" in result.output

    @pytest.mark.asyncio
    async def test_origin_reflection(self):
        async def mock_get(url, headers=None, **kwargs):
            origin = headers.get("Origin", "") if headers else ""
            if origin and origin != "https://example.com":
                return _mock_response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Credentials": "true",
                    },
                )
            return _mock_response(status_code=200, headers={})

        client = AsyncMock()
        client.get = mock_get

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cors_check("https://example.com")

        assert result.success
        assert result.data["vulnerable"]
        assert "origin_reflection" in result.output

    @pytest.mark.asyncio
    async def test_null_origin(self):
        async def mock_get(url, headers=None, **kwargs):
            origin = headers.get("Origin", "") if headers else ""
            if origin == "null":
                return _mock_response(
                    status_code=200,
                    headers={"Access-Control-Allow-Origin": "null"},
                )
            return _mock_response(status_code=200, headers={})

        client = AsyncMock()
        client.get = mock_get

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cors_check("https://example.com")

        assert result.success
        assert result.data["vulnerable"]
        assert "null_origin" in result.output


# ---------------------------------------------------------------------------
# 5. WAF Detect Tests
# ---------------------------------------------------------------------------

class TestWafDetect:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await waf_detect("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await waf_detect("http://172.16.0.1")
        assert not result.success

    @pytest.mark.asyncio
    async def test_cloudflare_detected(self):
        resp = _mock_response(
            status_code=200,
            text="<html>normal page</html>",
            headers={"cf-ray": "abc123-LAX", "cf-cache-status": "HIT"},
            cookies={},
        )

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await waf_detect("https://example.com")

        assert result.success
        assert result.data["waf_detected"]
        assert "Cloudflare" in result.output

    @pytest.mark.asyncio
    async def test_no_waf_detected(self):
        resp = _mock_response(
            status_code=200,
            text="<html>normal page</html>",
            headers={"Server": "nginx"},
            cookies={},
        )

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await waf_detect("https://example.com")

        assert result.success
        assert not result.data["waf_detected"]
        assert "No WAF detected" in result.output

    @pytest.mark.asyncio
    async def test_blocked_responses_detected(self):
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response(status_code=200, text="<html>ok</html>", headers={}, cookies={})
            return _mock_response(status_code=403, text="<html>Blocked by ModSecurity</html>", headers={"mod_security": "enabled"}, cookies={})

        client = AsyncMock()
        client.get = mock_get

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await waf_detect("https://example.com")

        assert result.success
        assert result.data["waf_detected"]


# ---------------------------------------------------------------------------
# 6. LFI Test Tests
# ---------------------------------------------------------------------------

class TestLfiTest:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await lfi_test("", "file")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await lfi_test("http://127.0.0.1/page?file=home", "file")
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_param(self):
        result = await lfi_test("https://example.com/page?file=home", "")
        assert not result.success
        assert "must not be empty" in result.error

    @pytest.mark.asyncio
    async def test_vulnerable_detected(self):
        async def mock_get(url, **kwargs):
            # Check URL-decoded content (urlencode may encode / and .)
            from urllib.parse import parse_qs, unquote, urlparse
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            file_value = params.get("file", [""])[0]
            if "etc/passwd" in unquote(file_value) or "etc/passwd" in unquote(url):
                return _mock_response(
                    status_code=200,
                    text="root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin",
                )
            return _mock_response(status_code=200, text="<html>Normal page</html>")

        client = AsyncMock()
        client.get = mock_get

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await lfi_test("https://example.com/page?file=home", "file")

        assert result.success
        assert result.data["vulnerable"]
        assert "VULNERABLE" in result.output
        assert "etc/passwd" in result.output

    @pytest.mark.asyncio
    async def test_not_vulnerable(self):
        resp = _mock_response(status_code=200, text="<html>File not found</html>")

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await lfi_test("https://example.com/page?file=home", "file")

        assert result.success
        assert not result.data["vulnerable"]
        assert "No LFI indicators" in result.output


# ---------------------------------------------------------------------------
# 7. Header Audit Tests
# ---------------------------------------------------------------------------

class TestHeaderAudit:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await header_audit("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await header_audit("http://192.168.0.1")
        assert not result.success

    @pytest.mark.asyncio
    async def test_all_headers_present(self):
        all_headers = {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "X-XSS-Protection": "0",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=()",
            "Cache-Control": "no-store",
            "Server": "nginx",
        }

        resp = _mock_response(status_code=200, text="<html>ok</html>", headers=all_headers)

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await header_audit("https://example.com")

        assert result.success
        assert result.data["grade"] in ("A", "B")
        assert result.data["missing_count"] == 0

    @pytest.mark.asyncio
    async def test_no_headers_grade_f(self):
        resp = _mock_response(status_code=200, text="<html>ok</html>", headers={"Server": "Apache"})

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await header_audit("https://example.com")

        assert result.success
        assert result.data["grade"] in ("D", "F")
        assert result.data["missing_count"] > 0
        assert "Missing headers" in result.output

    @pytest.mark.asyncio
    async def test_partial_headers(self):
        partial_headers = {
            "Strict-Transport-Security": "max-age=31536000",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Server": "nginx",
        }

        resp = _mock_response(status_code=200, text="<html>ok</html>", headers=partial_headers)

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await header_audit("https://example.com")

        assert result.success
        assert result.data["present_count"] == 3
        assert result.data["missing_count"] > 0
        assert result.data["grade"] in ("B", "C", "D")

    @pytest.mark.asyncio
    async def test_output_contains_recommendations(self):
        resp = _mock_response(status_code=200, text="<html>ok</html>", headers={})

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await header_audit("https://example.com")

        assert result.success
        assert "Fix:" in result.output
        assert "CRITICAL" in result.output


# ---------------------------------------------------------------------------
# Timeout / Error Handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_dir_bruteforce_timeout(self):
        import httpx as real_httpx

        client = AsyncMock()
        client.head = AsyncMock(side_effect=real_httpx.TimeoutException("timeout"))

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dir_bruteforce("https://example.com", wordlist="small")

        # Should handle gracefully (timeouts on individual requests are caught)
        assert result.success
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_sqli_test_timeout(self):
        import httpx as real_httpx

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=real_httpx.TimeoutException("timeout"),
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await sqli_test("https://example.com/search?q=test")

        assert not result.success
        assert "timed out" in result.error or "failed" in result.error

    @pytest.mark.asyncio
    async def test_header_audit_timeout(self):
        import httpx as real_httpx

        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=real_httpx.TimeoutException("timeout"),
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await header_audit("https://example.com")

        assert not result.success
        assert "failed" in result.error or "timed out" in result.error

    @pytest.mark.asyncio
    async def test_cors_check_generic_error(self):
        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("connection reset"),
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cors_check("https://example.com")

        assert not result.success
        assert "failed" in result.error

    @pytest.mark.asyncio
    async def test_waf_detect_generic_error(self):
        with patch("src.tools.web_attack.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=RuntimeError("unexpected error"),
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await waf_detect("https://example.com")

        assert not result.success
        assert "failed" in result.error
