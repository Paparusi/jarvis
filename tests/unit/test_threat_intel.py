"""Tests for threat intelligence tools."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.threat_intel import (
    _detect_indicator_type,
    _validate_hash,
    _validate_ip,
    _ssrf_check_ip,
    abuseipdb_check,
    abuseipdb_check_tool,
    malware_hash_check,
    malware_hash_check_tool,
    shodan_search,
    shodan_search_tool,
    virustotal_lookup,
    virustotal_lookup_tool,
)


# ---------------------------------------------------------------------------
# Tool Definition tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_all_tools_defined(self):
        tools = [
            virustotal_lookup_tool,
            abuseipdb_check_tool,
            malware_hash_check_tool,
            shodan_search_tool,
        ]
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.handler is not None
            assert tool.timeout_seconds > 0
            assert len(tool.parameters) >= 1

    def test_tool_names(self):
        assert virustotal_lookup_tool.name == "virustotal_lookup"
        assert abuseipdb_check_tool.name == "abuseipdb_check"
        assert malware_hash_check_tool.name == "malware_hash_check"
        assert shodan_search_tool.name == "shodan_search"

    def test_descriptions_not_empty(self):
        tools = [
            virustotal_lookup_tool,
            abuseipdb_check_tool,
            malware_hash_check_tool,
            shodan_search_tool,
        ]
        for tool in tools:
            assert len(tool.description) > 20

    def test_openai_schema_generation(self):
        for tool in [virustotal_lookup_tool, abuseipdb_check_tool,
                     malware_hash_check_tool, shodan_search_tool]:
            schema = tool.to_openai_schema()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == tool.name
            assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# Validation helper tests
# ---------------------------------------------------------------------------

class TestDetectIndicatorType:
    def test_md5_hash(self):
        assert _detect_indicator_type("d41d8cd98f00b204e9800998ecf8427e") == "hash"

    def test_sha1_hash(self):
        assert _detect_indicator_type("da39a3ee5e6b4b0d3255bfef95601890afd80709") == "hash"

    def test_sha256_hash(self):
        h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert _detect_indicator_type(h) == "hash"

    def test_url_http(self):
        assert _detect_indicator_type("http://example.com") == "url"

    def test_url_https(self):
        assert _detect_indicator_type("https://malware.com/payload.exe") == "url"

    def test_ip_address(self):
        assert _detect_indicator_type("8.8.8.8") == "ip"

    def test_domain(self):
        assert _detect_indicator_type("example.com") == "domain"

    def test_domain_with_subdomain(self):
        assert _detect_indicator_type("sub.example.com") == "domain"


class TestValidateHash:
    def test_valid_md5(self):
        assert _validate_hash("d41d8cd98f00b204e9800998ecf8427e") is None

    def test_valid_sha1(self):
        assert _validate_hash("da39a3ee5e6b4b0d3255bfef95601890afd80709") is None

    def test_valid_sha256(self):
        h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert _validate_hash(h) is None

    def test_invalid_chars(self):
        err = _validate_hash("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
        assert err is not None
        assert "hexadecimal" in err

    def test_wrong_length(self):
        err = _validate_hash("abcdef1234")
        assert err is not None
        assert "length" in err

    def test_empty(self):
        err = _validate_hash("")
        assert err is not None


class TestSsrfCheckIp:
    def test_blocks_localhost(self):
        assert _ssrf_check_ip("127.0.0.1") is not None

    def test_blocks_private_10(self):
        assert _ssrf_check_ip("10.0.0.1") is not None

    def test_blocks_private_192(self):
        assert _ssrf_check_ip("192.168.1.1") is not None

    def test_allows_public_ip(self):
        assert _ssrf_check_ip("8.8.8.8") is None

    def test_blocks_zero(self):
        assert _ssrf_check_ip("0.0.0.0") is not None


# ---------------------------------------------------------------------------
# VirusTotal Lookup tests
# ---------------------------------------------------------------------------

def _mock_httpx_response(status_code: int = 200, json_data: dict = None):
    """Create a mock httpx response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data or {}
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock,
        )
    return mock


