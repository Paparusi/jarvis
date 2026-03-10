"""Tool System — Base framework for JARVIS tool execution.

Defines the interface for tools that the agent loop can call:
- ToolDefinition: describes a tool (name, description, parameters schema)
- ToolResult: output from tool execution
- ToolRegistry: register and discover tools
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from src.utils.logging import get_logger

log = get_logger("tools.base")


@dataclass
class ToolParameter:
    """A single parameter for a tool."""

    name: str
    type: str  # "string", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: list[str] | None = None
    default: Any = None


@dataclass
class ToolDefinition:
    """Describes a tool that can be called by the agent loop.

    The schema is compatible with Claude's tool_use format.
    """

    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    handler: Callable[..., Coroutine[Any, Any, ToolResult]] | None = None
    timeout_seconds: int = 30
    requires_confirmation: bool = False  # Ask user before executing

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible function schema (used by ClaudeClient).

        Follows best practices:
        - additionalProperties: false prevents hallucinated params
        - default values guide the LLM when optional params are omitted
        """
        properties = {}
        required = []

        for param in self.parameters:
            prop: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }


@dataclass
class ToolResult:
    """Result from a tool execution."""

    success: bool
    output: str
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: int = 0


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._usage: dict[str, int] = {}
        self._event_bus = None  # Lazy import to avoid circular deps

    def _get_bus(self):
        if self._event_bus is None:
            from src.gateway.event_bus import get_event_bus
            self._event_bus = get_event_bus()
        return self._event_bus

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._usage.setdefault(tool.name, 0)
        log.info("tool_registered", name=tool.name)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def get_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas for LLM function calling."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def get_filtered(self, tool_names: set[str]) -> list[ToolDefinition]:
        """Get tools filtered by name set."""
        return [t for name, t in self._tools.items() if name in tool_names]

    def get_filtered_schemas(self, tool_names: set[str]) -> list[dict[str, Any]]:
        """Get tool schemas filtered by name set (for LLM function calling)."""
        return [
            t.to_openai_schema()
            for name, t in self._tools.items()
            if name in tool_names
        ]

    def _apply_defaults(self, tool: ToolDefinition, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Apply default values for missing optional parameters and coerce types."""
        result = dict(kwargs)
        for param in tool.parameters:
            if param.name not in result and param.default is not None:
                result[param.name] = param.default
            # Type coercion — LLMs sometimes send "5" instead of 5
            if param.name in result:
                val = result[param.name]
                if param.type == "integer" and isinstance(val, str):
                    try:
                        result[param.name] = int(val)
                    except (ValueError, TypeError):
                        pass
                elif param.type == "boolean" and isinstance(val, str):
                    result[param.name] = val.lower() in ("true", "1", "yes")
        return result

    async def execute(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute a tool by name with given arguments."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' not found",
            )

        if not tool.handler:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' has no handler",
            )

        # Apply defaults and coerce types
        kwargs = self._apply_defaults(tool, kwargs)

        # Publish tool_called event (safe — never blocks tool execution)
        try:
            bus = self._get_bus()
            await bus.publish("tool_called", {
                "tool": name, "args": {k: str(v)[:100] for k, v in kwargs.items()},
            }, source="tool_registry")
        except Exception as e:
            log.debug("event_bus_publish_failed", bus_event="tool_called", error=str(e))

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                tool.handler(**kwargs),
                timeout=tool.timeout_seconds,
            )
            result.execution_time_ms = int((time.monotonic() - start) * 1000)
            self._usage[name] = self._usage.get(name, 0) + 1
            log.info(
                "tool_executed",
                name=name,
                success=result.success,
                time_ms=result.execution_time_ms,
            )

            # Publish tool_completed event (safe)
            try:
                await bus.publish("tool_completed", {
                    "tool": name, "success": result.success,
                    "time_ms": result.execution_time_ms,
                }, source="tool_registry")
            except Exception as e:
                log.debug("event_bus_publish_failed", bus_event="tool_completed", error=str(e))

            return result

        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            log.warning("tool_timeout", name=name, timeout_s=tool.timeout_seconds)
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' timed out after {tool.timeout_seconds}s",
                execution_time_ms=elapsed,
            )
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            log.error("tool_error", name=name, error=str(e))
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' failed: {e}",
                execution_time_ms=elapsed,
            )

    def get_stats(self) -> dict[str, Any]:
        return {
            "registered_tools": len(self._tools),
            "tool_names": list(self._tools.keys()),
            "usage": dict(self._usage),
        }
