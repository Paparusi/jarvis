"""Code Analysis Tools — AST analysis, complexity, dependencies, search, and diff.

Provides static analysis capabilities for Python codebases:
1. ast_analyze: Parse Python files, extract structure and metrics
2. complexity_check: Calculate cyclomatic complexity
3. dependency_graph: Map import relationships
4. code_search: Regex-based pattern search across files
5. diff_summary: Unified diff between strings or files
"""

from __future__ import annotations

import ast
import difflib
import re
import time
from pathlib import Path
from typing import Any

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.code_analysis")

MAX_OUTPUT = 5000  # chars


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Truncate text to limit, appending notice if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n...(truncated, total {len(text)} chars)"


def _validate_file(path: str, must_be_python: bool = False) -> tuple[Path | None, str]:
    """Validate that a file exists and optionally is a Python file.

    Returns (resolved_path, error_message). error_message is empty on success.
    """
    file_path = Path(path).expanduser().resolve()

    if not file_path.exists():
        return None, f"File không tồn tại: {path}"

    if not file_path.is_file():
        return None, f"Không phải file: {path}"

    if must_be_python and file_path.suffix != ".py":
        return None, f"Không phải file Python (.py): {path}"

    return file_path, ""


def _validate_directory(path: str) -> tuple[Path | None, str]:
    """Validate that a directory exists.

    Returns (resolved_path, error_message). error_message is empty on success.
    """
    dir_path = Path(path).expanduser().resolve()

    if not dir_path.exists():
        return None, f"Đường dẫn không tồn tại: {path}"

    if not dir_path.is_dir():
        return None, f"Không phải thư mục: {path}"

    return dir_path, ""


# ---------------------------------------------------------------------------
# 1. ast_analyze — Parse Python file, extract structure and metrics
# ---------------------------------------------------------------------------