class TestVirusTotalLookup:
    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = await virustotal_lookup("d41d8cd98f00b204e9800998ecf8427e")
        assert not result.success
        assert "VIRUSTOTAL_API_KEY" in result.output
        assert "virustotal.com" in result.output

    @pytest.mark.asyncio
    async def test_empty_indicator(self):
        result = await virustotal_lookup("")
        assert not result.success
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_indicator_chars(self):
        result = await virustotal_lookup("test;echo bad")
        assert not result.success
        assert "disallowed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_hash_lookup_success(self):
        vt_response = {
            "data": {
                "attributes": {
                    "last_analysis_stats": {
                        "malicious": 15,
                        "suspicious": 2,
                        "undetected": 30,
                        "harmless": 5,
                    },
                    "reputation": -50,
                    "last_analysis_results": {
                        "EngineA": {"category": "malicious", "result": "Trojan.Gen"},
                        "EngineB": {"category": "malicious", "result": "Win32.Malware"},
                        "EngineC": {"category": "undetected", "result": None},
                    },
                    "meaningful_name": "suspicious.exe",
                    "type_description": "Win32 EXE",
                    "size": 12345,
                },
            },
        }

        mock_resp = _mock_httpx_response(200, vt_response)

        with patch.dict("os.environ", {"VIRUSTOTAL_API_KEY": "test_key"}):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await virustotal_lookup(
                    "d41d8cd98f00b204e9800998ecf8427e", "hash",
                )

        assert result.success
        assert "15/52" in result.output  # 15+2+30+5 = 52
        assert "HIGH" in result.output
        assert result.data["malicious"] == 15

    @pytest.mark.asyncio
    async def test_auto_detect_hash(self):
        with patch.dict("os.environ", {"VIRUSTOTAL_API_KEY": "test_key"}):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_resp = _mock_httpx_response(404, {})
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await virustotal_lookup("d41d8cd98f00b204e9800998ecf8427e")

        assert result.success
        assert "hash" in result.output.lower()

    @pytest.mark.asyncio
    async def test_ssrf_blocked_for_ip(self):
        with patch.dict("os.environ", {"VIRUSTOTAL_API_KEY": "test_key"}):
            result = await virustotal_lookup("127.0.0.1", "ip")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_rate_limit_handling(self):
        mock_resp = _mock_httpx_response(429, {})
        mock_resp.raise_for_status = MagicMock()  # Don't raise on 429

        with patch.dict("os.environ", {"VIRUSTOTAL_API_KEY": "test_key"}):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await virustotal_lookup(
                    "d41d8cd98f00b204e9800998ecf8427e", "hash",
                )

        assert not result.success
        assert "rate limit" in result.error.lower()


# ---------------------------------------------------------------------------
# AbuseIPDB Check tests
# ---------------------------------------------------------------------------

class TestAbuseIPDBCheck:
    @pytest.mark.asyncio
    async def test_invalid_ip(self):
        result = await abuseipdb_check("not-an-ip")
        assert not result.success
        assert "Invalid IP" in result.error

    @pytest.mark.asyncio
    async def test_empty_ip(self):
        result = await abuseipdb_check("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        result = await abuseipdb_check("127.0.0.1")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_private_ip_blocked(self):
        result = await abuseipdb_check("10.0.0.1")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_api_check_success(self):
        api_response = {
            "data": {
                "abuseConfidenceScore": 87,
                "totalReports": 42,
                "countryCode": "CN",
                "isp": "Evil ISP",
                "usageType": "Data Center/Web Hosting/Transit",
                "domain": "evil.example.com",
                "isWhitelisted": False,
                "lastReportedAt": "2026-03-01T12:00:00+00:00",
                "reports": [
                    {"categories": [14, 18]},
                    {"categories": [22]},
                ],
            },
        }

        mock_resp = _mock_httpx_response(200, api_response)

        with patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "test_key"}):
            with patch("src.tools.threat_intel._ssrf_check_ip", return_value=None):
                with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                    mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                        get=AsyncMock(return_value=mock_resp),
                    ))
                    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = await abuseipdb_check("203.0.113.50")

        assert result.success
        assert "87%" in result.output
        assert "HIGH" in result.output
        assert "42" in result.output
        assert result.data["confidence"] == 87

    @pytest.mark.asyncio
    async def test_free_fallback_used(self):
        free_response = {"result": "0.75", "status": "success"}
        mock_resp = _mock_httpx_response(200, free_response)

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.tools.threat_intel._ssrf_check_ip", return_value=None):
                with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                    mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                        get=AsyncMock(return_value=mock_resp),
                    ))
                    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = await abuseipdb_check("203.0.113.50")

        assert result.success
        assert "getipintel" in result.output.lower()
        assert "MEDIUM" in result.output
        assert result.data["source"] == "getipintel.net"

    @pytest.mark.asyncio
    async def test_max_age_days_clamped(self):
        """max_age_days should be clamped between 1 and 365."""
        mock_resp = _mock_httpx_response(200, {"result": "0.1"})

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.tools.threat_intel._ssrf_check_ip", return_value=None):
                with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                    mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                        get=AsyncMock(return_value=mock_resp),
                    ))
                    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                    # Should not error even with extreme values
                    result = await abuseipdb_check("203.0.113.50", max_age_days=9999)
        assert result.success


