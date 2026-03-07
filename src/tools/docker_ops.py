"""Docker Tools — Container and image management.

Provides structured docker operations via async subprocess.
"""

from __future__ import annotations

import asyncio
import time

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.docker_ops")

_CMD_TIMEOUT = 60


async def _docker_cmd(args: list[str], timeout: int = _CMD_TIMEOUT) -> tuple[str, str, int]:
    """Run a docker command."""
    cmd = ["docker"] + args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Docker command timed out after {timeout}s", 1
    except FileNotFoundError:
        return "", "Docker not installed or not in PATH", 127


async def docker_ps(all_containers: bool = False, filter_name: str = "") -> ToolResult:
    """List Docker containers."""
    start = time.monotonic()

    args = ["ps", "--format", "table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
    if all_containers:
        args.append("-a")
    if filter_name:
        args.extend(["--filter", f"name={filter_name}"])

    stdout, stderr, rc = await _docker_cmd(args)
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=rc == 0,
        output=stdout or "(no containers)",
        error=stderr if rc != 0 else "",
        execution_time_ms=elapsed,
    )


async def docker_logs(container: str, tail: int = 50, follow: bool = False) -> ToolResult:
    """Get Docker container logs."""
    start = time.monotonic()

    if not container:
        return ToolResult(success=False, output="", error="Container name/ID required")

    tail = min(max(1, tail), 500)
    args = ["logs", "--tail", str(tail)]
    if not follow:
        args.append(container)
    else:
        args.append(container)

    stdout, stderr, rc = await _docker_cmd(args, timeout=10)
    elapsed = int((time.monotonic() - start) * 1000)

    output = stdout or stderr
    if len(output) > 5000:
        output = output[-5000:]  # Keep tail

    return ToolResult(success=rc == 0, output=output, execution_time_ms=elapsed)


async def docker_exec(container: str, command: str) -> ToolResult:
    """Execute a command inside a running container."""
    start = time.monotonic()

    if not container or not command:
        return ToolResult(success=False, output="", error="Container and command are required")

    # Block dangerous commands
    blocked = ["rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:"]
    if any(b in command for b in blocked):
        return ToolResult(success=False, output="", error="Blocked: dangerous command")

    args = ["exec", container, "sh", "-c", command]
    stdout, stderr, rc = await _docker_cmd(args)
    elapsed = int((time.monotonic() - start) * 1000)

    output = stdout or stderr
    if len(output) > 5000:
        output = output[:5000] + "\n...[truncated]"

    return ToolResult(success=rc == 0, output=output, execution_time_ms=elapsed)


async def docker_images(filter_name: str = "") -> ToolResult:
    """List Docker images."""
    start = time.monotonic()

    args = ["images", "--format", "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}"]
    if filter_name:
        args.append(filter_name)

    stdout, stderr, rc = await _docker_cmd(args)
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(success=rc == 0, output=stdout or "(no images)", execution_time_ms=elapsed)


async def docker_compose(action: str = "ps", project_path: str = "") -> ToolResult:
    """Docker Compose operations."""
    start = time.monotonic()

    if action not in ("ps", "up", "down", "restart", "logs", "build"):
        return ToolResult(success=False, output="", error=f"Unsupported action: {action}")

    args = ["compose"]
    if project_path:
        args.extend(["-f", project_path])

    if action == "up":
        args.extend(["up", "-d"])
    elif action == "logs":
        args.extend(["logs", "--tail", "50"])
    else:
        args.append(action)

    stdout, stderr, rc = await _docker_cmd(args, timeout=120 if action in ("up", "build") else 30)
    elapsed = int((time.monotonic() - start) * 1000)

    output = stdout or stderr
    return ToolResult(success=rc == 0, output=output, execution_time_ms=elapsed)


# Tool definitions

docker_ps_tool = ToolDefinition(
    name="docker_ps",
    description="List Docker containers with status, ports, and images.",
    parameters=[
        ToolParameter(name="all_containers", type="boolean", description="Show all (including stopped)", required=False, default=False),
        ToolParameter(name="filter_name", type="string", description="Filter by container name", required=False, default=""),
    ],
    handler=docker_ps,
)

docker_logs_tool = ToolDefinition(
    name="docker_logs",
    description="Get logs from a Docker container.",
    parameters=[
        ToolParameter(name="container", type="string", description="Container name or ID"),
        ToolParameter(name="tail", type="integer", description="Number of lines (1-500)", required=False, default=50),
    ],
    handler=docker_logs,
)

docker_exec_tool = ToolDefinition(
    name="docker_exec",
    description="Execute a command inside a running Docker container.",
    parameters=[
        ToolParameter(name="container", type="string", description="Container name or ID"),
        ToolParameter(name="command", type="string", description="Command to execute"),
    ],
    handler=docker_exec,
)

docker_images_tool = ToolDefinition(
    name="docker_images",
    description="List Docker images with size and creation time.",
    parameters=[
        ToolParameter(name="filter_name", type="string", description="Filter by image name", required=False, default=""),
    ],
    handler=docker_images,
)

docker_compose_tool = ToolDefinition(
    name="docker_compose",
    description="Docker Compose operations: ps, up, down, restart, logs, build.",
    parameters=[
        ToolParameter(
            name="action", type="string", description="Compose action",
            required=False, default="ps",
            enum=["ps", "up", "down", "restart", "logs", "build"],
        ),
        ToolParameter(name="project_path", type="string", description="Path to docker-compose.yml", required=False, default=""),
    ],
    handler=docker_compose,
)
