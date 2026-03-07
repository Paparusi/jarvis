"""MCP Bridge — Connect JARVIS to Model Context Protocol servers.

MCP (Model Context Protocol) allows JARVIS to discover and use external tools
from any MCP-compatible server (stdio or SSE transport).

Flow:
1. Read MCP server configs from config/mcp.yaml
2. Connect to each server → discover available tools
3. Register discovered tools in JARVIS ToolRegistry
4. Auto-generate skeleton SKILL.md for each MCP tool group
5. Handle tool execution by proxying to MCP server

Supports:
- stdio transport (local processes)
- SSE transport (remote HTTP servers)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.tools.base import ToolDefinition, ToolParameter, ToolResult, ToolRegistry
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("skills.mcp_bridge")

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_env_var(value: str) -> str:
    """Resolve ${ENV_VAR} references in config values."""
    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return _ENV_VAR_RE.sub(_replace, value)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""
    name: str
    transport: str  # "stdio" or "sse"
    command: str = ""  # For stdio: command to run
    args: list[str] = field(default_factory=list)
    url: str = ""  # For SSE: endpoint URL
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    server_name: str


class MCPConnection:
    """Manages connection to a single MCP server via stdio."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._tools: list[MCPTool] = []
        self._request_id = 0
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}

    async def connect(self) -> bool:
        """Connect to MCP server and discover tools."""
        if self.config.transport != "stdio":
            log.warning("unsupported_transport", transport=self.config.transport,
                       server=self.config.name)
            return False

        try:
            # Build env: start with current env, overlay MCP server env
            env = dict(os.environ)
            if self.config.env:
                for k, v in self.config.env.items():
                    env[k] = _resolve_env_var(v)
            self._process = await asyncio.create_subprocess_exec(
                self.config.command, *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=1024 * 1024,  # 1MB buffer for large tool lists
            )

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_loop())

            # Initialize MCP session
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "JARVIS", "version": "2.0"},
            })

            if not init_result:
                log.error("mcp_init_failed", server=self.config.name)
                return False

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            # Discover tools
            tools_result = await self._send_request("tools/list", {})
            if tools_result and "tools" in tools_result:
                for tool_data in tools_result["tools"]:
                    self._tools.append(MCPTool(
                        name=tool_data["name"],
                        description=tool_data.get("description", ""),
                        parameters=tool_data.get("inputSchema", {}),
                        server_name=self.config.name,
                    ))

            log.info("mcp_connected", server=self.config.name,
                    tools=len(self._tools))
            return True

        except FileNotFoundError:
            log.error("mcp_command_not_found", command=self.config.command,
                     server=self.config.name)
            return False
        except Exception as e:
            log.error("mcp_connect_error", server=self.config.name, error=str(e))
            return False

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return result or {"error": "No response from MCP server"}

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if self._reader_task:
            self._reader_task.cancel()
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        log.info("mcp_disconnected", server=self.config.name)

    @property
    def tools(self) -> list[MCPTool]:
        return self._tools

    async def _send_request(self, method: str, params: dict) -> dict | None:
        """Send JSON-RPC request and wait for response."""
        if not self._process or not self._process.stdin:
            return None

        self._request_id += 1
        req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        try:
            data = json.dumps(message) + "\n"
            self._process.stdin.write(data.encode())
            await self._process.stdin.drain()

            result = await asyncio.wait_for(future, timeout=30)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            log.warning("mcp_request_timeout", method=method, server=self.config.name)
            return None
        except Exception as e:
            self._pending.pop(req_id, None)
            log.error("mcp_request_error", method=method, error=str(e))
            return None

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """Read JSON-RPC responses from MCP server stdout."""
        if not self._process or not self._process.stdout:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                req_id = message.get("id")
                if req_id and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if "error" in message:
                        future.set_result({"error": message["error"]})
                    else:
                        future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("mcp_read_error", server=self.config.name, error=str(e))


