"""Data Analysis Tools — CSV, JSON, SQLite, and text analysis utilities.

Provides read-only data inspection and transformation tools:
- csv_analyze: Basic stats and preview of CSV files
- json_query: Dot-notation queries into JSON files
- sqlite_query: Read-only SQL queries on SQLite databases
- text_stats: Line/word/char counts and frequency analysis
- json_transform: Pretty-print, minify, extract keys, flatten JSON
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.data_tools")

MAX_OUTPUT = 5000

# --- Helpers ---


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Truncate text to limit, appending notice if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(truncated, total {len(text)} chars)"


def _validate_file(path_str: str, extensions: list[str] | None = None) -> tuple[Path | None, str]:
    """Validate that a file exists and optionally has expected extension.

    Returns (resolved_path, error_message). If error, path is None.
    """
    try:
        file_path = Path(path_str).expanduser().resolve()
    except Exception as e:
        return None, f"Invalid path: {e}"

    if not file_path.exists():
        return None, f"File not found: {path_str}"

    if not file_path.is_file():
        return None, f"Not a file: {path_str}"

    if extensions:
        if file_path.suffix.lower() not in extensions:
            return None, f"Expected file types: {', '.join(extensions)}, got '{file_path.suffix}'"

    return file_path, ""


# --- csv_analyze ---


async def csv_analyze(file_path: str, max_rows: int = 5) -> ToolResult:
    """Read a CSV file and show columns, row count, basic stats, and sample rows."""
    start = time.monotonic()

    path, err = _validate_file(file_path, [".csv", ".tsv"])
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Cannot read file: {e}")

    try:
        # Detect delimiter
        dialect = csv.Sniffer().sniff(content[:4096])
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "," if path.suffix.lower() == ".csv" else "\t"

    try:
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        rows = list(reader)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"CSV parse error: {e}")

    if not rows:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=True, output="Empty CSV file (no rows).", execution_time_ms=elapsed)

    headers = rows[0]
    data_rows = rows[1:]
    total_rows = len(data_rows)
    num_cols = len(headers)

    lines = [
        f"=== CSV Analysis: {path.name} ===",
        f"Rows: {total_rows} | Columns: {num_cols}",
        f"Headers: {', '.join(headers)}",
    ]

    # Basic stats for numeric columns
    numeric_stats: dict[int, list[float]] = {}
    for col_idx in range(num_cols):
        values: list[float] = []
        for row in data_rows:
            if col_idx < len(row) and row[col_idx].strip():
                try:
                    values.append(float(row[col_idx].strip()))
                except ValueError:
                    pass
        if values:
            numeric_stats[col_idx] = values

    if numeric_stats:
        lines.append("\n--- Numeric Column Stats ---")
        for col_idx, values in numeric_stats.items():
            col_name = headers[col_idx] if col_idx < len(headers) else f"col_{col_idx}"
            min_val = min(values)
            max_val = max(values)
            mean_val = sum(values) / len(values)
            lines.append(
                f"  {col_name}: min={min_val:.4g}, max={max_val:.4g}, "
                f"mean={mean_val:.4g}, count={len(values)}"
            )

    # Sample rows
    max_rows = max(1, min(max_rows, 50))
    sample = data_rows[:max_rows]
    if sample:
        lines.append(f"\n--- Sample Rows (first {len(sample)}) ---")
        # Format as simple table
        col_widths = [len(h) for h in headers]
        for row in sample:
            for i, cell in enumerate(row):
                if i < num_cols:
                    col_widths[i] = max(col_widths[i], min(len(cell), 30))

        header_line = " | ".join(h[:30].ljust(col_widths[i]) for i, h in enumerate(headers))
        sep_line = "-+-".join("-" * col_widths[i] for i in range(num_cols))
        lines.append(header_line)
        lines.append(sep_line)
        for row in sample:
            cells = []
            for i in range(num_cols):
                cell = row[i][:30] if i < len(row) else ""
                cells.append(cell.ljust(col_widths[i]))
            lines.append(" | ".join(cells))

    elapsed = int((time.monotonic() - start) * 1000)
    output = _truncate("\n".join(lines))
    return ToolResult(success=True, output=output, execution_time_ms=elapsed)


# --- json_query ---


def _resolve_json_path(data: Any, query: str) -> Any:
    """Resolve a dot-notation path with optional array indices.

    Supports: "data.users[0].name", "items[2]", "key"
    """
    if not query or query == ".":
        return data

    # Tokenize: split on dots, then handle bracket indices
    parts: list[str | int] = []
    for segment in query.split("."):
        if not segment:
            continue
        # Check for array index: "users[0]"
        match = re.match(r"^(\w+)(\[(\d+)\])?$", segment)
        if match:
            parts.append(match.group(1))
            if match.group(3) is not None:
                parts.append(int(match.group(3)))
        else:
            parts.append(segment)

    current = data
    for part in parts:
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ValueError(f"Cannot index non-list with [{part}]")
            if part < 0 or part >= len(current):
                raise IndexError(f"Index [{part}] out of range (length {len(current)})")
            current = current[part]
        elif isinstance(current, dict):
            if part not in current:
                available = ", ".join(list(current.keys())[:10])
                raise KeyError(f"Key '{part}' not found. Available: {available}")
            current = current[part]
        else:
            raise ValueError(f"Cannot access '{part}' on {type(current).__name__}")

    return current


async def json_query(file_path: str, query: str) -> ToolResult:
    """Load a JSON file and query using dot-notation path."""
    start = time.monotonic()

    path, err = _validate_file(file_path, [".json", ".jsonl"])
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, output="", error=f"Invalid JSON: {e}")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Cannot read file: {e}")

    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Query cannot be empty")

    try:
        result = _resolve_json_path(data, query.strip())
    except (KeyError, IndexError, ValueError) as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=False, output="", error=str(e), execution_time_ms=elapsed)

    # Format output
    if isinstance(result, (dict, list)):
        output = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    else:
        output = str(result)

    elapsed = int((time.monotonic() - start) * 1000)
    return ToolResult(success=True, output=_truncate(output), execution_time_ms=elapsed)


# --- sqlite_query ---

_BLOCKED_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA\s+\w+\s*=)\b",
    re.IGNORECASE,
)


async def sqlite_query(db_path: str, query: str, max_rows: int = 20) -> ToolResult:
    """Execute a read-only SQL query on a SQLite database."""
    start = time.monotonic()

    path, err = _validate_file(db_path, [".db", ".sqlite", ".sqlite3"])
    if err:
        return ToolResult(success=False, output="", error=err)

    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Query cannot be empty")

    # Block write operations
    if _BLOCKED_SQL.search(query):
        return ToolResult(
            success=False,
            output="",
            error="Write operations are blocked. Only SELECT and read-only PRAGMA queries are allowed.",
        )

    max_rows = max(1, min(max_rows, 500))

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max_rows)
        total_available = len(rows)

        # Check if there are more rows
        extra = cursor.fetchone()
        has_more = extra is not None

        conn.close()
    except sqlite3.Error as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=False, output="", error=f"SQL error: {e}", execution_time_ms=elapsed)
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=False, output="", error=f"Database error: {e}", execution_time_ms=elapsed)

    if not columns:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=True, output="Query executed (no results).", execution_time_ms=elapsed)

    # Format results as table
    lines = [f"Columns: {', '.join(columns)}", f"Rows returned: {total_available}" + (" (more available)" if has_more else ""), ""]

    # Calculate column widths
    col_widths = [len(c) for c in columns]
    str_rows: list[list[str]] = []
    for row in rows:
        str_row = [str(row[i]) if row[i] is not None else "NULL" for i in range(len(columns))]
        str_rows.append(str_row)
        for i, cell in enumerate(str_row):
            col_widths[i] = max(col_widths[i], min(len(cell), 40))

    # Header
    header = " | ".join(c[:40].ljust(col_widths[i]) for i, c in enumerate(columns))
    sep = "-+-".join("-" * col_widths[i] for i in range(len(columns)))
    lines.append(header)
    lines.append(sep)

    for str_row in str_rows:
        line = " | ".join(cell[:40].ljust(col_widths[i]) for i, cell in enumerate(str_row))
        lines.append(line)

    elapsed = int((time.monotonic() - start) * 1000)
    output = _truncate("\n".join(lines))
    return ToolResult(success=True, output=output, execution_time_ms=elapsed)


# --- text_stats ---


async def text_stats(file_path: str) -> ToolResult:
    """Analyze a text file: line/word/char counts, frequent words, encoding detection."""
    start = time.monotonic()

    path, err = _validate_file(file_path)
    if err:
        return ToolResult(success=False, output="", error=err)

    # Try common encodings
    content = None
    detected_encoding = "unknown"
    for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252", "ascii"]:
        try:
            content = path.read_text(encoding=enc)
            detected_encoding = enc
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if content is None:
        return ToolResult(success=False, output="", error="Cannot decode file with supported encodings")

    lines_list = content.splitlines()
    line_count = len(lines_list)
    char_count = len(content)

    # Word extraction (simple split on non-alphanumeric)
    words = re.findall(r"\b\w+\b", content.lower())
    word_count = len(words)

    # Most frequent words (filter out short/common ones)
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "or", "and", "but", "not",
        "it", "this", "that", "he", "she", "we", "they", "i", "you",
    }
    filtered_words = [w for w in words if len(w) > 2 and w not in stop_words]
    freq = Counter(filtered_words).most_common(15)

    # Line length stats
    line_lengths = [len(line) for line in lines_list] if lines_list else [0]
    avg_line_len = sum(line_lengths) / max(len(line_lengths), 1)
    max_line_len = max(line_lengths)

    # File size
    file_size = path.stat().st_size
    if file_size < 1024:
        size_str = f"{file_size}B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.1f}KB"
    else:
        size_str = f"{file_size / (1024 * 1024):.1f}MB"

    output_lines = [
        f"=== Text Analysis: {path.name} ===",
        f"File size: {size_str}",
        f"Encoding: {detected_encoding}",
        f"Lines: {line_count}",
        f"Words: {word_count}",
        f"Characters: {char_count}",
        f"Avg line length: {avg_line_len:.1f} chars",
        f"Max line length: {max_line_len} chars",
    ]

    if freq:
        output_lines.append("\n--- Most Frequent Words ---")
        for word, count in freq:
            output_lines.append(f"  {word}: {count}")

    # Blank line stats
    blank_count = sum(1 for line in lines_list if not line.strip())
    if line_count > 0:
        output_lines.append(f"\nBlank lines: {blank_count} ({blank_count * 100 // line_count}%)")

    elapsed = int((time.monotonic() - start) * 1000)
    output = _truncate("\n".join(output_lines))
    return ToolResult(success=True, output=output, execution_time_ms=elapsed)


# --- json_transform ---


def _flatten_json(data: Any, prefix: str = "", sep: str = ".") -> dict[str, Any]:
    """Flatten a nested dict/list into dot-notation keys."""
    items: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            new_key = f"{prefix}{sep}{key}" if prefix else key
            if isinstance(value, (dict, list)):
                items.update(_flatten_json(value, new_key, sep))
            else:
                items[new_key] = value
    elif isinstance(data, list):
        for i, value in enumerate(data):
            new_key = f"{prefix}[{i}]"
            if isinstance(value, (dict, list)):
                items.update(_flatten_json(value, new_key, sep))
            else:
                items[new_key] = value
    else:
        items[prefix] = data
    return items


def _extract_keys(data: Any, prefix: str = "") -> list[str]:
    """Recursively extract all keys from nested JSON."""
    keys: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            keys.append(full_key)
            if isinstance(value, (dict, list)):
                keys.extend(_extract_keys(value, full_key))
    elif isinstance(data, list) and data:
        # Show structure from first element
        keys.extend(_extract_keys(data[0], f"{prefix}[]"))
    return keys


async def json_transform(input: str, action: str) -> ToolResult:
    """Transform JSON: pretty-print, minify, extract keys, or flatten."""
    start = time.monotonic()

    if not input or not input.strip():
        return ToolResult(success=False, output="", error="Input cannot be empty")

    valid_actions = {"pretty", "minify", "keys", "flatten"}
    if action not in valid_actions:
        return ToolResult(
            success=False,
            output="",
            error=f"Invalid action '{action}'. Must be one of: {', '.join(sorted(valid_actions))}",
        )

    # Try to load as file path first, then as raw JSON
    raw = input.strip()
    input_path = Path(raw).expanduser()
    if input_path.exists() and input_path.is_file():
        try:
            raw = input_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Cannot read file: {e}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, output="", error=f"Invalid JSON: {e}")

    if action == "pretty":
        output = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    elif action == "minify":
        output = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    elif action == "keys":
        all_keys = _extract_keys(data)
        output = f"Total keys: {len(all_keys)}\n\n" + "\n".join(all_keys)
    elif action == "flatten":
        flat = _flatten_json(data)
        output = json.dumps(flat, indent=2, ensure_ascii=False, default=str)
    else:
        output = ""

    elapsed = int((time.monotonic() - start) * 1000)
    return ToolResult(success=True, output=_truncate(output), execution_time_ms=elapsed)


# --- Tool Definitions ---

csv_analyze_tool = ToolDefinition(
    name="csv_analyze",
    description=(
        "Phân tích file CSV: hiển thị columns, số dòng, thống kê cơ bản "
        "(min/max/mean cho cột số), và các dòng mẫu."
    ),
    parameters=[
        ToolParameter(
            name="file_path",
            type="string",
            description="Đường dẫn đến file CSV cần phân tích",
        ),
        ToolParameter(
            name="max_rows",
            type="integer",
            description="Số dòng mẫu hiển thị (mặc định: 5)",
            required=False,
            default=5,
        ),
    ],
    handler=csv_analyze,
    timeout_seconds=30,
)

json_query_tool = ToolDefinition(
    name="json_query",
    description=(
        "Đọc file JSON và truy vấn bằng dot-notation path. "
        "Ví dụ: 'data.users[0].name' để lấy tên user đầu tiên."
    ),
    parameters=[
        ToolParameter(
            name="file_path",
            type="string",
            description="Đường dẫn đến file JSON",
        ),
        ToolParameter(
            name="query",
            type="string",
            description="Dot-notation query path (e.g., 'data.users[0].name')",
        ),
    ],
    handler=json_query,
    timeout_seconds=15,
)

sqlite_query_tool = ToolDefinition(
    name="sqlite_query",
    description=(
        "Thực thi SQL query read-only trên database SQLite. "
        "Chặn INSERT/UPDATE/DELETE/DROP/ALTER. Chỉ cho phép SELECT."
    ),
    parameters=[
        ToolParameter(
            name="db_path",
            type="string",
            description="Đường dẫn đến file SQLite database",
        ),
        ToolParameter(
            name="query",
            type="string",
            description="SQL query (chỉ SELECT)",
        ),
        ToolParameter(
            name="max_rows",
            type="integer",
            description="Số dòng kết quả tối đa (mặc định: 20)",
            required=False,
            default=20,
        ),
    ],
    handler=sqlite_query,
    timeout_seconds=30,
)

text_stats_tool = ToolDefinition(
    name="text_stats",
    description=(
        "Phân tích file text: đếm dòng, từ, ký tự, các từ xuất hiện nhiều nhất, "
        "phát hiện encoding."
    ),
    parameters=[
        ToolParameter(
            name="file_path",
            type="string",
            description="Đường dẫn đến file text cần phân tích",
        ),
    ],
    handler=text_stats,
    timeout_seconds=15,
)

json_transform_tool = ToolDefinition(
    name="json_transform",
    description=(
        "Chuyển đổi JSON: pretty-print, minify, liệt kê keys, hoặc flatten cấu trúc lồng nhau. "
        "Input có thể là raw JSON string hoặc đường dẫn file."
    ),
    parameters=[
        ToolParameter(
            name="input",
            type="string",
            description="Raw JSON string hoặc đường dẫn đến file JSON",
        ),
        ToolParameter(
            name="action",
            type="string",
            description="Hành động: 'pretty', 'minify', 'keys', hoặc 'flatten'",
            enum=["pretty", "minify", "keys", "flatten"],
        ),
    ],
    handler=json_transform,
    timeout_seconds=15,
)