# ---------------------------------------------------------------------------
# Malware Hash Check tests
# ---------------------------------------------------------------------------

class TestMalwareHashCheck:
    @pytest.mark.asyncio
    async def test_invalid_hash(self):
        result = await malware_hash_check("not-a-hash")
        assert not result.success
        assert "hexadecimal" in result.error

    @pytest.mark.asyncio
    async def test_empty_hash(self):
        result = await malware_hash_check("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_wrong_length_hash(self):
        result = await malware_hash_check("abcdef12")
        assert not result.success
        assert "length" in result.error

    @pytest.mark.asyncio
    async def test_hash_found_in_bazaar(self):
        bazaar_response = {
            "query_status": "ok",
            "data": [{
                "signature": "Emotet",
                "file_type": "exe",
                "file_size": 98304,
                "first_seen": "2026-01-15 10:00:00",
                "tags": ["emotet", "trojan"],
                "reporter": "abuse_ch",
                "origin_country": "DE",
            }],
        }
        threatfox_response = {"query_status": "no_result", "data": []}
        urlhaus_response = {"query_status": "no_results"}

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()

            if "mb-api.abuse.ch" in url:
                resp.json.return_value = bazaar_response
            elif "threatfox-api.abuse.ch" in url:
                resp.json.return_value = threatfox_response
            elif "urlhaus-api.abuse.ch" in url:
                resp.json.return_value = urlhaus_response
            else:
                resp.json.return_value = {}
            return resp

        test_hash = "d41d8cd98f00b204e9800998ecf8427e"

        with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock(side_effect=mock_post)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await malware_hash_check(test_hash)

        assert result.success
        assert "Emotet" in result.output
        assert "MalwareBazaar" in result.output
        assert result.data["found"]
        assert result.data["malwarebazaar"] is not None

    @pytest.mark.asyncio
    async def test_hash_not_found_anywhere(self):
        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"query_status": "hash_not_found"}
            return resp

        test_hash = "d41d8cd98f00b204e9800998ecf8427e"

        with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock(side_effect=mock_post)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await malware_hash_check(test_hash)

        assert result.success
        assert "Khong tim thay" in result.output
        assert not result.data["found"]

    @pytest.mark.asyncio
    async def test_graceful_handling_when_one_source_fails(self):
        """If one source errors, others should still return results."""
        call_idx = 0

        async def mock_post(url, **kwargs):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()

            if "mb-api.abuse.ch" in url:
                raise Exception("Connection refused")
            elif "threatfox-api.abuse.ch" in url:
                resp.json.return_value = {
                    "query_status": "ok",
                    "data": [{
                        "threat_type": "payload",
                        "malware_printable": "Cobalt Strike",
                        "confidence_level": 90,
                        "first_seen": "2026-02-01",
                        "last_seen": "2026-03-01",
                        "tags": ["cobaltstrike"],
                    }],
                }
            else:
                resp.json.return_value = {"query_status": "no_results"}
            return resp

        test_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

        with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock(side_effect=mock_post)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await malware_hash_check(test_hash)

        assert result.success
        assert "ThreatFox" in result.output
        assert "Cobalt Strike" in result.output
        assert result.data["found"]

    @pytest.mark.asyncio
    async def test_sha256_hash_accepted(self):
        """SHA256 hashes should be valid input."""
        test_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"query_status": "hash_not_found"}
            return resp

        with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock(side_effect=mock_post)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await malware_hash_check(test_hash)

        assert result.success


# ---------------------------------------------------------------------------
# Shodan Search tests
# ---------------------------------------------------------------------------

