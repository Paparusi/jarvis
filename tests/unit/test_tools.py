"""Tests for JARVIS tool system."""

import asyncio

import pytest

from src.tools.base import ToolDefinition, ToolParameter, ToolRegistry, ToolResult


# --- Helper ---

async def mock_handler(text: str = "hello") -> ToolResult:
    return ToolResult(success=True, output=f"Echo: {text}")


async def failing_handler() -> ToolResult:
    raise ValueError("Boom!")


async def slow_handler() -> ToolResult:
    await asyncio.sleep(10)
    return ToolResult(success=True, output="done")


# --- ToolDefinition Tests ---

class TestToolDefinition:
    def test_to_openai_schema_basic(self):
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(name="query", type="string", description="Search query"),
            ],
        )
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert "query" in schema["function"]["parameters"]["properties"]
        assert "query" in schema["function"]["parameters"]["required"]

    def test_to_openai_schema_optional_param(self):
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(name="required_param", type="string", description="Required"),
                ToolParameter(name="optional_param", type="integer", description="Optional", required=False),
            ],
        )
        schema = tool.to_openai_schema()
        assert "required_param" in schema["function"]["parameters"]["required"]
        assert "optional_param" not in schema["function"]["parameters"]["required"]

    def test_to_openai_schema_enum(self):
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(name="lang", type="string", description="Language",
                              enum=["en", "vi"]),
            ],
        )
        schema = tool.to_openai_schema()
        assert schema["function"]["parameters"]["properties"]["lang"]["enum"] == ["en", "vi"]

    def test_to_openai_schema_no_params(self):
        tool = ToolDefinition(name="no_params", description="No params tool")
        schema = tool.to_openai_schema()
        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []


# --- ToolRegistry Tests ---

class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = ToolDefinition(name="test", description="Test tool")
        registry.register(tool)
        assert registry.get("test") is tool

    def test_get_nonexistent(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_all(self):
        registry = ToolRegistry()
        for name in ["a", "b", "c"]:
            registry.register(ToolDefinition(name=name, description=f"Tool {name}"))
        assert len(registry.get_all()) == 3

    def test_get_schemas(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="test",
            description="Test tool",
            parameters=[ToolParameter(name="x", type="string", description="X")],
        ))
        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "test"

    @pytest.mark.asyncio
    async def test_execute_success(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="echo",
            description="Echo tool",
            handler=mock_handler,
        ))
        result = await registry.execute("echo", text="world")
        assert result.success
        assert result.output == "Echo: world"
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        registry = ToolRegistry()
        result = await registry.execute("nonexistent")
        assert not result.success
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_error(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="fail",
            description="Failing tool",
            handler=failing_handler,
        ))
        result = await registry.execute("fail")
        assert not result.success
        assert "Boom!" in result.error

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="slow",
            description="Slow tool",
            handler=slow_handler,
            timeout_seconds=1,
        ))
        result = await registry.execute("slow")
        assert not result.success
        assert "timed out" in result.error

    def test_stats(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="a", description="A"))
        registry.register(ToolDefinition(name="b", description="B"))
        stats = registry.get_stats()
        assert stats["registered_tools"] == 2
        assert "a" in stats["tool_names"]
        assert "b" in stats["tool_names"]


# --- Shell Safety Tests ---

class TestShellSafety:
    def test_allowed_commands(self):
        from src.tools.shell import _is_safe_command
        assert _is_safe_command("ls -la")[0] is True
        assert _is_safe_command("date")[0] is True
        assert _is_safe_command("python3 --version")[0] is True
        assert _is_safe_command("git status")[0] is True

    def test_blocked_commands(self):
        from src.tools.shell import _is_safe_command
        assert _is_safe_command("rm -rf /")[0] is False
        assert _is_safe_command("sudo apt install")[0] is False
        assert _is_safe_command("kill -9 1234")[0] is False
        assert _is_safe_command("chmod 777 /etc/passwd")[0] is False

    def test_dangerous_patterns(self):
        from src.tools.shell import _is_safe_command
        assert _is_safe_command("echo foo > /dev/sda")[0] is False
        assert _is_safe_command("dd if=/dev/zero of=/dev/sda")[0] is False

    def test_piped_commands(self):
        from src.tools.shell import _is_safe_command
        assert _is_safe_command("ls | grep foo")[0] is True
        assert _is_safe_command("cat file | sort | uniq")[0] is True
        assert _is_safe_command("ls | rm")[0] is False

    @pytest.mark.asyncio
    async def test_shell_execute_safe(self):
        from src.tools.shell import execute_shell
        result = await execute_shell("echo hello")
        assert result.success
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_shell_execute_blocked(self):
        from src.tools.shell import execute_shell
        result = await execute_shell("rm -rf /tmp/test")
        assert not result.success
        assert "nguy hiểm" in result.error or "chặn" in result.error


# --- File Ops Safety Tests ---

class TestFileOpsSafety:
    def test_allowed_paths(self):
        from src.tools.file_ops import _is_path_allowed
        from pathlib import Path
        assert _is_path_allowed(Path("/tmp/test.txt"))[0] is True
        assert _is_path_allowed(Path.home() / "projects" / "test.txt")[0] is True

    def test_blocked_paths(self):
        from src.tools.file_ops import _is_path_allowed
        from pathlib import Path
        assert _is_path_allowed(Path("/etc/passwd"))[0] is False
        assert _is_path_allowed(Path("/root/.ssh/id_rsa"))[0] is False

    def test_blocked_patterns(self):
        from src.tools.file_ops import _is_path_allowed
        from pathlib import Path
        assert _is_path_allowed(Path("/tmp/.env"))[0] is False
        assert _is_path_allowed(Path("/tmp/credentials.json"))[0] is False
        assert _is_path_allowed(Path("/tmp/secret_key.txt"))[0] is False

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        from src.tools.file_ops import read_file
        result = await read_file("/tmp/nonexistent_jarvis_test_12345.txt")
        assert not result.success
        assert "không tồn tại" in result.error

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        from src.tools.file_ops import read_file, write_file
        import os
        path = "/tmp/jarvis_test_write.txt"
        try:
            r = await write_file(path, "test content 123")
            assert r.success

            r = await read_file(path)
            assert r.success
            assert "test content 123" in r.output
        finally:
            if os.path.exists(path):
                os.remove(path)
