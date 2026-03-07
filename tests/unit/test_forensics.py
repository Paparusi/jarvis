"""Tests for forensics tools."""
from __future__ import annotations

import json
import os
import struct
import tempfile

import pytest

from src.tools.forensics import (
    file_metadata,
    file_metadata_tool,
    ioc_extract,
    ioc_extract_tool,
    log_analyze,
    log_analyze_tool,
    stego_detect,
    stego_detect_tool,
    _detect_log_type,
    _extract_iocs,
    _format_size,
    _looks_like_version,
    _parse_pdf_metadata,
    _parse_png_chunks,
    _validate_file_path,
)


# ---------------------------------------------------------------------------
# Tool definition tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_all_tools_defined(self):
        tools = [
            file_metadata_tool, stego_detect_tool,
            ioc_extract_tool, log_analyze_tool,
        ]
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.handler is not None
            assert tool.timeout_seconds > 0

    def test_tool_names(self):
        assert file_metadata_tool.name == "file_metadata"
        assert stego_detect_tool.name == "stego_detect"
        assert ioc_extract_tool.name == "ioc_extract"
        assert log_analyze_tool.name == "log_analyze"

    def test_tool_descriptions_in_vietnamese(self):
        for tool in [file_metadata_tool, stego_detect_tool, ioc_extract_tool, log_analyze_tool]:
            # Vietnamese descriptions contain diacritics or Vietnamese words
            assert any(c in tool.description for c in "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệ"
                       "ìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")

    def test_tool_schemas(self):
        for tool in [file_metadata_tool, stego_detect_tool, ioc_extract_tool, log_analyze_tool]:
            schema = tool.to_openai_schema()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == tool.name
            assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_format_size_bytes(self):
        assert _format_size(0) == "0 B"
        assert "B" in _format_size(500)

    def test_format_size_kb(self):
        result = _format_size(2048)
        assert "KB" in result

    def test_format_size_mb(self):
        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result

    def test_validate_file_path_empty(self):
        path, err = _validate_file_path("")
        assert path is None
        assert err

    def test_validate_file_path_nonexistent(self):
        path, err = _validate_file_path("/tmp/nonexistent_test_file_12345.txt")
        assert path is None
        assert "khong ton tai" in err.lower()

    def test_validate_file_path_outside_allowed(self):
        # Create a file outside allowed dirs
        with tempfile.NamedTemporaryFile(dir="/var/tmp", delete=False, suffix=".txt") as f:
            f.write(b"test")
            temp_path = f.name
        try:
            path, err = _validate_file_path(temp_path)
            assert path is None
            assert "cho phep" in err.lower()
        finally:
            os.unlink(temp_path)

    def test_validate_file_path_valid(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".txt") as f:
            f.write(b"test content")
            temp_path = f.name
        try:
            path, err = _validate_file_path(temp_path)
            assert path is not None
            assert err == ""
        finally:
            os.unlink(temp_path)

    def test_looks_like_version_true(self):
        text = "Using version 1.2.3.4 of the software"
        assert _looks_like_version(text, "1.2.3.4")

    def test_looks_like_version_false(self):
        text = "The attacker IP was 192.168.1.1"
        assert not _looks_like_version(text, "192.168.1.1")


# ---------------------------------------------------------------------------
# file_metadata tests
# ---------------------------------------------------------------------------

class TestFileMetadata:
    @pytest.mark.asyncio
    async def test_text_file(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".txt") as f:
            f.write(b"Hello, world!\nLine two.\n")
            temp_path = f.name
        try:
            result = await file_metadata(temp_path)
            assert result.success
            assert "File Metadata" in result.output
            assert "text/plain" in result.output
            assert result.data["filename"].endswith(".txt")
            assert result.data["size"] > 0
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        result = await file_metadata("/tmp/does_not_exist_abc123.bin")
        assert not result.success
        assert result.error

    @pytest.mark.asyncio
    async def test_empty_path(self):
        result = await file_metadata("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_pdf_metadata(self):
        # Create a minimal PDF with metadata
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
            b"3 0 obj\n<< /Author (Test Author) /Creator (Test Creator) "
            b"/Producer (Test Producer) /Title (Test Title) >>\nendobj\n"
            b"xref\n0 4\ntrailer\n<< /Size 4 /Root 1 0 R /Info 3 0 R >>\n"
            b"startxref\n0\n%%EOF"
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".pdf") as f:
            f.write(pdf_content)
            temp_path = f.name
        try:
            result = await file_metadata(temp_path)
            assert result.success
            assert "PDF Info" in result.output
            assert "Test Author" in result.output
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_png_metadata(self):
        # Create a minimal valid PNG (1x1 red pixel)
        png_data = _build_minimal_png()
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".png") as f:
            f.write(png_data)
            temp_path = f.name
        try:
            result = await file_metadata(temp_path)
            assert result.success
            assert "image/png" in result.output
        finally:
            os.unlink(temp_path)


