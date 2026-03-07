"""Shell Executor Tool — Run commands in a sandboxed environment.

Safety measures:
- Whitelist of allowed commands
- Timeout enforcement
- Output size limits
- Dangerous command blocking
"""

from __future__ import annotations

import asyncio
import shlex

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.shell")

# Commands that are allowed to run
_ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "find", "grep", "sort", "uniq",
    "date", "cal", "echo", "pwd", "whoami", "uname", "df", "du",
    "python3", "python", "pip", "node", "npm", "git",
    "curl", "wget", "dig", "ping", "traceroute",
    "docker", "docker-compose",
    "jq", "sed", "awk", "cut", "tr", "tee",
    "tar", "zip", "unzip", "gzip", "gunzip",
    "which", "file", "stat", "md5sum", "sha256sum",
}

# Commands that are always blocked (destructive)
_BLOCKED_COMMANDS = {
    "rm", "rmdir", "mkfs", "dd", "fdisk", "shutdown", "reboot",
    "kill", "killall", "pkill",
    "chmod", "chown", "chgrp",
    "sudo", "su",
    "passwd", "useradd", "userdel",
    "iptables", "systemctl",
}

# Patterns that indicate dangerous operations
_DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r", "> /dev/", "| rm", "&& rm",
    ":(){ :|:& };:", "fork bomb",
    "mkfs.", "/dev/sd", "/dev/nvm",
    "dd if=", "dd of=",
]

MAX_OUTPUT_SIZE = 4000  # chars


def _is_safe_command(command: str) -> tuple[bool, str]:
    """Check if a command is safe to execute.

    Returns (is_safe, reason).
    """
    command_stripped = command.strip()

    # Check dangerous patterns
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in command_stripped.lower():
            return False, f"Lệnh chứa pattern nguy hiểm: '{pattern}'"

    # Extract base command
    try:
        parts = shlex.split(command_stripped)
    except ValueError:
        parts = command_stripped.split()

    if not parts:
        return False, "Lệnh trống"

    # Handle pipes and chains — check all commands
    all_cmds = []
    current = []
    for part in parts:
        if part in ("|", "&&", "||", ";"):
            if current:
                all_cmds.append(current[0])
            current = []
        else:
            current.append(part)
    if current:
        all_cmds.append(current[0])

    for cmd in all_cmds:
        # Strip path prefix
        base_cmd = cmd.split("/")[-1]

        if base_cmd in _BLOCKED_COMMANDS:
            return False, f"Lệnh '{base_cmd}' bị chặn vì lý do an toàn"

        if base_cmd not in _ALLOWED_COMMANDS:
            return False, f"Lệnh '{base_cmd}' chưa được cho phép. Các lệnh cho phép: {', '.join(sorted(_ALLOWED_COMMANDS))}"

    return True, ""


async def execute_shell(command: str, timeout: int = 30) -> ToolResult:
    """Execute a shell command with safety checks."""
    # Safety check
    is_safe, reason = _is_safe_command(command)
    if not is_safe:
        return ToolResult(
            success=False,
            output="",
            error=reason,
        )

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/home/admin_1",
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # Truncate if too large
        if len(stdout_text) > MAX_OUTPUT_SIZE:
            stdout_text = stdout_text[:MAX_OUTPUT_SIZE] + f"\n\n...(truncated, total {len(stdout.decode())} chars)"
        if len(stderr_text) > 1000:
            stderr_text = stderr_text[:1000] + "\n...(truncated)"

        output = stdout_text
        if stderr_text and process.returncode != 0:
            output = f"{stdout_text}\n\nSTDERR:\n{stderr_text}" if stdout_text else f"STDERR:\n{stderr_text}"

        return ToolResult(
            success=process.returncode == 0,
            output=output,
            error=stderr_text if process.returncode != 0 else "",
            data={"return_code": process.returncode},
        )

    except asyncio.TimeoutError:
        return ToolResult(
            success=False,
            output="",
            error=f"Lệnh bị timeout sau {timeout}s",
        )
    except Exception as e:
        log.error("shell_error", command=command[:100], error=str(e))
        return ToolResult(
            success=False,
            output="",
            error=f"Lỗi thực thi: {e}",
        )


# --- Tool Definition ---

shell_tool = ToolDefinition(
    name="run_command",
    description="Chạy lệnh terminal/shell. Dùng khi cần thực thi lệnh hệ thống, chạy script, kiểm tra hệ thống, hoặc xử lý dữ liệu. Chỉ hỗ trợ các lệnh an toàn.",
    parameters=[
        ToolParameter(
            name="command",
            type="string",
            description="Lệnh shell cần chạy (ví dụ: 'ls -la', 'python3 script.py', 'git status')",
        ),
        ToolParameter(
            name="timeout",
            type="integer",
            description="Timeout tính bằng giây (mặc định 30, tối đa 120)",
            required=False,
            default=30,
        ),
    ],
    handler=execute_shell,
    timeout_seconds=120,
)
