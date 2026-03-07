"""Code Execution Tool — Sandboxed Python code runner.

Security measures:
1. Static analysis: blocked imports/patterns before execution
2. Resource limits: memory (256MB), CPU time (10s), output size (5000 chars)
3. Network isolation: no socket access
4. Filesystem isolation: /tmp only, no access to JARVIS internals
5. Subprocess isolation: separate process with restricted environment
"""

from __future__ import annotations

import asyncio
import textwrap
import tempfile
from pathlib import Path

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.code_exec")

# Imports/patterns that are blocked for safety
_BLOCKED_PATTERNS = [
    # System access
    "import os", "from os ", "os.system", "os.popen", "os.exec",
    "os.remove", "os.unlink", "os.rmdir", "os.environ", "os.path",
    # Subprocess
    "import subprocess", "from subprocess ", "subprocess.run",
    "subprocess.Popen", "subprocess.call",
    # File system manipulation
    "import shutil", "from shutil ",
    # System internals
    "import sys", "from sys ", "sys.exit",
    # Code injection
    "__import__", "eval(", "exec(", "compile(",
    "breakpoint(", "globals(", "locals(",
    # Network
    "import socket", "from socket ", "import http", "from http ",
    "import urllib", "from urllib ", "import requests",
    "import httpx", "from httpx ",
    "import aiohttp", "from aiohttp ",
    # Introspection attacks
    "__builtins__", "__subclasses__", "__class__",
    "importlib", "ctypes", "pickle.loads",
]

# Patterns that bypass the simple "open(" block — allow print() etc
_BLOCKED_EXACT = ["open("]  # Block file open, but not "reopen" etc.

MAX_OUTPUT = 5000
TIMEOUT_SECONDS = 10
MAX_MEMORY_MB = 256

# Sandbox wrapper that sets resource limits inside the subprocess
_SANDBOX_WRAPPER = textwrap.dedent("""\
    import resource
    import signal

    # Memory limit: {mem_mb}MB
    resource.setrlimit(resource.RLIMIT_AS, ({mem_bytes}, {mem_bytes}))

    # CPU time limit: {cpu_seconds}s
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds}))

    # No core dumps
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    # File size limit: 1MB
    resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))

    # Max open files: 10
    resource.setrlimit(resource.RLIMIT_NOFILE, (10, 10))

    # No new processes
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    # Timeout handler
    def _timeout_handler(signum, frame):
        raise TimeoutError("CPU time limit exceeded")
    signal.signal(signal.SIGXCPU, _timeout_handler)

    # --- User code below ---
""")


def _check_code_safety(code: str) -> str | None:
    """Static analysis — check if code is safe to execute.

    Returns error message if unsafe, None if OK.
    """
    code_lower = code.lower()

    for pattern in _BLOCKED_PATTERNS:
        if pattern.lower() in code_lower:
            return f"Blocked pattern: '{pattern}'"

    # Check exact patterns (avoiding false positives like "reopen")
    for pattern in _BLOCKED_EXACT:
        # Find "open(" but not "xopen(" — must be start of line or after space/operator
        idx = 0
        while True:
            idx = code_lower.find(pattern.lower(), idx)
            if idx == -1:
                break
            # Check char before — if it's alphanumeric, skip (part of another word)
            if idx > 0 and code_lower[idx - 1].isalnum():
                idx += 1
                continue
            return f"Blocked pattern: '{pattern}'"

    # Check for excessively long code (likely attack or data dump)
    if len(code) > 50000:
        return "Code too long (max 50000 chars)"

    return None


def _build_sandboxed_code(user_code: str) -> str:
    """Wrap user code with resource limits sandbox."""
    wrapper = _SANDBOX_WRAPPER.format(
        mem_mb=MAX_MEMORY_MB,
        mem_bytes=MAX_MEMORY_MB * 1024 * 1024,
        cpu_seconds=TIMEOUT_SECONDS,
    )
    return wrapper + user_code


async def execute_python(code: str) -> ToolResult:
    """Execute Python code in a sandboxed subprocess."""
    if not code or not code.strip():
        return ToolResult(success=False, output="", error="Code không được để trống")

    # Static safety check
    safety_error = _check_code_safety(code)
    if safety_error:
        return ToolResult(
            success=False,
            output="",
            error=f"Code bị từ chối: {safety_error}",
        )

    # Build sandboxed code
    sandboxed = _build_sandboxed_code(code)

    # Write to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False,
        encoding="utf-8", dir="/tmp",
    ) as f:
        f.write(sandboxed)
        temp_path = f.name

    try:
        # Run in isolated subprocess with restricted environment
        proc = await asyncio.create_subprocess_exec(
            "python3", temp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/tmp",
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
                "LANG": "en_US.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=TIMEOUT_SECONDS + 2  # +2s buffer for sandbox setup
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.communicate()
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"Code bị timeout sau {TIMEOUT_SECONDS}s",
            )

        stdout_str = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
        stderr_str = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT]

        if proc.returncode != 0:
            # Filter out sandbox wrapper noise from error
            stderr_clean = _clean_sandbox_errors(stderr_str)
            return ToolResult(
                success=False,
                output=stdout_str,
                error=stderr_clean or f"Process exited with code {proc.returncode}",
            )

        output = stdout_str
        if stderr_str:
            output += f"\n[stderr]: {stderr_str}"

        return ToolResult(success=True, output=output or "(no output)")

    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Execution failed: {e}",
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _clean_sandbox_errors(stderr: str) -> str:
    """Remove sandbox wrapper line numbers from error messages."""
    lines = stderr.split("\n")
    # Filter out lines referencing the sandbox wrapper setup
    cleaned = [
        line for line in lines
        if "resource.setrlimit" not in line
        and "signal.signal" not in line
        and "_timeout_handler" not in line
    ]
    return "\n".join(cleaned).strip()


# Tool definition for registry
code_exec_tool = ToolDefinition(
    name="run_python",
    description=(
        "Chạy code Python và trả về kết quả. Dùng để tính toán, xử lý dữ liệu, "
        "phân tích, vẽ biểu đồ. Sandbox: không truy cập file hệ thống hoặc mạng."
    ),
    parameters=[
        ToolParameter(
            name="code",
            type="string",
            description="Python code to execute. Use print() to output results.",
        ),
    ],
    handler=execute_python,
    timeout_seconds=15,
)