class MCPBridge:
    """Bridge between MCP servers and JARVIS tool system.

    Discovers MCP tools and registers them as JARVIS tools.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry
        self._connections: dict[str, MCPConnection] = {}
        self._mcp_tools: dict[str, MCPTool] = {}  # tool_name → MCPTool

    async def connect_all(self) -> int:
        """Connect to all configured MCP servers and register tools.

        Returns number of tools discovered.
        """
        configs = self._load_configs()
        total_tools = 0

        for config in configs:
            if not config.enabled:
                continue

            conn = MCPConnection(config)
            if await conn.connect():
                self._connections[config.name] = conn

                # Register each MCP tool in JARVIS
                for mcp_tool in conn.tools:
                    self._register_mcp_tool(mcp_tool, conn)
                    total_tools += 1

        log.info("mcp_bridge_ready",
                servers=len(self._connections),
                tools=total_tools)
        return total_tools

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for conn in self._connections.values():
            await conn.disconnect()
        self._connections.clear()

    def _register_mcp_tool(self, mcp_tool: MCPTool, conn: MCPConnection) -> None:
        """Register an MCP tool as a JARVIS tool."""
        # Prefix with server name to avoid conflicts
        tool_name = f"mcp_{mcp_tool.server_name}_{mcp_tool.name}"

        # Convert JSON Schema parameters to ToolParameters
        params = []
        schema_props = mcp_tool.parameters.get("properties", {})
        required = mcp_tool.parameters.get("required", [])

        for pname, pschema in schema_props.items():
            params.append(ToolParameter(
                name=pname,
                type=pschema.get("type", "string"),
                description=pschema.get("description", pname),
                required=pname in required,
                default=pschema.get("default"),
            ))

        # Create handler that proxies to MCP server
        async def mcp_handler(_conn=conn, _tool_name=mcp_tool.name, **kwargs) -> ToolResult:
            try:
                result = await _conn.call_tool(_tool_name, kwargs)
                if "error" in result:
                    return ToolResult(
                        success=False,
                        output="",
                        error=str(result["error"]),
                    )

                # Extract content from MCP response
                content = result.get("content", [])
                if isinstance(content, list):
                    text_parts = [
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    output = "\n".join(text_parts) or str(content)
                else:
                    output = str(content)

                return ToolResult(success=True, output=output[:4000])

            except Exception as e:
                return ToolResult(success=False, output="", error=f"MCP error: {e}")

        tool_def = ToolDefinition(
            name=tool_name,
            description=f"[MCP:{mcp_tool.server_name}] {mcp_tool.description}",
            parameters=params,
            handler=mcp_handler,
            timeout_seconds=30,
        )

        self._tool_registry.register(tool_def)
        self._mcp_tools[tool_name] = mcp_tool

    def _load_configs(self) -> list[MCPServerConfig]:
        """Load MCP server configs from config/mcp.yaml."""
        config_path = get_project_root() / "config" / "mcp.yaml"

        if not config_path.exists():
            log.debug("no_mcp_config", path=str(config_path))
            return []

        try:
            import yaml
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}

            configs = []
            for name, server in data.get("servers", {}).items():
                configs.append(MCPServerConfig(
                    name=name,
                    transport=server.get("transport", "stdio"),
                    command=server.get("command", ""),
                    args=server.get("args", []),
                    url=server.get("url", ""),
                    env=server.get("env", {}),
                    enabled=server.get("enabled", True),
                ))

            return configs

        except ImportError:
            log.warning("pyyaml_not_installed")
            return []
        except Exception as e:
            log.error("mcp_config_error", error=str(e))
            return []

    def generate_skill_skeletons(self) -> list[str]:
        """Generate skeleton SKILL.md files for MCP tool groups.

        Returns list of file paths created.
        """
        created = []
        skills_dir = get_project_root() / "workspace" / "skills" / "mcp"
        skills_dir.mkdir(parents=True, exist_ok=True)

        # Group tools by server
        by_server: dict[str, list[MCPTool]] = {}
        for tool_name, mcp_tool in self._mcp_tools.items():
            by_server.setdefault(mcp_tool.server_name, []).append(mcp_tool)

        for server_name, tools in by_server.items():
            skill_dir = skills_dir / server_name
            skill_dir.mkdir(exist_ok=True)
            skill_path = skill_dir / "SKILL.md"

            tool_names = ", ".join(t.name for t in tools)
            tool_docs = []
            for t in tools:
                tool_docs.append(f"- `{t.name}`: {t.description}")

            content = f"""---
name: mcp-{server_name}
description: >
  Tools from MCP server '{server_name}'.
  Available tools: {tool_names}
version: 1.0.0
metadata:
  jarvis:
    auto_generated: true
    created_by: mcp_bridge
    category: mcp
    priority: 0.7
---

# MCP: {server_name}

## Available Tools
{chr(10).join(tool_docs)}

## Workflow
1. Identify which MCP tool is needed for the user's request
2. Call the appropriate tool with correct parameters
3. Format the result for the user

## Rules
- Always use the MCP-prefixed tool name (e.g., `mcp_{server_name}_toolname`)
- Handle errors gracefully — MCP servers may be unavailable
"""
            skill_path.write_text(content, encoding="utf-8")
            created.append(str(skill_path))
            log.info("mcp_skill_generated", server=server_name, tools=len(tools))

        return created

    def get_stats(self) -> dict:
        return {
            "connected_servers": len(self._connections),
            "mcp_tools": len(self._mcp_tools),
            "server_names": list(self._connections.keys()),
        }