async def ast_analyze(path: str) -> ToolResult:
    """Parse a Python file with ast and extract structure/metrics."""
    start = time.monotonic()

    file_path, error = _validate_file(path, must_be_python=True)
    if error:
        return ToolResult(success=False, output="", error=error)

    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Lỗi đọc file: {e}")

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False,
            output="",
            error=f"Lỗi cú pháp Python: {e}",
            execution_time_ms=elapsed,
        )

    classes: list[dict[str, Any]] = []
    functions: list[str] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append({"name": node.name, "methods": methods, "line": node.lineno})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level functions (not methods inside classes)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check parent — we already captured class methods above
                pass
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")

    # Collect only top-level functions (not methods)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)

    total_lines = len(source.splitlines())

    # Build output
    lines: list[str] = []
    lines.append(f"=== Phân tích: {file_path.name} ===")
    lines.append(f"Tổng dòng: {total_lines}")
    lines.append(f"Functions: {len(functions)}")
    lines.append(f"Classes: {len(classes)}")
    lines.append(f"Imports: {len(imports)}")
    lines.append("")

    if imports:
        lines.append("--- Imports ---")
        for imp in imports[:30]:
            lines.append(f"  {imp}")
        if len(imports) > 30:
            lines.append(f"  ...({len(imports) - 30} more)")
        lines.append("")

    if functions:
        lines.append("--- Functions ---")
        for fn in functions:
            lines.append(f"  def {fn}()")
        lines.append("")

    if classes:
        lines.append("--- Classes ---")
        for cls in classes:
            lines.append(f"  class {cls['name']} (line {cls['line']})")
            for method in cls["methods"]:
                lines.append(f"    def {method}()")
        lines.append("")

    output = _truncate("\n".join(lines))
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=True,
        output=output,
        data={
            "path": str(file_path),
            "lines": total_lines,
            "functions": len(functions),
            "classes": len(classes),
            "imports": len(imports),
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# 2. complexity_check — Cyclomatic complexity of a Python file
# ---------------------------------------------------------------------------

def _count_complexity(source: str) -> list[dict[str, Any]]:
    """Calculate cyclomatic complexity per function/method.

    Counts decision points: if, elif, for, while, and, or, except, with,
    assert, and comprehension expressions. Base complexity is 1.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results: list[dict[str, Any]] = []

    func_nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    for func in func_nodes:
        complexity = 1  # Base complexity

        for node in ast.walk(func):
            if isinstance(node, ast.If):
                complexity += 1
            elif isinstance(node, ast.For):
                complexity += 1
            elif isinstance(node, ast.While):
                complexity += 1
            elif isinstance(node, ast.ExceptHandler):
                complexity += 1
            elif isinstance(node, ast.With):
                complexity += 1
            elif isinstance(node, ast.Assert):
                complexity += 1
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                # Each 'and'/'or' adds a branch
                complexity += len(node.values) - 1

        # Determine if it's a method (parent is a class)
        parent_class = None
        for cls_node in ast.walk(tree):
            if isinstance(cls_node, ast.ClassDef):
                for child in ast.iter_child_nodes(cls_node):
                    if child is func:
                        parent_class = cls_node.name
                        break

        name = f"{parent_class}.{func.name}" if parent_class else func.name

        results.append({
            "name": name,
            "complexity": complexity,
            "line": func.lineno,
        })

    return results


async def complexity_check(path: str) -> ToolResult:
    """Calculate cyclomatic complexity for all functions in a Python file."""
    start = time.monotonic()

    file_path, error = _validate_file(path, must_be_python=True)
    if error:
        return ToolResult(success=False, output="", error=error)

    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Lỗi đọc file: {e}")

    results = _count_complexity(source)
    if not results:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Không tìm thấy function nào trong {file_path.name}",
            execution_time_ms=elapsed,
        )

    # Sort by complexity descending
    results.sort(key=lambda r: r["complexity"], reverse=True)

    lines: list[str] = []
    lines.append(f"=== Cyclomatic Complexity: {file_path.name} ===")
    lines.append("")

    total_complexity = 0
    high_complexity_count = 0

    for r in results:
        cc = r["complexity"]
        total_complexity += cc

        if cc <= 5:
            rating = "A (low)"
        elif cc <= 10:
            rating = "B (moderate)"
        elif cc <= 20:
            rating = "C (high)"
            high_complexity_count += 1
        else:
            rating = "D (very high)"
            high_complexity_count += 1

        lines.append(f"  {r['name']} (line {r['line']}): CC={cc} [{rating}]")

    lines.append("")
    avg_cc = total_complexity / len(results) if results else 0
    lines.append(f"Tổng functions: {len(results)}")
    lines.append(f"Trung bình CC: {avg_cc:.1f}")
    lines.append(f"Phức tạp cao (CC>10): {high_complexity_count}")

    output = _truncate("\n".join(lines))
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=True,
        output=output,
        data={
            "path": str(file_path),
            "functions": len(results),
            "avg_complexity": round(avg_cc, 1),
            "max_complexity": results[0]["complexity"] if results else 0,
            "high_complexity_count": high_complexity_count,
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# 3. dependency_graph — Analyze imports in a file or directory
# ---------------------------------------------------------------------------

def _extract_imports(file_path: Path) -> list[dict[str, str]]:
    """Extract import statements from a Python file."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    imports: list[dict[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "type": "import",
                    "module": alias.name,
                    "alias": alias.asname or "",
                })
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append({
                    "type": "from",
                    "module": module,
                    "name": alias.name,
                    "alias": alias.asname or "",
                })

    return imports