class TestParsePngChunks:
    def test_valid_png(self):
        png_data = _build_minimal_png()
        info = _parse_png_chunks(png_data)
        assert info.get("width") == 1
        assert info.get("height") == 1

    def test_invalid_data(self):
        info = _parse_png_chunks(b"not a png")
        assert info == {}

    def test_empty_data(self):
        info = _parse_png_chunks(b"")
        assert info == {}


class TestParsePdfMetadata:
    def test_with_author(self):
        data = b"/Author (John Doe) /Creator (TestApp) /Producer (LibTest)"
        result = _parse_pdf_metadata(data)
        assert result["Author"] == "John Doe"
        assert result["Creator"] == "TestApp"
        assert result["Producer"] == "LibTest"

    def test_empty(self):
        result = _parse_pdf_metadata(b"just some random bytes")
        assert result == {}


# ---------------------------------------------------------------------------
# stego_detect tests
# ---------------------------------------------------------------------------

class TestStegoDetect:
    @pytest.mark.asyncio
    async def test_clean_png(self):
        png_data = _build_minimal_png()
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".png") as f:
            f.write(png_data)
            temp_path = f.name
        try:
            result = await stego_detect(temp_path)
            assert result.success
            assert "Steganography Analysis" in result.output
            assert result.data["confidence"] in ("LOW", "MEDIUM", "HIGH")
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        result = await stego_detect("/tmp/no_such_image_999.png")
        assert not result.success

    @pytest.mark.asyncio
    async def test_non_image_file(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".txt") as f:
            f.write(b"not an image")
            temp_path = f.name
        try:
            result = await stego_detect(temp_path)
            assert not result.success
            assert "ho tro" in result.error.lower()
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_invalid_method(self):
        png_data = _build_minimal_png()
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".png") as f:
            f.write(png_data)
            temp_path = f.name
        try:
            result = await stego_detect(temp_path, method="invalid")
            assert not result.success
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_lsb_method_only(self):
        png_data = _build_minimal_png()
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".png") as f:
            f.write(png_data)
            temp_path = f.name
        try:
            result = await stego_detect(temp_path, method="lsb")
            assert result.success
            assert "LSB Analysis" in result.output
            # Should not contain other sections
            assert "Metadata Analysis" not in result.output
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_metadata_method_only(self):
        png_data = _build_minimal_png()
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".png") as f:
            f.write(png_data)
            temp_path = f.name
        try:
            result = await stego_detect(temp_path, method="metadata")
            assert result.success
            assert "Metadata Analysis" in result.output
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_jpeg_with_trailing_data(self):
        """JPEG with data after EOI marker should trigger trailing_data detection."""
        # Minimal JPEG-like structure: SOI + some content + EOI + hidden data
        jpeg_data = b"\xff\xd8"  # SOI
        jpeg_data += b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9  # APP0
        jpeg_data += b"\xff\xd9"  # EOI
        jpeg_data += b"HIDDEN_SECRET_DATA_HERE" * 10  # Trailing data

        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".jpg") as f:
            f.write(jpeg_data)
            temp_path = f.name
        try:
            result = await stego_detect(temp_path, method="visual")
            assert result.success
            assert "trailing_data" in str(result.data.get("findings", []))
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_stego_tool_signature_detection(self):
        """File containing stego tool signatures should be flagged."""
        png_data = _build_minimal_png()
        # Inject a known signature
        modified = png_data + b"\x00\x00OpenStego signature marker\x00\x00"

        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".png") as f:
            f.write(modified)
            temp_path = f.name
        try:
            result = await stego_detect(temp_path, method="metadata")
            assert result.success
            findings = result.data.get("findings", [])
            tool_finding = [f for f in findings if f["check"] == "tool_signatures"]
            assert any(f["suspicious"] for f in tool_finding)
        finally:
            os.unlink(temp_path)


# ---------------------------------------------------------------------------
# ioc_extract tests
# ---------------------------------------------------------------------------

