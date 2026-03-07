"""Tests for MCP Bridge — MCP server config, env resolution, and tool wrapping."""

import os
from pathlib import Path

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.skills.mcp_bridge import (
    MCPServerConfig,
    MCPTool,
    MCPConnection,
    MCPBridge,
    _resolve_env_var,
)


class TestMCPServerConfig:

    def test_config_structure(self):
        config = MCPServerConfig(
            name="test-server",
            transport="stdio",
            command="npx",
            args=["-y", "test-package"],
            env={"API_KEY": "secret"},
            enabled=True,
        )
        assert config.name == "test-server"
        assert config.transport == "stdio"
        assert config.enabled is True


class TestMCPTool:

    def test_tool_structure(self):
        tool = MCPTool(
            name="search",
            description="Search the web",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            server_name="brave-search",
        )
        assert tool.name == "search"
        assert tool.server_name == "brave-search"


class TestMCPBridge:

    def test_load_configs_no_file(self, tmp_path):
        bridge = MCPBridge(tool_registry=MagicMock())
        # Should not crash when config doesn't exist
        with patch("src.skills.mcp_bridge.get_project_root", return_value=tmp_path):
            configs = bridge._load_configs()
        assert configs == []

    def test_load_configs_empty(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mcp.yaml").write_text("servers:\n")

        bridge = MCPBridge(tool_registry=MagicMock())
        with patch("src.skills.mcp_bridge.get_project_root", return_value=tmp_path):
            configs = bridge._load_configs()
        assert configs == []

    def test_load_configs_with_servers(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mcp.yaml").write_text("""servers:
  brave-search:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-brave-search"]
    env:
      BRAVE_API_KEY: "test-key"
    enabled: true
  disabled-server:
    transport: stdio
    command: test
    enabled: false
""")

        bridge = MCPBridge(tool_registry=MagicMock())
        with patch("src.skills.mcp_bridge.get_project_root", return_value=tmp_path):
            configs = bridge._load_configs()

        assert len(configs) == 2  # All servers loaded (filtering happens in connect_all)
        enabled = [c for c in configs if c.enabled]
        assert len(enabled) == 1
        assert enabled[0].name == "brave-search"
        assert enabled[0].env["BRAVE_API_KEY"] == "test-key"

    def test_generate_skill_skeletons(self, tmp_path):
        bridge = MCPBridge(tool_registry=MagicMock())
        bridge._mcp_tools = {
            "search_web": MCPTool(
                name="search_web",
                description="Search the web for information",
                parameters={},
                server_name="test-server",
            ),
            "search_images": MCPTool(
                name="search_images",
                description="Search for images",
                parameters={},
                server_name="test-server",
            ),
        }

        with patch("src.skills.mcp_bridge.get_project_root", return_value=tmp_path):
            skeletons = bridge.generate_skill_skeletons()

        assert len(skeletons) == 1  # One per server
        assert "test-server" in skeletons[0]

    def test_get_stats_empty(self):
        bridge = MCPBridge(tool_registry=MagicMock())
        stats = bridge.get_stats()
        assert stats["connected_servers"] == 0
        assert stats["mcp_tools"] == 0

    def test_register_mcp_tool(self):
        from src.tools.base import ToolRegistry
        registry = ToolRegistry()
        bridge = MCPBridge(tool_registry=registry)
        conn = MagicMock()

        tool = MCPTool(
            name="get_me",
            description="Get current user info",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why"},
                },
                "required": ["reason"],
            },
            server_name="github",
        )
        bridge._register_mcp_tool(tool, conn)

        registered = registry.get("mcp_github_get_me")
        assert registered is not None
        assert "github" in registered.description
        assert registered.name == "mcp_github_get_me"
        param_names = [p.name for p in registered.parameters]
        assert "reason" in param_names
        # Check required flag
        reason_param = [p for p in registered.parameters if p.name == "reason"][0]
        assert reason_param.required is True

    def test_register_tool_no_params(self):
        from src.tools.base import ToolRegistry
        registry = ToolRegistry()
        bridge = MCPBridge(tool_registry=registry)
        conn = MagicMock()

        tool = MCPTool(name="simple", description="Simple tool",
                      parameters={}, server_name="test")
        bridge._register_mcp_tool(tool, conn)
        registered = registry.get("mcp_test_simple")
        assert registered is not None
        assert registered.parameters == []


class TestResolveEnvVar:
    def test_no_vars(self):
        assert _resolve_env_var("plain_text") == "plain_text"

    def test_single_var(self):
        os.environ["_TEST_MCP_VAR"] = "hello"
        assert _resolve_env_var("${_TEST_MCP_VAR}") == "hello"
        del os.environ["_TEST_MCP_VAR"]

    def test_var_in_string(self):
        os.environ["_TEST_MCP_TOKEN"] = "abc123"
        assert _resolve_env_var("Bearer ${_TEST_MCP_TOKEN}") == "Bearer abc123"
        del os.environ["_TEST_MCP_TOKEN"]

    def test_multiple_vars(self):
        os.environ["_TEST_A"] = "foo"
        os.environ["_TEST_B"] = "bar"
        assert _resolve_env_var("${_TEST_A}/${_TEST_B}") == "foo/bar"
        del os.environ["_TEST_A"]
        del os.environ["_TEST_B"]

    def test_missing_var_keeps_template(self):
        result = _resolve_env_var("${_NONEXISTENT_VAR_99999}")
        assert result == "${_NONEXISTENT_VAR_99999}"

    def test_empty_string(self):
        assert _resolve_env_var("") == ""


class TestMCPConnection:
    def test_init(self):
        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        conn = MCPConnection(config)
        assert conn.config == config
        assert conn._process is None
        assert conn.tools == []

    @pytest.mark.asyncio
    async def test_unsupported_transport(self):
        config = MCPServerConfig(name="test", transport="sse", url="http://example.com")
        conn = MCPConnection(config)
        result = await conn.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        config = MCPServerConfig(
            name="test", transport="stdio",
            command="/nonexistent/binary_xyz", args=["stdio"],
        )
        conn = MCPConnection(config)
        result = await conn.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_disconnect_no_process(self):
        config = MCPServerConfig(name="test", transport="stdio")
        conn = MCPConnection(config)
        await conn.disconnect()  # Should not raise


class TestGitHubConfig:
    def test_mcp_yaml_has_github(self):
        """Verify config/mcp.yaml includes github server."""
        import yaml
        config_path = Path(__file__).parent.parent.parent / "config" / "mcp.yaml"
        if not config_path.exists():
            pytest.skip("No mcp.yaml")
        with open(config_path) as f:
            data = yaml.safe_load(f)
        servers = data.get("servers", {})
        assert "github" in servers
        gh = servers["github"]
        assert gh["transport"] == "stdio"
        assert "github-mcp-server" in gh["command"]
        assert "stdio" in gh["args"]
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in str(gh.get("env", {}))

    def test_github_binary_exists(self):
        """Verify github-mcp-server binary is installed."""
        binary = Path(__file__).parent.parent.parent / "bin" / "github-mcp-server"
        if not binary.exists():
            pytest.skip("Binary not installed")
        assert binary.stat().st_size > 0
        assert os.access(binary, os.X_OK)

    def test_github_skill_exists(self):
        """Verify GitHub SKILL.md was created."""
        skill = Path(__file__).parent.parent.parent / "workspace" / "skills" / "mcp" / "github" / "SKILL.md"
        if not skill.exists():
            pytest.skip("Skill not generated")
        content = skill.read_text()
        assert "mcp-github" in content
        assert "get_me" in content
        assert "search_repositories" in content