async def dependency_graph(path: str) -> ToolResult:
    """Analyze import dependencies in a Python file or directory."""
    start = time.monotonic()

    target = Path(path).expanduser().resolve()

    if not target.exists():
        return ToolResult(success=False, output="", error=f"Đường dẫn không tồn tại: {path}")

    # Collect Python files
    if target.is_file():
        if target.suffix != ".py":
            return ToolResult(success=False, output="", error=f"Không phải file Python: {path}")
        py_files = [target]
    else:
        py_files = sorted(target.rglob("*.py"))[:200]  # Limit to 200 files

    if not py_files:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output="Không tìm thấy file Python nào.",
            execution_time_ms=elapsed,
        )

    # Build dependency map: file -> list of imported modules
    dep_map: dict[str, list[str]] = {}
    all_modules: set[str] = set()

    for py_file in py_files:
        rel_name = str(py_file.relative_to(target)) if target.is_dir() else py_file.name
        file_imports = _extract_imports(py_file)

        modules = []
        for imp in file_imports:
            mod = imp["module"]
            if mod:
                # Use top-level module name for grouping
                top_module = mod.split(".")[0]
                modules.append(mod)
                all_modules.add(top_module)

        if modules:
            dep_map[rel_name] = sorted(set(modules))

    # Classify modules into stdlib, local, third-party
    stdlib_modules = {
        "os", "sys", "re", "json", "ast", "time", "datetime", "pathlib",
        "collections", "functools", "itertools", "typing", "dataclasses",
        "asyncio", "logging", "unittest", "textwrap", "tempfile", "shutil",
        "io", "math", "random", "hashlib", "hmac", "base64", "struct",
        "difflib", "copy", "abc", "enum", "contextlib", "signal",
        "threading", "multiprocessing", "subprocess", "socket", "http",
        "urllib", "email", "html", "xml", "csv", "sqlite3", "pickle",
        "shelve", "configparser", "argparse", "pprint", "traceback",
        "warnings", "inspect", "importlib", "pkgutil", "resource",
    }

    local_deps: set[str] = set()
    external_deps: set[str] = set()

    for mod in all_modules:
        if mod in stdlib_modules:
            continue
        elif mod.startswith("src") or mod.startswith("."):
            local_deps.add(mod)
        else:
            external_deps.add(mod)

    # Build output
    lines: list[str] = []
    lines.append(f"=== Dependency Graph: {target.name} ===")
    lines.append(f"Files phân tích: {len(py_files)}")
    lines.append(f"Files có imports: {len(dep_map)}")
    lines.append("")

    if local_deps:
        lines.append("--- Local Dependencies ---")
        for dep in sorted(local_deps):
            lines.append(f"  {dep}")
        lines.append("")

    if external_deps:
        lines.append("--- External Dependencies ---")
        for dep in sorted(external_deps):
            lines.append(f"  {dep}")
        lines.append("")

    lines.append("--- File Dependencies ---")
    for file_name, modules in sorted(dep_map.items()):
        lines.append(f"  {file_name}:")
        for mod in modules[:20]:
            lines.append(f"    -> {mod}")
        if len(modules) > 20:
            lines.append(f"    ...({len(modules) - 20} more)")

    output = _truncate("\n".join(lines))
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=True,
        output=output,
        data={
            "path": str(target),
            "files_analyzed": len(py_files),
            "local_deps": len(local_deps),
            "external_deps": len(external_deps),
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# 4. code_search — Regex-based pattern search across files
# ---------------------------------------------------------------------------

async def code_search(pattern: str, path: str = ".", file_glob: str = "*.py") -> ToolResult:
    """Search for a regex pattern across files in a directory."""
    start = time.monotonic()

    dir_path, error = _validate_directory(path)
    if error:
        return ToolResult(success=False, output="", error=error)

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(success=False, output="", error=f"Regex không hợp lệ: {e}")

    # Collect matching files
    files = sorted(dir_path.rglob(file_glob))[:500]  # Limit to 500 files

    matches: list[str] = []
    files_with_matches = 0
    total_matches = 0

    for file_path in files:
        if not file_path.is_file():
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        file_matches = []
        for line_no, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                rel_path = file_path.relative_to(dir_path)
                file_matches.append(f"{rel_path}:{line_no}: {line.strip()}")
                total_matches += 1

                if total_matches >= 200:  # Limit total matches
                    break

        if file_matches:
            files_with_matches += 1
            matches.extend(file_matches)

        if total_matches >= 200:
            break

    # Build output
    lines: list[str] = []
    lines.append(f"=== Tìm kiếm: '{pattern}' trong {dir_path.name}/ ===")
    lines.append(f"Files quét: {len(files)}")
    lines.append(f"Files khớp: {files_with_matches}")
    lines.append(f"Kết quả: {total_matches}")
    lines.append("")

    for match in matches:
        lines.append(match)

    if total_matches >= 200:
        lines.append("\n...(giới hạn 200 kết quả)")

    output = _truncate("\n".join(lines))
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=True,
        output=output,
        data={
            "path": str(dir_path),
            "pattern": pattern,
            "files_scanned": len(files),
            "files_matched": files_with_matches,
            "total_matches": total_matches,
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# 5. diff_summary — Unified diff between two strings or files
# ---------------------------------------------------------------------------

async def diff_summary(
    old: str = "",
    new: str = "",
    old_path: str = "",
    new_path: str = "",
) -> ToolResult:
    """Generate a unified diff summary between two texts or files."""
    start = time.monotonic()

    old_label = "old"
    new_label = "new"

    # Load from files if paths are provided
    if old_path:
        file_path, error = _validate_file(old_path)
        if error:
            return ToolResult(success=False, output="", error=f"old_path: {error}")
        try:
            old = file_path.read_text(encoding="utf-8")
            old_label = file_path.name
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Lỗi đọc old_path: {e}")

    if new_path:
        file_path, error = _validate_file(new_path)
        if error:
            return ToolResult(success=False, output="", error=f"new_path: {error}")
        try:
            new = file_path.read_text(encoding="utf-8")
            new_label = file_path.name
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Lỗi đọc new_path: {e}")

    if not old and not new:
        return ToolResult(
            success=False,
            output="",
            error="Cần cung cấp ít nhất 'old' hoặc 'new' (text hoặc file path)",
        )

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_label,
        tofile=new_label,
        lineterm="",
    ))

    if not diff:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output="Không có thay đổi (hai nội dung giống nhau).",
            execution_time_ms=elapsed,
        )

    # Compute summary stats
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    lines: list[str] = []
    lines.append(f"=== Diff Summary ===")
    lines.append(f"Thêm: +{additions} dòng")
    lines.append(f"Xóa: -{deletions} dòng")
    lines.append("")

    for diff_line in diff:
        lines.append(diff_line.rstrip())

    output = _truncate("\n".join(lines))
    elapsed = int((time.monotonic() - start) * 1000)

    return ToolResult(
        success=True,
        output=output,
        data={
            "additions": additions,
            "deletions": deletions,
            "total_changes": additions + deletions,
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

ast_analyze_tool = ToolDefinition(
    name="ast_analyze",
    description=(
        "Phân tích cấu trúc file Python bằng AST: trích xuất classes, functions, "
        "imports và tính metrics cơ bản (số dòng, số functions, số classes, số imports)."
    ),
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Đường dẫn đến file Python cần phân tích",
        ),
    ],
    handler=ast_analyze,
    timeout_seconds=15,
)