class TestIocExtract:
    @pytest.mark.asyncio
    async def test_extract_ipv4(self):
        text = "The attacker used 192.168.1.1 and 10.0.0.5 as C2 servers."
        result = await ioc_extract(text, ioc_types="ip")
        assert result.success
        assert "192.168.1.1" in result.output
        assert "10.0.0.5" in result.output

    @pytest.mark.asyncio
    async def test_extract_url(self):
        text = "Download malware from https://evil.com/payload.exe and http://bad.org/shell.php"
        result = await ioc_extract(text, ioc_types="url")
        assert result.success
        assert "https://evil.com/payload.exe" in result.output
        assert "http://bad.org/shell.php" in result.output

    @pytest.mark.asyncio
    async def test_extract_hashes(self):
        md5 = "d41d8cd98f00b204e9800998ecf8427e"
        sha1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        text = f"MD5: {md5}\nSHA1: {sha1}\nSHA256: {sha256}"
        result = await ioc_extract(text, ioc_types="hash")
        assert result.success
        assert md5 in result.output
        assert sha1 in result.output
        assert sha256 in result.output

    @pytest.mark.asyncio
    async def test_extract_emails(self):
        text = "Contact attacker@evil.com or phishing@malware.net"
        result = await ioc_extract(text, ioc_types="email")
        assert result.success
        assert "attacker@evil.com" in result.output
        assert "phishing@malware.net" in result.output

    @pytest.mark.asyncio
    async def test_extract_cves(self):
        text = "Exploits CVE-2021-44228 (Log4Shell) and CVE-2023-12345"
        result = await ioc_extract(text, ioc_types="cve")
        assert result.success
        assert "CVE-2021-44228" in result.output
        assert "CVE-2023-12345" in result.output

    @pytest.mark.asyncio
    async def test_extract_domains(self):
        text = "Resolved C2 at evil-server.net and malware-host.org"
        result = await ioc_extract(text, ioc_types="domain")
        assert result.success
        assert "evil-server.net" in result.output
        assert "malware-host.org" in result.output

    @pytest.mark.asyncio
    async def test_filter_common_domains(self):
        text = "The user visited google.com and evil-c2.net"
        result = await ioc_extract(text, ioc_types="domain")
        assert result.success
        # google.com should be filtered out
        iocs = result.data.get("iocs", {})
        domains = iocs.get("Domains", [])
        assert "google.com" not in domains
        assert "evil-c2.net" in domains

    @pytest.mark.asyncio
    async def test_extract_all(self):
        text = (
            "IP: 192.168.1.100\n"
            "URL: https://evil.com/malware\n"
            "Hash: d41d8cd98f00b204e9800998ecf8427e\n"
            "Email: hacker@dark.net\n"
            "CVE: CVE-2024-99999\n"
            "MAC: AA:BB:CC:DD:EE:FF\n"
        )
        result = await ioc_extract(text)
        assert result.success
        assert result.data["total"] >= 5

    @pytest.mark.asyncio
    async def test_empty_text(self):
        result = await ioc_extract("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_no_iocs(self):
        result = await ioc_extract("This is just a normal text with no indicators.")
        assert result.success
        assert result.data["total"] == 0

    @pytest.mark.asyncio
    async def test_invalid_ioc_type(self):
        result = await ioc_extract("some text", ioc_types="invalid")
        assert not result.success

    @pytest.mark.asyncio
    async def test_deduplication(self):
        text = "IP 10.0.0.1 appeared at 10.0.0.1 and again 10.0.0.1"
        result = await ioc_extract(text, ioc_types="ip")
        assert result.success
        ips = result.data["iocs"].get("IPv4", [])
        assert ips.count("10.0.0.1") == 1

    @pytest.mark.asyncio
    async def test_mac_address(self):
        text = "Device MAC: AA:BB:CC:DD:EE:FF and 11-22-33-44-55-66"
        result = await ioc_extract(text)
        assert result.success
        macs = result.data["iocs"].get("MAC", [])
        assert len(macs) >= 1

    @pytest.mark.asyncio
    async def test_version_filter(self):
        text = "Using version 1.2.3.4 of the software"
        result = await ioc_extract(text, ioc_types="ip")
        assert result.success
        ips = result.data["iocs"].get("IPv4", [])
        assert "1.2.3.4" not in ips


class TestExtractIocsSync:
    """Test the synchronous _extract_iocs helper directly."""

    def test_hash_dedup_across_lengths(self):
        """SHA256 should not also appear as MD5 or SHA1 substring."""
        sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        text = f"Hash: {sha256}"
        iocs = _extract_iocs(text, "hash")
        assert "SHA256" in iocs
        # The first 32 chars should NOT appear as MD5
        assert "MD5" not in iocs
        # The first 40 chars should NOT appear as SHA1
        assert "SHA1" not in iocs

    def test_ip_validation(self):
        """Invalid IP ranges should be filtered."""
        text = "IP: 999.999.999.999 and 192.168.1.1"
        iocs = _extract_iocs(text, "ip")
        ips = iocs.get("IPv4", [])
        assert "999.999.999.999" not in ips
        assert "192.168.1.1" in ips


# ---------------------------------------------------------------------------
# log_analyze tests
# ---------------------------------------------------------------------------

class TestLogAnalyze:
    @pytest.mark.asyncio
    async def test_apache_log(self):
        log_content = (
            '192.168.1.100 - - [10/Oct/2025:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n'
            '192.168.1.100 - - [10/Oct/2025:13:55:37 +0000] "GET /admin HTTP/1.1" 401 0\n'
            '10.0.0.5 - - [10/Oct/2025:13:55:38 +0000] "GET /etc/passwd HTTP/1.1" 403 0\n'
            '10.0.0.5 - - [10/Oct/2025:13:55:39 +0000] "GET /search?q=1%20OR%201=1 HTTP/1.1" 200 500\n'
            '10.0.0.5 - - [10/Oct/2025:13:55:40 +0000] "GET /../../../etc/shadow HTTP/1.1" 403 0\n'
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="apache")
            assert result.success
            assert "Status Code Distribution" in result.output
            assert "192.168.1.100" in result.output
            data = result.data
            assert data["parsed_count"] == 5
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_apache_sql_injection_detection(self):
        # URL-encoded paths that the Apache regex can parse (no raw spaces in path)
        # The SLEEP() and BENCHMARK() patterns match without needing spaces
        log_content = (
            '10.0.0.1 - - [10/Oct/2025:14:00:00 +0000] "GET /search?id=1;DROP%20TABLE HTTP/1.1" 200 100\n'
            '10.0.0.1 - - [10/Oct/2025:14:00:01 +0000] "GET /login?user=SLEEP(5) HTTP/1.1" 200 100\n'
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="apache")
            assert result.success
            attacks = result.data.get("attack_patterns", {})
            assert attacks.get("SQL Injection", 0) > 0
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_apache_xss_detection(self):
        log_content = (
            '10.0.0.1 - - [10/Oct/2025:14:00:00 +0000] '
            '"GET /page?name=<script>alert(1)</script> HTTP/1.1" 200 100\n'
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="apache")
            assert result.success
            attacks = result.data.get("attack_patterns", {})
            assert attacks.get("XSS", 0) > 0
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_auth_log(self):
        log_content = (
            "Oct 10 13:55:36 server sshd[1234]: Failed password for root from 192.168.1.50 port 22\n"
            "Oct 10 13:55:37 server sshd[1235]: Failed password for root from 192.168.1.50 port 22\n"
            "Oct 10 13:55:38 server sshd[1236]: Failed password for root from 192.168.1.50 port 22\n"
            "Oct 10 13:55:39 server sshd[1237]: Failed password for root from 192.168.1.50 port 22\n"
            "Oct 10 13:55:40 server sshd[1238]: Failed password for root from 192.168.1.50 port 22\n"
            "Oct 10 13:55:41 server sshd[1239]: Accepted password for root from 192.168.1.50 port 22\n"
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="auth")
            assert result.success
            assert "Brute Force" in result.output
            data = result.data
            assert data["failed_attempts"] >= 5
            assert "192.168.1.50" in data.get("brute_force_ips", {})
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_auth_log_success_after_fail(self):
        log_content = (
            "Oct 10 13:55:36 server sshd[1234]: Failed password for admin from 10.0.0.1 port 22\n"
            "Oct 10 13:55:37 server sshd[1235]: Failed password for admin from 10.0.0.1 port 22\n"
            "Oct 10 13:55:38 server sshd[1236]: Accepted password for admin from 10.0.0.1 port 22\n"
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="auth")
            assert result.success
            assert "Possible Compromise" in result.output or len(result.data.get("success_after_fail", [])) > 0
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_json_log(self):
        entries = [
            {"level": "INFO", "message": "Server started", "timestamp": "2025-10-10T00:00:00"},
            {"level": "INFO", "message": "Request processed", "timestamp": "2025-10-10T00:00:01"},
            {"level": "ERROR", "message": "Database connection failed", "timestamp": "2025-10-10T00:00:02"},
            {"level": "ERROR", "message": "Timeout reached", "timestamp": "2025-10-10T00:00:03"},
            {"level": "CRITICAL", "message": "Service crash", "timestamp": "2025-10-10T00:00:04"},
        ]
        log_content = "\n".join(json.dumps(e) for e in entries)
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="json")
            assert result.success
            assert "Log Level Distribution" in result.output
            data = result.data
            assert data["error_count"] >= 2
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_syslog(self):
        log_content = (
            "Oct 10 00:00:01 myhost kernel: segfault at 0000000000\n"
            "Oct 10 00:00:02 myhost systemd[1]: Started nginx.service\n"
            "Oct 10 00:00:03 myhost cron[500]: (root) CMD (run-parts /etc/cron.daily)\n"
            "Oct 10 00:00:04 myhost kernel: Out of memory: Killed process 1234\n"
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="syslog")
            assert result.success
            assert "Syslog Summary" in result.output
            data = result.data
            assert data["error_count"] >= 1
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_auto_detect_apache(self):
        log_content = (
            '1.2.3.4 - - [10/Oct/2025:13:55:36 +0000] "GET / HTTP/1.1" 200 1234\n'
            '1.2.3.4 - - [10/Oct/2025:13:55:37 +0000] "GET /page HTTP/1.1" 200 5678\n'
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="auto")
            assert result.success
            assert "apache" in result.output.lower() or "nginx" in result.output.lower()
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_auto_detect_json(self):
        entries = [{"level": "INFO", "msg": "ok"}] * 5
        log_content = "\n".join(json.dumps(e) for e in entries)
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="auto")
            assert result.success
            assert "json" in result.output.lower()
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_empty_log(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write("")
            temp_path = f.name
        try:
            result = await log_analyze(temp_path)
            assert result.success
            assert "rong" in result.output.lower()
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_nonexistent_log(self):
        result = await log_analyze("/tmp/no_such_log_file_999.log")
        assert not result.success

    @pytest.mark.asyncio
    async def test_invalid_log_type(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write("some log line\n")
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="invalid")
            assert not result.success
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_scanner_detection(self):
        log_content = (
            '10.0.0.1 - - [10/Oct/2025:14:00:00 +0000] '
            '"GET /nikto-test HTTP/1.1" 404 0\n'
        )
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False, suffix=".log", mode="w") as f:
            f.write(log_content)
            temp_path = f.name
        try:
            result = await log_analyze(temp_path, log_type="apache")
            assert result.success
            attacks = result.data.get("attack_patterns", {})
            assert attacks.get("Scanner Activity", 0) > 0
        finally:
            os.unlink(temp_path)


