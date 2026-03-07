"""File Operations Tool — Read, write, and manage files in workspace.

Safety:
- Operations restricted to workspace/ and /tmp/
- No access to system files, .env, secrets
"""

from __future__ import annotations

import os
from pathlib import Path

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("tools.file_ops")

# Allowed base directories
_ALLOWED_DIRS = [
    get_project_root() / "workspace",
    Path("/tmp"),
    Path.home() / "projects",
]

# Blocked file patterns
_BLOCKED_PATTERNS = {".env", "credentials", "secret", "password", "token", ".key", ".pem"}

MAX_READ_SIZE = 10000  # chars


def _is_path_allowed(path: Path) -> tuple[bool, str]:
    """Check if a file path is within allowed directories."""
    resolved = path.resolve()

    # Check blocked patterns in filename
    name_lower = resolved.name.lower()
    for pattern in _BLOCKED_PATTERNS:
        if pattern in name_lower:
            return False, f"Không được truy cập file chứa '{pattern}' trong tên"

    # Check if within allowed directories
    for allowed in _ALLOWED_DIRS:
        try:
            resolved.relative_to(allowed.resolve())
            return True, ""
        except ValueError:
            continue

    return False, f"Chỉ được truy cập: {', '.join(str(d) for d in _ALLOWED_DIRS)}"


async def read_file(path: str) -> ToolResult:
    """Read content from a file."""
    file_path = Path(path).expanduser()

    is_allowed, reason = _is_path_allowed(file_path)
    if not is_allowed:
        return ToolResult(success=False, output="", error=reason)

    if not file_path.exists():
        return ToolResult(success=False, output="", error=f"File không tồn tại: {path}")

    if not file_path.is_file():
        return ToolResult(success=False, output="", error=f"Không phải file: {path}")

    try:
        content = file_path.read_text(encoding="utf-8")
        if len(content) > MAX_READ_SIZE:
            content = content[:MAX_READ_SIZE] + f"\n\n...(truncated, total {len(content)} chars)"

        return ToolResult(
            success=True,
            output=content,
            data={"path": str(file_path), "size": file_path.stat().st_size},
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Lỗi đọc file: {e}")


async def write_file(path: str, content: str) -> ToolResult:
    """Write content to a file."""
    file_path = Path(path).expanduser()

    is_allowed, reason = _is_path_allowed(file_path)
    if not is_allowed:
        return ToolResult(success=False, output="", error=reason)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

        return ToolResult(
            success=True,
            output=f"Đã ghi {len(content)} ký tự vào {path}",
            data={"path": str(file_path), "size": len(content)},
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Lỗi ghi file: {e}")


async def list_directory(path: str = "", pattern: str = "*") -> ToolResult:
    """List files in a directory."""
    if not path:
        path = str(get_project_root() / "workspace")

    dir_path = Path(path).expanduser()

    is_allowed, reason = _is_path_allowed(dir_path)
    if not is_allowed:
        return ToolResult(success=False, output="", error=reason)

    if not dir_path.exists():
        return ToolResult(success=False, output="", error=f"Thư mục không tồn tại: {path}")

    if not dir_path.is_dir():
        return ToolResult(success=False, output="", error=f"Không phải thư mục: {path}")

    try:
        entries = sorted(dir_path.glob(pattern))
        lines = [f"Nội dung thư mục: {path}\n"]
        for entry in entries[:100]:  # Max 100 entries
            rel = entry.relative_to(dir_path)
            if entry.is_dir():
                lines.append(f"  {rel}/")
            else:
                size = entry.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f}MB"
                lines.append(f"  {rel} ({size_str})")

        if len(entries) > 100:
            lines.append(f"\n...(+{len(entries) - 100} more entries)")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"path": str(dir_path), "count": len(entries)},
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Lỗi list directory: {e}")


# --- Tool Definitions ---

read_file_tool = ToolDefinition(
    name="read_file",
    description="Đọc nội dung file. Chỉ truy cập được files trong workspace/ và /tmp/.",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Đường dẫn đến file cần đọc",
        ),
    ],
    handler=read_file,
    timeout_seconds=10,
)

write_file_tool = ToolDefinition(
    name="write_file",
    description="Ghi nội dung vào file. Tạo file mới hoặc ghi đè file cũ. Chỉ truy cập được workspace/ và /tmp/.",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Đường dẫn file cần ghi",
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Nội dung cần ghi vào file",
        ),
    ],
    handler=write_file,
    timeout_seconds=10,
)

list_dir_tool = ToolDefinition(
    name="list_directory",
    description="Liệt kê nội dung thư mục. Mặc định hiển thị thư mục workspace/.",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Đường dẫn thư mục (mặc định: workspace/)",
            required=False,
        ),
        ToolParameter(
            name="pattern",
            type="string",
            description="Glob pattern để lọc (mặc định: '*')",
            required=False,
        ),
    ],
    handler=list_directory,
    timeout_seconds=10,
)
