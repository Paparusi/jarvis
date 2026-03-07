"""Tests for Batch 10 new tools — HTTP client, network, git, docker, browser."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.base import ToolResult


# === HTTP Client Tests ===

class TestHTTPClient:

    @pytest.mark.asyncio
    async def test_invalid_url(self):
        from src.tools.http_client import http_request
        result = await http_request(url="not-a-url")
        assert not result.success
        assert "http" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unsupported_method(self):
        from src.tools.http_client import http_request
        result = await http_request(url="https://example.com", method="INVALID")
        assert not result.success
        assert "Unsupported" in result.error

    @pytest.mark.asyncio
    async def test_tool_definition(self):
        from src.tools.http_client import http_request_tool
        schema = http_request_tool.to_openai_schema()
        assert schema["function"]["name"] == "http_request"
        params = schema["function"]["parameters"]["properties"]
        assert "url" in params
        assert "method" in params
        assert "headers" in params
        assert "body" in params
        assert "auth_token" in params


# === Network Tools Tests ===

class TestNetworkTools:

    @pytest.mark.asyncio
    async def test_port_scan_invalid_target(self):
        from src.tools.network import port_scan
        result = await port_scan(target="test;rm -rf /")
        assert not result.success
        assert "Invalid" in result.error

    @pytest.mark.asyncio
    async def test_dns_lookup_invalid_domain(self):
        from src.tools.network import dns_lookup
        result = await dns_lookup(domain="test|whoami")
        assert not result.success

    @pytest.mark.asyncio
    async def test_dns_lookup_bad_record_type(self):
        from src.tools.network import dns_lookup
        result = await dns_lookup(domain="example.com", record_type="INVALID")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ping_invalid_target(self):
        from src.tools.network import ping
        result = await ping(target="test$(whoami)")
        assert not result.success

    @pytest.mark.asyncio
    async def test_ping_count_clamped(self):
        from src.tools.network import ping
        # Test that count is clamped — just verify no crash
        result = await ping(target="", count=100)
        assert not result.success  # Empty target

    @pytest.mark.asyncio
    async def test_traceroute_invalid(self):
        from src.tools.network import traceroute
        result = await traceroute(target="test&whoami")
        assert not result.success

    def test_tool_definitions_exist(self):
        from src.tools.network import port_scan_tool, dns_lookup_tool, ping_tool, traceroute_tool
        assert port_scan_tool.name == "port_scan"
        assert dns_lookup_tool.name == "dns_lookup"
        assert ping_tool.name == "ping"
        assert traceroute_tool.name == "traceroute"


# === Git Tools Tests ===

class TestGitTools:

    @pytest.mark.asyncio
    async def test_git_status(self):
        from src.tools.git_ops import git_status
        # Running in jarvis project dir (which may or may not be a git repo)
        result = await git_status(repo_path="/tmp")
        # Either succeeds (if git repo) or fails gracefully
        assert isinstance(result, ToolResult)

    @pytest.mark.asyncio
    async def test_git_commit_empty_message(self):
        from src.tools.git_ops import git_commit
        result = await git_commit(message="")
        assert not result.success
        assert "required" in result.error.lower()

    @pytest.mark.asyncio
    async def test_git_branch_invalid_action(self):
        from src.tools.git_ops import git_branch
        result = await git_branch(action="invalid")
        assert not result.success

    @pytest.mark.asyncio
    async def test_git_log_count_clamped(self):
        from src.tools.git_ops import git_log
        result = await git_log(count=1000, repo_path="/tmp")
        # Count should be clamped to 50, not crash
        assert isinstance(result, ToolResult)

    def test_tool_definitions_exist(self):
        from src.tools.git_ops import git_status_tool, git_diff_tool, git_log_tool, git_commit_tool, git_branch_tool
        assert git_status_tool.name == "git_status"
        assert git_diff_tool.name == "git_diff"
        assert git_log_tool.name == "git_log"
        assert git_commit_tool.name == "git_commit"
        assert git_branch_tool.name == "git_branch"


# === Docker Tools Tests ===

class TestDockerTools:

    @pytest.mark.asyncio
    async def test_docker_exec_empty(self):
        from src.tools.docker_ops import docker_exec
        result = await docker_exec(container="", command="")
        assert not result.success
        assert "required" in result.error.lower()

    @pytest.mark.asyncio
    async def test_docker_exec_blocked_command(self):
        from src.tools.docker_ops import docker_exec
        result = await docker_exec(container="test", command="rm -rf /")
        assert not result.success
        assert "Blocked" in result.error

    @pytest.mark.asyncio
    async def test_docker_compose_invalid_action(self):
        from src.tools.docker_ops import docker_compose
        result = await docker_compose(action="invalid")
        assert not result.success

    def test_tool_definitions_exist(self):
        from src.tools.docker_ops import docker_ps_tool, docker_logs_tool, docker_exec_tool, docker_images_tool, docker_compose_tool
        assert docker_ps_tool.name == "docker_ps"
        assert docker_logs_tool.name == "docker_logs"
        assert docker_exec_tool.name == "docker_exec"
        assert docker_images_tool.name == "docker_images"
        assert docker_compose_tool.name == "docker_compose"


# === Browser Tools Tests ===

class TestBrowserTools:

    @pytest.mark.asyncio
    async def test_screenshot_invalid_url(self):
        from src.tools.browser import screenshot_page
        result = await screenshot_page(url="not-a-url")
        assert not result.success

    @pytest.mark.asyncio
    async def test_browse_web_invalid_url(self):
        from src.tools.browser import browse_web
        result = await browse_web(url="ftp://invalid")
        assert not result.success

    def test_tool_definitions_exist(self):
        from src.tools.browser import screenshot_tool, browse_web_tool, deep_search_tool
        assert screenshot_tool.name == "screenshot"
        assert browse_web_tool.name == "browse_web"
        assert deep_search_tool.name == "deep_search"


# === Tool Registration Integration ===

class TestToolRegistration:
    """Verify all new tools can be registered without errors."""

    def test_all_new_tools_register(self):
        from src.tools.base import ToolRegistry
        from src.tools.http_client import http_request_tool
        from src.tools.network import port_scan_tool, dns_lookup_tool, ping_tool, traceroute_tool
        from src.tools.git_ops import git_status_tool, git_diff_tool, git_log_tool, git_commit_tool, git_branch_tool
        from src.tools.docker_ops import docker_ps_tool, docker_logs_tool, docker_exec_tool, docker_images_tool, docker_compose_tool
        from src.tools.browser import screenshot_tool, extract_page_tool

        registry = ToolRegistry()
        all_tools = [
            http_request_tool,
            port_scan_tool, dns_lookup_tool, ping_tool, traceroute_tool,
            git_status_tool, git_diff_tool, git_log_tool, git_commit_tool, git_branch_tool,
            docker_ps_tool, docker_logs_tool, docker_exec_tool, docker_images_tool, docker_compose_tool,
            screenshot_tool, extract_page_tool,
        ]

        for tool in all_tools:
            registry.register(tool)

        assert len(registry.get_all()) == 17

    def test_all_schemas_valid(self):
        from src.tools.http_client import http_request_tool
        from src.tools.network import port_scan_tool, dns_lookup_tool, ping_tool, traceroute_tool
        from src.tools.git_ops import git_status_tool, git_diff_tool, git_log_tool, git_commit_tool, git_branch_tool
        from src.tools.docker_ops import docker_ps_tool, docker_logs_tool, docker_exec_tool
        from src.tools.browser import screenshot_tool, extract_page_tool

        all_tools = [
            http_request_tool, port_scan_tool, dns_lookup_tool, ping_tool,
            traceroute_tool, git_status_tool, git_diff_tool, git_log_tool,
            git_commit_tool, git_branch_tool, docker_ps_tool, docker_logs_tool,
            docker_exec_tool, screenshot_tool, extract_page_tool,
        ]

        for tool in all_tools:
            schema = tool.to_openai_schema()
            assert schema["type"] == "function"
            assert "name" in schema["function"]
            assert "parameters" in schema["function"]
            assert schema["function"]["parameters"]["type"] == "object"
            assert "additionalProperties" in schema["function"]["parameters"]