complexity_check_tool = ToolDefinition(
    name="complexity_check",
    description=(
        "Tính cyclomatic complexity của file Python. Đếm các nhánh điều kiện "
        "(if/elif/for/while/and/or/except/with/assert/comprehension) cho mỗi function."
    ),
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Đường dẫn đến file Python cần kiểm tra",
        ),
    ],
    handler=complexity_check,
    timeout_seconds=15,
)

dependency_graph_tool = ToolDefinition(
    name="dependency_graph",
    description=(
        "Phân tích imports trong file/thư mục Python để hiển thị quan hệ phụ thuộc. "
        "Phân loại: local, external, stdlib."
    ),
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Đường dẫn đến file hoặc thư mục Python",
        ),
    ],
    handler=dependency_graph,
    timeout_seconds=30,
)

code_search_tool = ToolDefinition(
    name="code_search",
    description=(
        "Tìm kiếm pattern (regex) trong các files của thư mục. "
        "Trả về file:line:match cho mỗi kết quả."
    ),
    parameters=[
        ToolParameter(
            name="pattern",
            type="string",
            description="Regex pattern cần tìm (ví dụ: 'def .*async', 'TODO|FIXME')",
        ),
        ToolParameter(
            name="path",
            type="string",
            description="Thư mục cần tìm (mặc định: thư mục hiện tại)",
            required=False,
            default=".",
        ),
        ToolParameter(
            name="file_glob",
            type="string",
            description="Glob pattern lọc file (mặc định: '*.py')",
            required=False,
            default="*.py",
        ),
    ],
    handler=code_search,
    timeout_seconds=30,
)

diff_summary_tool = ToolDefinition(
    name="diff_summary",
    description=(
        "So sánh hai nội dung text hoặc file, tạo unified diff summary. "
        "Có thể dùng text trực tiếp hoặc đường dẫn file."
    ),
    parameters=[
        ToolParameter(
            name="old",
            type="string",
            description="Nội dung cũ (text)",
            required=False,
            default="",
        ),
        ToolParameter(
            name="new",
            type="string",
            description="Nội dung mới (text)",
            required=False,
            default="",
        ),
        ToolParameter(
            name="old_path",
            type="string",
            description="Đường dẫn file cũ (thay thế cho 'old')",
            required=False,
            default="",
        ),
        ToolParameter(
            name="new_path",
            type="string",
            description="Đường dẫn file mới (thay thế cho 'new')",
            required=False,
            default="",
        ),
    ],
    handler=diff_summary,
    timeout_seconds=15,
)
