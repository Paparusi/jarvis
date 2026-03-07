"""Tests for OSINT tools."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.osint import (
    google_dork,
    google_dork_tool,
    username_search,
    username_search_tool,
    email_harvest,
    email_harvest_tool,
    wayback_lookup,
    wayback_lookup_tool,
    github_leaks,
    github_leaks_tool,
    _validate_domain,
    _validate_input,
    _validate_url,
    _validate_username,
)


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_all_tools_defined(self):
        tools = [
            google_dork_tool, username_search_tool, email_harvest_tool,
            wayback_lookup_tool, github_leaks_tool,
        ]
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.handler is not None
            assert tool.timeout_seconds > 0

    def test_tool_names(self):
        assert google_dork_tool.name == "google_dork"
        assert username_search_tool.name == "username_search"
        assert email_harvest_tool.name == "email_harvest"
        assert wayback_lookup_tool.name == "wayback_lookup"
        assert github_leaks_tool.name == "github_leaks"

    def test_descriptions_in_vietnamese(self):
        tools = [
            google_dork_tool, username_search_tool, email_harvest_tool,
            wayback_lookup_tool, github_leaks_tool,
        ]
        for tool in tools:
            # Vietnamese descriptions should contain Vietnamese characters or keywords
            assert any(c in tool.description for c in "áàảãạắặ" + "ếệ" + "ốộ" + "ứự")

    def test_tool_schemas_valid(self):
        tools = [
            google_dork_tool, username_search_tool, email_harvest_tool,
            wayback_lookup_tool, github_leaks_tool,
        ]
        for tool in tools:
            schema = tool.to_openai_schema()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == tool.name
            assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------

class TestValidation:
    def test_validate_input_empty(self):
        assert _validate_input("", "test") is not None
        assert _validate_input("  ", "test") is not None

    def test_validate_input_dangerous_chars(self):
        assert _validate_input("foo;bar", "test") is not None
        assert _validate_input("foo|bar", "test") is not None
        assert _validate_input("foo`cmd`", "test") is not None

    def test_validate_input_valid(self):
        assert _validate_input("example.com", "test") is None
        assert _validate_input("hello world", "test") is None

    def test_validate_domain_valid(self):
        assert _validate_domain("example.com") is None
        assert _validate_domain("sub.example.com") is None
        assert _validate_domain("test-site.co.uk") is None

    def test_validate_domain_invalid(self):
        assert _validate_domain("") is not None
        assert _validate_domain("-invalid.com") is not None
        assert _validate_domain("not a domain!") is not None

    def test_validate_url_valid(self):
        assert _validate_url("https://example.com") is None
        assert _validate_url("http://test.org/path") is None

    def test_validate_url_invalid(self):
        assert _validate_url("") is not None
        assert _validate_url("ftp://files.com") is not None
        assert _validate_url("not-a-url") is not None

    def test_validate_url_ssrf_blocked(self):
        assert _validate_url("http://127.0.0.1") is not None
        assert _validate_url("http://localhost") is not None
        assert _validate_url("http://0.0.0.0") is not None
        assert _validate_url("http://169.254.169.254") is not None

    def test_validate_username_valid(self):
        assert _validate_username("john_doe") is None
        assert _validate_username("user123") is None
        assert _validate_username("my-name.here") is None

    def test_validate_username_invalid(self):
        assert _validate_username("") is not None
        assert _validate_username("user name") is not None
        assert _validate_username("user@name") is not None
        assert _validate_username("a" * 65) is not None


# ---------------------------------------------------------------------------
# 1. Google Dork
# ---------------------------------------------------------------------------

class TestGoogleDork:
    @pytest.mark.asyncio
    async def test_google_dork_all(self):
        result = await google_dork("example.com", "all")
        assert result.success
        assert "example.com" in result.output
        assert "site:example.com" in result.output
        assert result.data["count"] > 0
        assert result.data["target"] == "example.com"

    @pytest.mark.asyncio
    async def test_google_dork_files(self):
        result = await google_dork("example.com", "files")
        assert result.success
        assert "filetype:" in result.output
        assert result.data["dork_type"] == "files"

    @pytest.mark.asyncio
    async def test_google_dork_login(self):
        result = await google_dork("example.com", "login")
        assert result.success
        assert "inurl:login" in result.output or "inurl:admin" in result.output

    @pytest.mark.asyncio
    async def test_google_dork_config(self):
        result = await google_dork("example.com", "config")
        assert result.success
        assert "ext:env" in result.output or "ext:yml" in result.output

    @pytest.mark.asyncio
    async def test_google_dork_database(self):
        result = await google_dork("example.com", "database")
        assert result.success
        assert "sql" in result.output.lower() or "database" in result.output.lower()

    @pytest.mark.asyncio
    async def test_google_dork_sensitive(self):
        result = await google_dork("example.com", "sensitive")
        assert result.success
        assert "password" in result.output.lower() or "api_key" in result.output.lower()

    @pytest.mark.asyncio
    async def test_google_dork_dirs(self):
        result = await google_dork("example.com", "dirs")
        assert result.success
        assert "index of" in result.output.lower()

    @pytest.mark.asyncio
    async def test_google_dork_invalid_type(self):
        result = await google_dork("example.com", "invalid")
        assert not result.success
        assert "không hợp lệ" in result.error

    @pytest.mark.asyncio
    async def test_google_dork_empty_target(self):
        result = await google_dork("", "all")
        assert not result.success

    @pytest.mark.asyncio
    async def test_google_dork_dangerous_input(self):
        result = await google_dork("example.com; rm -rf /", "all")
        assert not result.success


# ---------------------------------------------------------------------------
# 2. Username Search
# ---------------------------------------------------------------------------

class TestUsernameSearch:
    @pytest.mark.asyncio
    async def test_username_search_found(self):
        """Mock all platforms returning 200 (found)."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await username_search("testuser", "popular")

        assert result.success
        assert "testuser" in result.output
        assert result.data["found_count"] > 0

    @pytest.mark.asyncio
    async def test_username_search_not_found(self):
        """Mock all platforms returning 404 (not found)."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await username_search("nonexistent_user_xyz_999", "popular")

        assert result.success
        assert result.data["found_count"] == 0

    @pytest.mark.asyncio
    async def test_username_search_specific_platforms(self):
        """Test specifying specific platforms."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await username_search("testuser", "github,reddit")

        assert result.success
        assert result.data["checked_count"] == 2

    @pytest.mark.asyncio
    async def test_username_search_all_platforms(self):
        """Test checking all platforms."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await username_search("testuser", "all")

        assert result.success
        assert result.data["checked_count"] == 10

    @pytest.mark.asyncio
    async def test_username_search_invalid_platform(self):
        result = await username_search("testuser", "fakebook")
        assert not result.success
        assert "không hợp lệ" in result.error

    @pytest.mark.asyncio
    async def test_username_search_empty(self):
        result = await username_search("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_username_search_invalid_chars(self):
        result = await username_search("user name with spaces")
        assert not result.success

    @pytest.mark.asyncio
    async def test_username_search_timeout_handling(self):
        """Test graceful handling of timeouts on individual platforms."""
        import httpx as httpx_module

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx_module.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await username_search("testuser", "github")

        assert result.success
        assert "timeout" in result.output.lower() or "Inconclusive" in result.output


# ---------------------------------------------------------------------------
# 3. Email Harvest
# ---------------------------------------------------------------------------

class TestEmailHarvest:
    @pytest.mark.asyncio
    async def test_email_harvest_found(self):
        """Mock DDG search + page fetch with emails."""
        mock_search_results = [
            {"href": "https://acme-corp.com/contact", "title": "Contact"},
            {"href": "https://acme-corp.com/about", "title": "About"},
        ]

        mock_page_html = """
        <html><body>
        Contact us at info@acme-corp.com or support@acme-corp.com
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = mock_page_html

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.asyncio.to_thread", return_value=mock_search_results), \
             patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await email_harvest("acme-corp.com")

        assert result.success
        assert result.data["count"] >= 1
        assert any("acme-corp.com" in e for e in result.data["emails"])

    @pytest.mark.asyncio
    async def test_email_harvest_no_results(self):
        """Mock DDG search returning no results."""
        with patch("src.tools.osint.asyncio.to_thread", return_value=[]):
            result = await email_harvest("nonexistent-domain-xyz.com")

        assert result.success
        assert "Không tìm thấy" in result.output

    @pytest.mark.asyncio
    async def test_email_harvest_excludes_junk(self):
        """Test that example.com and other junk emails are excluded."""
        mock_search_results = [
            {"href": "https://target.org/page", "title": "Page"},
        ]

        mock_page_html = """
        <html><body>
        Email: real@target.org and fake@example.com and image@test.png
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = mock_page_html

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.asyncio.to_thread", return_value=mock_search_results), \
             patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await email_harvest("target.org")

        assert result.success
        emails = result.data["emails"]
        assert "real@target.org" in emails
        assert "fake@example.com" not in emails

    @pytest.mark.asyncio
    async def test_email_harvest_invalid_domain(self):
        result = await email_harvest("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_email_harvest_clamps_max_pages(self):
        """Test that max_pages is clamped to 1-15."""
        with patch("src.tools.osint.asyncio.to_thread", return_value=[]) as mock_thread:
            await email_harvest("example.com", max_pages=100)
            # The clamped value (15) should be passed to the search function
            call_args = mock_thread.call_args
            assert call_args[0][2] == 15

    @pytest.mark.asyncio
    async def test_email_harvest_ddg_import_error(self):
        """Test graceful handling when DDG is not installed."""
        with patch("src.tools.osint.asyncio.to_thread", side_effect=ImportError("no module")):
            result = await email_harvest("example.com")

        assert not result.success
        assert "duckduckgo-search" in result.error


# ---------------------------------------------------------------------------
# 4. Wayback Machine Lookup
# ---------------------------------------------------------------------------

class TestWaybackLookup:
    @pytest.mark.asyncio
    async def test_wayback_lookup_found(self):
        """Mock CDX API returning snapshots."""
        mock_cdx_response = [
            ["timestamp", "original", "statuscode", "mimetype", "length"],
            ["20240615120000", "https://example.com", "200", "text/html", "12345"],
            ["20240101080000", "https://example.com", "200", "text/html", "11000"],
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_cdx_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await wayback_lookup("example.com")

        assert result.success
        assert result.data["count"] == 2
        assert "web.archive.org" in result.output
        assert "2024-06-15" in result.output

    @pytest.mark.asyncio
    async def test_wayback_lookup_no_snapshots(self):
        """Mock CDX API returning empty results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=[])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await wayback_lookup("totally-nonexistent-site-xyz.com")

        assert result.success
        assert "Không tìm thấy" in result.output
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_wayback_lookup_empty_url(self):
        result = await wayback_lookup("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_wayback_lookup_clamps_limit(self):
        """Test that limit is clamped to 1-50."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=[])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await wayback_lookup("example.com", limit=999)

        assert result.success

    @pytest.mark.asyncio
    async def test_wayback_lookup_timeout(self):
        """Test graceful timeout handling."""
        import httpx as httpx_module

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx_module.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await wayback_lookup("example.com")

        assert not result.success
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_wayback_lookup_archive_url_format(self):
        """Test that archive URLs are correctly formatted."""
        mock_cdx_response = [
            ["timestamp", "original", "statuscode", "mimetype", "length"],
            ["20230315143000", "https://example.com/page", "200", "text/html", "5000"],
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_cdx_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await wayback_lookup("example.com/page")

        assert result.success
        snapshot = result.data["snapshots"][0]
        assert snapshot["archive_url"] == "https://web.archive.org/web/20230315143000/https://example.com/page"
        assert snapshot["formatted_time"] == "2023-03-15 14:30:00"


# ---------------------------------------------------------------------------
# 5. GitHub Leaks
# ---------------------------------------------------------------------------

class TestGithubLeaks:
    @pytest.mark.asyncio
    async def test_github_leaks_code_results(self):
        """Mock GitHub search API returning code results."""
        mock_github_response = {
            "total_count": 2,
            "items": [
                {
                    "repository": {"full_name": "user/repo1"},
                    "path": ".env",
                    "html_url": "https://github.com/user/repo1/blob/main/.env",
                    "text_matches": [{"fragment": "API_KEY=sk-12345"}],
                },
                {
                    "repository": {"full_name": "user/repo2"},
                    "path": "config.yml",
                    "html_url": "https://github.com/user/repo2/blob/main/config.yml",
                    "text_matches": [],
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_github_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("example-corp", "code")

        assert result.success
        assert result.data["count"] == 2
        assert result.data["items"][0]["repo"] == "user/repo1"

    @pytest.mark.asyncio
    async def test_github_leaks_commits(self):
        """Mock GitHub search API returning commit results."""
        mock_github_response = {
            "total_count": 1,
            "items": [
                {
                    "repository": {"full_name": "user/repo1"},
                    "commit": {
                        "message": "add config with password",
                        "author": {"name": "Dev User"},
                    },
                    "html_url": "https://github.com/user/repo1/commit/abc123",
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_github_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("example-corp", "commits")

        assert result.success
        assert result.data["count"] == 1
        assert result.data["items"][0]["author"] == "Dev User"

    @pytest.mark.asyncio
    async def test_github_leaks_repos(self):
        """Mock GitHub search API returning repo results."""
        mock_github_response = {
            "total_count": 1,
            "items": [
                {
                    "full_name": "user/secrets-repo",
                    "description": "A repo with leaked secrets",
                    "html_url": "https://github.com/user/secrets-repo",
                    "stargazers_count": 5,
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_github_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("example-corp", "repos")

        assert result.success
        assert result.data["count"] == 1
        assert result.data["items"][0]["stars"] == 5

    @pytest.mark.asyncio
    async def test_github_leaks_rate_limited(self):
        """Test graceful handling of GitHub rate limits."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("example-corp", "code")

        assert not result.success
        assert "rate limit" in result.error.lower()

    @pytest.mark.asyncio
    async def test_github_leaks_invalid_search_type(self):
        result = await github_leaks("example", "invalid")
        assert not result.success
        assert "không hợp lệ" in result.error

    @pytest.mark.asyncio
    async def test_github_leaks_empty_query(self):
        result = await github_leaks("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_github_leaks_no_results(self):
        """Test handling of empty results."""
        mock_github_response = {
            "total_count": 0,
            "items": [],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_github_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("completely-unique-nonexistent-xyz", "code")

        assert result.success
        assert "Không tìm thấy" in result.output
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_github_leaks_includes_suggestions(self):
        """Test that suggested dork patterns are included in output."""
        mock_github_response = {"total_count": 0, "items": []}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_github_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("testcorp", "code")

        assert result.success
        assert "search patterns" in result.output.lower() or "Gợi ý" in result.output

    @pytest.mark.asyncio
    async def test_github_leaks_uses_token_when_available(self):
        """Test that GITHUB_TOKEN is used when available."""
        mock_github_response = {"total_count": 0, "items": []}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_github_response)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test123"}):
            result = await github_leaks("testcorp", "code")

        assert result.success
        # Verify the token was included in the request headers
        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "token ghp_test123"

    @pytest.mark.asyncio
    async def test_github_leaks_timeout(self):
        """Test graceful timeout handling."""
        import httpx as httpx_module

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx_module.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tools.osint.httpx.AsyncClient", return_value=mock_client):
            result = await github_leaks("testcorp", "code")

        assert not result.success
        assert "timed out" in result.error
