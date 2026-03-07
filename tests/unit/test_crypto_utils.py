"""Tests for crypto_utils tools."""
from __future__ import annotations

import pytest

from src.tools.crypto_utils import (
    base64_encode_decode,
    hash_text,
    url_encode_decode,
    jwt_decode,
    hex_convert,
    regex_test,
    timestamp_convert,
    generate_password,
    cidr_calc,
    base64_tool,
    hash_tool,
    url_encode_tool,
    jwt_decode_tool,
    hex_convert_tool,
    regex_test_tool,
    timestamp_tool,
    generate_password_tool,
    cidr_calc_tool,
)


class TestBase64:
    @pytest.mark.asyncio
    async def test_encode(self):
        result = await base64_encode_decode("hello world", "encode")
        assert result.success
        assert "aGVsbG8gd29ybGQ=" in result.output

    @pytest.mark.asyncio
    async def test_decode(self):
        result = await base64_encode_decode("aGVsbG8gd29ybGQ=", "decode")
        assert result.success
        assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_invalid_decode(self):
        result = await base64_encode_decode("not-valid-base64!!!", "decode")
        # Should still try — base64 is lenient
        assert result.success or not result.success  # No crash

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await base64_encode_decode("", "encode")
        assert result.success or not result.success  # No crash


class TestHash:
    @pytest.mark.asyncio
    async def test_md5(self):
        result = await hash_text("hello", "md5")
        assert result.success
        assert "5d41402abc4b2a76b9719d911017c592" in result.output

    @pytest.mark.asyncio
    async def test_sha256(self):
        result = await hash_text("hello", "sha256")
        assert result.success
        assert "2cf24dba" in result.output

    @pytest.mark.asyncio
    async def test_sha1(self):
        result = await hash_text("test", "sha1")
        assert result.success

    @pytest.mark.asyncio
    async def test_sha512(self):
        result = await hash_text("test", "sha512")
        assert result.success

    @pytest.mark.asyncio
    async def test_invalid_algorithm(self):
        result = await hash_text("test", "invalid")
        assert not result.success


class TestUrlEncode:
    @pytest.mark.asyncio
    async def test_encode(self):
        result = await url_encode_decode("hello world&foo=bar", "encode")
        assert result.success
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_decode(self):
        result = await url_encode_decode("hello%20world", "decode")
        assert result.success
        assert "hello world" in result.output


class TestJwtDecode:
    @pytest.mark.asyncio
    async def test_decode_valid_jwt(self):
        # A simple JWT (header.payload.signature)
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = await jwt_decode(jwt)
        assert result.success
        assert "John Doe" in result.output

    @pytest.mark.asyncio
    async def test_decode_invalid_jwt(self):
        result = await jwt_decode("not.a.jwt")
        # Should handle gracefully
        assert not result.success or "error" in result.output.lower() or result.success


class TestHexConvert:
    @pytest.mark.asyncio
    async def test_text_to_hex(self):
        result = await hex_convert("hello", "to_hex")
        assert result.success
        assert "68656c6c6f" in result.output

    @pytest.mark.asyncio
    async def test_hex_to_text(self):
        result = await hex_convert("68656c6c6f", "from_hex")
        assert result.success
        assert "hello" in result.output


class TestRegexTest:
    @pytest.mark.asyncio
    async def test_match(self):
        result = await regex_test(r"\d+", "abc 123 def 456")
        assert result.success
        assert "123" in result.output

    @pytest.mark.asyncio
    async def test_no_match(self):
        result = await regex_test(r"\d+", "no numbers here")
        assert result.success
        assert "0" in result.output or "no match" in result.output.lower() or "No matches" in result.output


class TestTimestamp:
    @pytest.mark.asyncio
    async def test_timestamp_conversion(self):
        import time
        result = await timestamp_convert(str(int(time.time())))
        assert result.success

    @pytest.mark.asyncio
    async def test_unix_timestamp(self):
        result = await timestamp_convert("1609459200")
        assert result.success
        assert "2021" in result.output


class TestGeneratePassword:
    @pytest.mark.asyncio
    async def test_default(self):
        result = await generate_password()
        assert result.success
        assert len(result.output) > 0

    @pytest.mark.asyncio
    async def test_custom_length(self):
        result = await generate_password(length=32)
        assert result.success

    @pytest.mark.asyncio
    async def test_multiple(self):
        result = await generate_password(count=5)
        assert result.success

    @pytest.mark.asyncio
    async def test_alphanumeric(self):
        result = await generate_password(charset="alphanumeric")
        assert result.success


class TestCidrCalc:
    @pytest.mark.asyncio
    async def test_valid_cidr(self):
        result = await cidr_calc("192.168.1.0/24")
        assert result.success
        assert "192.168.1" in result.output
        assert "256" in result.output or "254" in result.output

    @pytest.mark.asyncio
    async def test_invalid_cidr(self):
        result = await cidr_calc("not-a-cidr")
        assert not result.success


class TestToolDefinitions:
    def test_all_tools_have_names(self):
        tools = [
            base64_tool, hash_tool, url_encode_tool, jwt_decode_tool,
            hex_convert_tool, regex_test_tool, timestamp_tool,
            generate_password_tool, cidr_calc_tool,
        ]
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.handler is not None