class TestShodanSearch:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await shodan_search("")
        assert not result.success
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_api_key_non_ip_query(self):
        with patch.dict("os.environ", {}, clear=True):
            result = await shodan_search("apache")
        assert not result.success
        assert "SHODAN_API_KEY" in result.output

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self):
        with patch.dict("os.environ", {}, clear=True):
            result = await shodan_search("127.0.0.1")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_internetdb_fallback(self):
        internetdb_response = {
            "ip": "93.184.216.34",
            "ports": [80, 443],
            "hostnames": ["example.com"],
            "cpes": ["cpe:/a:apache:http_server:2.4.41"],
            "vulns": ["CVE-2021-44228"],
            "tags": ["cloud"],
        }

        mock_resp = _mock_httpx_response(200, internetdb_response)

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await shodan_search("93.184.216.34")

        assert result.success
        assert "InternetDB" in result.output
        assert "80" in result.output
        assert "443" in result.output
        assert "CVE-2021-44228" in result.output
        assert result.data["source"] == "internetdb"

    @pytest.mark.asyncio
    async def test_internetdb_not_found(self):
        mock_resp = _mock_httpx_response(404, {})
        mock_resp.raise_for_status = MagicMock()  # Don't raise for 404

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.tools.threat_intel._ssrf_check_ip", return_value=None):
                with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                    mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                        get=AsyncMock(return_value=mock_resp),
                    ))
                    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = await shodan_search("198.51.100.1")

        assert result.success
        assert "Khong tim thay" in result.output

    @pytest.mark.asyncio
    async def test_shodan_api_host_search(self):
        host_response = {
            "ip_str": "93.184.216.34",
            "org": "Edgecast",
            "os": "Linux",
            "country_name": "United States",
            "city": "Los Angeles",
            "ports": [80, 443],
            "vulns": ["CVE-2021-44228"],
            "data": [
                {
                    "port": 80,
                    "transport": "tcp",
                    "product": "nginx",
                    "version": "1.21.6",
                    "data": "HTTP/1.1 200 OK",
                },
            ],
        }

        mock_resp = _mock_httpx_response(200, host_response)

        with patch.dict("os.environ", {"SHODAN_API_KEY": "test_key"}):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await shodan_search("93.184.216.34", "host")

        assert result.success
        assert "Edgecast" in result.output
        assert "nginx" in result.output
        assert "CVE-2021-44228" in result.output

    @pytest.mark.asyncio
    async def test_shodan_api_invalid_key(self):
        mock_resp = _mock_httpx_response(401, {})
        mock_resp.raise_for_status = MagicMock()  # Don't raise; we handle 401 explicitly

        with patch.dict("os.environ", {"SHODAN_API_KEY": "bad_key"}):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await shodan_search("apache", "search")

        assert not result.success
        assert "khong hop le" in result.error.lower()

    @pytest.mark.asyncio
    async def test_shodan_dns_search(self):
        dns_response = {"example.com": "93.184.216.34"}
        mock_resp = _mock_httpx_response(200, dns_response)

        with patch.dict("os.environ", {"SHODAN_API_KEY": "test_key"}):
            with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp),
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                # Need to also patch _ssrf_check_domain to not actually resolve DNS
                with patch("src.tools.threat_intel._ssrf_check_domain", return_value=None):
                    result = await shodan_search("example.com", "dns")

        assert result.success
        assert "example.com" in result.output
        assert "93.184.216.34" in result.output

    @pytest.mark.asyncio
    async def test_internetdb_recommendations(self):
        """InternetDB results with vulns should include recommendations."""
        internetdb_response = {
            "ip": "198.51.100.1",
            "ports": [22, 80, 443, 3306, 5432, 8080],
            "hostnames": [],
            "cpes": [],
            "vulns": ["CVE-2023-12345", "CVE-2024-67890"],
            "tags": [],
        }

        mock_resp = _mock_httpx_response(200, internetdb_response)

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.tools.threat_intel._ssrf_check_ip", return_value=None):
                with patch("src.tools.threat_intel.httpx.AsyncClient") as mock_client:
                    mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                        get=AsyncMock(return_value=mock_resp),
                    ))
                    mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = await shodan_search("198.51.100.1")

        assert result.success
        assert "Khuyen nghi" in result.output
        assert "lo hong" in result.output
