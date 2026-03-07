"""Git Tools — Structured git operations.

Safer than raw shell commands with structured output.
Works on any git repo the user has access to.
"""

from __future__ import annotations

import asyncio
import os
import time

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.git_ops")

_CMD_TIMEOUT = 30


async def _git_cmd(args: list[str], cwd: str = "", timeout: int = _CMD_TIMEOUT) -> tuple[str, str, int]:
    """Run a git command."""
    cmd = ["git"] + args
    work_dir = cwd or os.path.expanduser("~/projects")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Git command timed out after {timeout}s", 1
    except Exception as e:
        return "", str(e), 1


async def git_status(repo_path: str = "") -> ToolResult:
    """Get git status of a repository."""
    start = time.monotonic()

    stdout, stderr, rc = await _git_cmd(
        ["status", "--short", "--branch"], cwd=repo_path,
    )
    elapsed = int((time.monotonic() - start) * 1000)

    if rc != 0:
        return ToolResult(success=False, output="", error=stderr, execution_time_ms=elapsed)

    return ToolResult(success=True, output=stdout or "(clean)", execution_time_ms=elapsed)


async def git_diff(repo_path: str = "", staged: bool = False, file_path: str = "") -> ToolResult:
    """Get git diff output."""
    start = time.monotonic()

    args = ["diff", "--stat"]
    if staged:
        args.append("--cached")
    if file_path:
        args.extend(["--", file_path])

    stdout, stderr, rc = await _git_cmd(args, cwd=repo_path)

    # Also get the actual diff (limited)
    detail_args = ["diff"]
    if staged:
        detail_args.append("--cached")
    if file_path:
        detail_args.extend(["--", file_path])

    detail_out, _, _ = await _git_cmd(detail_args, cwd=repo_path)
    elapsed = int((time.monotonic() - start) * 1000)

    output = stdout
    if detail_out:
        # Limit diff output
        if len(detail_out) > 4000:
            detail_out = detail_out[:4000] + "\n...[truncated]"
        output += "\n" + detail_out

    return ToolResult(
        success=rc == 0,
        output=output or "(no changes)",
        error=stderr if rc != 0 else "",
        execution_time_ms=elapsed,
    )


async def git_log(
    repo_path: str = "",
    count: int = 10,
    oneline: bool = True,
) -> ToolResult:
    """Get git commit log."""
    start = time.monotonic()

    count = min(max(1, count), 50)  # Clamp 1-50
    args = ["log", f"-{count}"]
    if oneline:
        args.append("--oneline")
    else:
        args.extend(["--format=%H %an <%ae> %ai%n  %s%n"])

    stdout, stderr, rc = await _git_cmd(args, cwd=repo_path)
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=rc == 0,
        output=stdout or "(no commits)",
        error=stderr if rc != 0 else "",
        execution_time_ms=elapsed,
    )


async def git_commit(
    repo_path: str = "",
    message: str = "",
    add_all: bool = False,
) -> ToolResult:
    """Stage and commit changes."""
    start = time.monotonic()

    if not message:
        return ToolResult(success=False, output="", error="Commit message is required")

    # Stage files
    if add_all:
        _, stderr, rc = await _git_cmd(["add", "-A"], cwd=repo_path)
        if rc != 0:
            return ToolResult(success=False, output="", error=f"git add failed: {stderr}")

    # Commit
    stdout, stderr, rc = await _git_cmd(["commit", "-m", message], cwd=repo_path)
    elapsed = int((time.monotonic() - start) * 1000)

    output = stdout or stderr
    return ToolResult(success=rc == 0, output=output, execution_time_ms=elapsed)


async def git_branch(repo_path: str = "", action: str = "list", name: str = "") -> ToolResult:
    """List, create, or switch branches."""
    start = time.monotonic()

    if action == "list":
        stdout, stderr, rc = await _git_cmd(["branch", "-a", "--no-color"], cwd=repo_path)
    elif action == "create" and name:
        stdout, stderr, rc = await _git_cmd(["checkout", "-b", name], cwd=repo_path)
    elif action == "switch" and name:
        stdout, stderr, rc = await _git_cmd(["checkout", name], cwd=repo_path)
    elif action == "delete" and name:
        stdout, stderr, rc = await _git_cmd(["branch", "-d", name], cwd=repo_path)
    else:
        return ToolResult(success=False, output="", error="Invalid action or missing branch name")

    elapsed = int((time.monotonic() - start) * 1000)
    output = stdout or stderr
    return ToolResult(success=rc == 0, output=output, execution_time_ms=elapsed)


# Tool definitions

git_status_tool = ToolDefinition(
    name="git_status",
    description="Get git status — shows modified, staged, and untracked files.",
    parameters=[
        ToolParameter(
            name="repo_path", type="string",
            description="Path to git repository (default: ~/projects)",
            required=False, default="",
        ),
    ],
    handler=git_status,
)

git_diff_tool = ToolDefinition(
    name="git_diff",
    description="Show git diff — what changed in files. Can show staged or unstaged changes.",
    parameters=[
        ToolParameter(name="repo_path", type="string", description="Path to repo", required=False, default=""),
        ToolParameter(name="staged", type="boolean", description="Show staged changes only", required=False, default=False),
        ToolParameter(name="file_path", type="string", description="Specific file to diff", required=False, default=""),
    ],
    handler=git_diff,
)

git_log_tool = ToolDefinition(
    name="git_log",
    description="Show git commit history.",
    parameters=[
        ToolParameter(name="repo_path", type="string", description="Path to repo", required=False, default=""),
        ToolParameter(name="count", type="integer", description="Number of commits (1-50)", required=False, default=10),
        ToolParameter(name="oneline", type="boolean", description="One line per commit", required=False, default=True),
    ],
    handler=git_log,
)

git_commit_tool = ToolDefinition(
    name="git_commit",
    description="Stage and commit changes to git.",
    parameters=[
        ToolParameter(name="repo_path", type="string", description="Path to repo", required=False, default=""),
        ToolParameter(name="message", type="string", description="Commit message"),
        ToolParameter(name="add_all", type="boolean", description="Stage all changes before commit", required=False, default=False),
    ],
    handler=git_commit,
)

git_branch_tool = ToolDefinition(
    name="git_branch",
    description="List, create, switch, or delete git branches.",
    parameters=[
        ToolParameter(name="repo_path", type="string", description="Path to repo", required=False, default=""),
        ToolParameter(
            name="action", type="string", description="Branch action",
            required=False, default="list",
            enum=["list", "create", "switch", "delete"],
        ),
        ToolParameter(name="name", type="string", description="Branch name (for create/switch/delete)", required=False, default=""),
    ],
    handler=git_branch,
)