class TestDetectLogType:
    def test_detect_json(self):
        lines = ['{"level":"info"}', '{"level":"error"}', '{"msg":"test"}']
        assert _detect_log_type(lines) == "json"

    def test_detect_apache(self):
        lines = [
            '1.2.3.4 - - [10/Oct/2025:00:00:00 +0000] "GET / HTTP/1.1" 200 100',
            '1.2.3.4 - - [10/Oct/2025:00:00:01 +0000] "GET /a HTTP/1.1" 200 100',
        ]
        assert _detect_log_type(lines) == "apache"

    def test_detect_auth(self):
        lines = [
            "Oct 10 00:00:00 host sshd[1]: Failed password for root from 1.2.3.4 port 22",
            "Oct 10 00:00:01 host sshd[2]: Failed password for root from 1.2.3.4 port 22",
        ]
        assert _detect_log_type(lines) == "auth"

    def test_detect_syslog_fallback(self):
        lines = ["some random line", "another line", "nothing special"]
        assert _detect_log_type(lines) == "syslog"

    def test_detect_empty(self):
        assert _detect_log_type([]) == "syslog"


# ---------------------------------------------------------------------------
# Helpers for building test files
# ---------------------------------------------------------------------------

def _build_minimal_png() -> bytes:
    """Build a minimal valid PNG file (1x1 white pixel, RGB)."""
    import zlib

    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk: width=1, height=1, bit_depth=8, color_type=2 (RGB)
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr = _png_chunk(b"IHDR", ihdr_data)

    # IDAT chunk: row filter byte (0) + RGB pixel (255, 255, 255)
    raw_row = b"\x00\xff\xff\xff"
    compressed = zlib.compress(raw_row)
    idat = _png_chunk(b"IDAT", compressed)

    # IEND chunk
    iend = _png_chunk(b"IEND", b"")

    return sig + ihdr + idat + iend


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Create a PNG chunk with length, type, data, and CRC."""
    import zlib

    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    return length + chunk_type + data + crc
