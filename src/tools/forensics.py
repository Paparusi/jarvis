"""Forensics Tools -- File metadata, steganography detection, IoC extraction, log analysis.

Provides digital forensics capabilities for security analysis and CTF:
1. file_metadata: Extract metadata from files (EXIF, PDF info, basic stats)
2. stego_detect: Detect steganography in images (LSB, metadata, visual)
3. ioc_extract: Extract Indicators of Compromise from text
4. log_analyze: Analyze log files for suspicious patterns
"""

from __future__ import annotations

import asyncio
import math
import mimetypes
import os
import re
import struct
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.forensics")

MAX_OUTPUT = 5000  # chars
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

_ALLOWED_DIRS = [
    Path.home() / "projects",
    Path("/tmp"),
    Path.home() / "workspace",
    Path.cwd() / "workspace",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validate_file_path(path: str) -> tuple[Path | None, str]:
    """Validate file path exists, is a file, and is within allowed directories.

    Returns (resolved_path, error_message). error_message is empty on success.
    """
    if not path or not path.strip():
        return None, "File path must not be empty"

    file_path = Path(path).expanduser().resolve()

    if not file_path.exists():
        return None, f"File khong ton tai: {path}"

    if not file_path.is_file():
        return None, f"Khong phai file: {path}"

    allowed = any(
        _is_subpath(file_path, allowed_dir)
        for allowed_dir in _ALLOWED_DIRS
    )
    if not allowed:
        return None, f"File nam ngoai thu muc cho phep: {path}"

    stat = file_path.stat()
    if stat.st_size > _MAX_FILE_SIZE:
        return None, f"File qua lon ({stat.st_size} bytes, max {_MAX_FILE_SIZE})"

    return file_path, ""


def _is_subpath(path: Path, parent: Path) -> bool:
    """Check if path is a subpath of parent directory."""
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Truncate text to limit, appending notice if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n...(truncated, total {len(text)} chars)"


def _format_size(size_bytes: int) -> str:
    """Format byte size into human-readable string."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    exp = min(int(math.log(size_bytes, 1024)), len(units) - 1)
    value = size_bytes / (1024 ** exp)
    return f"{value:.1f} {units[exp]}"


# ---------------------------------------------------------------------------
# EXIF tag names (subset for forensics)
# ---------------------------------------------------------------------------

_EXIF_TAGS: dict[int, str] = {
    0x010F: "Make",
    0x0110: "Model",
    0x0112: "Orientation",
    0x011A: "XResolution",
    0x011B: "YResolution",
    0x0131: "Software",
    0x0132: "DateTime",
    0x8769: "ExifIFDPointer",
    0x8825: "GPSInfoIFDPointer",
    0xA002: "ExifImageWidth",
    0xA003: "ExifImageHeight",
    0x9003: "DateTimeOriginal",
    0x9004: "DateTimeDigitized",
    0x920A: "FocalLength",
    0xA405: "FocalLengthIn35mmFilm",
}

_GPS_TAGS: dict[int, str] = {
    0x0001: "GPSLatitudeRef",
    0x0002: "GPSLatitude",
    0x0003: "GPSLongitudeRef",
    0x0004: "GPSLongitude",
    0x0005: "GPSAltitudeRef",
    0x0006: "GPSAltitude",
}


def _parse_exif_ifd(data: bytes, offset: int, byte_order: str,
                    tag_map: dict[int, str]) -> dict[str, Any]:
    """Parse a single IFD (Image File Directory) from TIFF/EXIF data."""
    results: dict[str, Any] = {}

    if offset + 2 > len(data):
        return results

    num_entries = struct.unpack_from(f"{byte_order}H", data, offset)[0]
    offset += 2

    for _ in range(num_entries):
        if offset + 12 > len(data):
            break

        tag, type_id, count, value_offset = struct.unpack_from(
            f"{byte_order}HHI I", data, offset,
        )
        offset += 12

        tag_name = tag_map.get(tag)
        if tag_name is None:
            continue

        value = _read_exif_value(data, type_id, count, value_offset, byte_order)
        if value is not None:
            results[tag_name] = value

    return results


def _read_exif_value(data: bytes, type_id: int, count: int,
                     value_offset: int, byte_order: str) -> Any:
    """Read a single EXIF value based on its type."""
    # Type 2 = ASCII string
    if type_id == 2:
        total_size = count
        if total_size <= 4:
            raw = struct.pack(f"{byte_order}I", value_offset)[:count]
        else:
            if value_offset + count > len(data):
                return None
            raw = data[value_offset:value_offset + count]
        return raw.decode("ascii", errors="replace").rstrip("\x00")

    # Type 3 = UNSIGNED SHORT
    if type_id == 3:
        if count == 1:
            return value_offset & 0xFFFF
        return value_offset

    # Type 4 = UNSIGNED LONG
    if type_id == 4:
        if count == 1:
            return value_offset
        return value_offset

    # Type 5 = UNSIGNED RATIONAL (two ULONGs)
    if type_id == 5:
        values = []
        pos = value_offset
        for _ in range(count):
            if pos + 8 > len(data):
                break
            num, den = struct.unpack_from(f"{byte_order}II", data, pos)
            values.append(num / den if den != 0 else 0)
            pos += 8
        if count == 1 and values:
            return values[0]
        return values

    return None


def _parse_jpeg_exif(file_data: bytes) -> dict[str, Any]:
    """Parse EXIF data from a JPEG file by finding APP1 marker."""
    results: dict[str, Any] = {}

    if len(file_data) < 4 or file_data[:2] != b"\xff\xd8":
        return results

    pos = 2
    while pos < len(file_data) - 4:
        if file_data[pos] != 0xFF:
            break

        marker = file_data[pos + 1]
        if marker == 0xE1:  # APP1 = EXIF
            seg_len = struct.unpack(">H", file_data[pos + 2:pos + 4])[0]
            exif_data = file_data[pos + 4:pos + 2 + seg_len]

            if exif_data[:6] == b"Exif\x00\x00":
                tiff_data = exif_data[6:]
                results = _parse_tiff_header(tiff_data)
            break

        # Skip to next marker
        if marker == 0xD9:  # EOI
            break
        if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8):
            pos += 2
            continue
        if pos + 4 > len(file_data):
            break
        seg_len = struct.unpack(">H", file_data[pos + 2:pos + 4])[0]
        pos += 2 + seg_len

    return results


def _parse_tiff_header(tiff_data: bytes) -> dict[str, Any]:
    """Parse TIFF header and extract IFD0 + GPS + EXIF sub-IFDs."""
    results: dict[str, Any] = {}

    if len(tiff_data) < 8:
        return results

    # Determine byte order
    if tiff_data[:2] == b"II":
        byte_order = "<"
    elif tiff_data[:2] == b"MM":
        byte_order = ">"
    else:
        return results

    # Verify TIFF magic
    magic = struct.unpack_from(f"{byte_order}H", tiff_data, 2)[0]
    if magic != 42:
        return results

    ifd0_offset = struct.unpack_from(f"{byte_order}I", tiff_data, 4)[0]

    # Parse IFD0
    ifd0 = _parse_exif_ifd(tiff_data, ifd0_offset, byte_order, _EXIF_TAGS)
    results.update(ifd0)

    # Parse GPS sub-IFD if present
    gps_offset = ifd0.get("GPSInfoIFDPointer")
    if isinstance(gps_offset, int) and gps_offset < len(tiff_data):
        gps_data = _parse_exif_ifd(tiff_data, gps_offset, byte_order, _GPS_TAGS)
        results.update(gps_data)

    # Parse EXIF sub-IFD if present
    exif_offset = ifd0.get("ExifIFDPointer")
    if isinstance(exif_offset, int) and exif_offset < len(tiff_data):
        exif_data = _parse_exif_ifd(tiff_data, exif_offset, byte_order, _EXIF_TAGS)
        results.update(exif_data)

    # Remove pointer entries from output
    results.pop("GPSInfoIFDPointer", None)
    results.pop("ExifIFDPointer", None)

    return results


def _parse_pdf_metadata(file_data: bytes) -> dict[str, str]:
    """Extract metadata from PDF /Info dictionary."""
    results: dict[str, str] = {}
    text = file_data[:10000].decode("latin-1", errors="replace")

    for key in ("Author", "Creator", "Producer", "Title", "Subject", "CreationDate", "ModDate"):
        pattern = rf"/{key}\s*\(([^)]*)\)"
        match = re.search(pattern, text)
        if match:
            results[key] = match.group(1)

    return results


# ---------------------------------------------------------------------------
# 1. file_metadata -- Extract metadata from files
# ---------------------------------------------------------------------------

async def file_metadata(file_path: str) -> ToolResult:
    """Extract metadata from a file including EXIF for images and info for PDFs.

    Reads basic filesystem stats, MIME type, and format-specific metadata
    using Python stdlib only (no PIL or external libraries).
    """
    start = time.monotonic()

    resolved, err = _validate_file_path(file_path)
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        stat_result = await asyncio.to_thread(os.stat, resolved)
        file_data = await asyncio.to_thread(resolved.read_bytes)

        mime_type, _ = mimetypes.guess_type(str(resolved))
        mime_type = mime_type or "application/octet-stream"

        meta: dict[str, Any] = {
            "filename": resolved.name,
            "path": str(resolved),
            "size": stat_result.st_size,
            "size_human": _format_size(stat_result.st_size),
            "mime_type": mime_type,
            "created": datetime.fromtimestamp(
                stat_result.st_ctime, tz=timezone.utc,
            ).isoformat(),
            "modified": datetime.fromtimestamp(
                stat_result.st_mtime, tz=timezone.utc,
            ).isoformat(),
            "accessed": datetime.fromtimestamp(
                stat_result.st_atime, tz=timezone.utc,
            ).isoformat(),
        }

        lines = [
            f"File Metadata: {resolved.name}",
            f"  Path: {resolved}",
            f"  Size: {meta['size_human']} ({stat_result.st_size} bytes)",
            f"  MIME: {mime_type}",
            f"  Created: {meta['created']}",
            f"  Modified: {meta['modified']}",
        ]

        # Format-specific metadata
        suffix = resolved.suffix.lower()

        if suffix in (".jpg", ".jpeg"):
            exif = await asyncio.to_thread(_parse_jpeg_exif, file_data)
            if exif:
                meta["exif"] = exif
                lines.append("")
                lines.append("EXIF Data:")
                for key, value in exif.items():
                    lines.append(f"  {key}: {value}")
            else:
                lines.append("")
                lines.append("EXIF Data: (none found)")

        elif suffix == ".png":
            png_info = _parse_png_chunks(file_data)
            if png_info:
                meta["png_info"] = png_info
                lines.append("")
                lines.append("PNG Info:")
                for key, value in png_info.items():
                    lines.append(f"  {key}: {value}")

        elif suffix == ".pdf":
            pdf_meta = await asyncio.to_thread(_parse_pdf_metadata, file_data)
            if pdf_meta:
                meta["pdf_info"] = pdf_meta
                lines.append("")
                lines.append("PDF Info:")
                for key, value in pdf_meta.items():
                    lines.append(f"  {key}: {value}")

        elif suffix in (".exe", ".elf", ".dll", ".so"):
            exe_info = _analyze_executable(file_data, suffix)
            if exe_info:
                meta["executable_info"] = exe_info
                lines.append("")
                lines.append("Executable Info:")
                for key, value in exe_info.items():
                    lines.append(f"  {key}: {value}")

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data=meta,
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("file_metadata_error", path=file_path, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Metadata extraction failed: {e}",
            execution_time_ms=elapsed,
        )


def _parse_png_chunks(data: bytes) -> dict[str, Any]:
    """Parse PNG header chunk for basic image info."""
    info: dict[str, Any] = {}

    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return info

    # IHDR is always the first chunk (offset 8)
    ihdr_len = struct.unpack(">I", data[8:12])[0]
    if data[12:16] == b"IHDR" and ihdr_len >= 13:
        width = struct.unpack(">I", data[16:20])[0]
        height = struct.unpack(">I", data[20:24])[0]
        bit_depth = data[24]
        color_type = data[25]
        info["width"] = width
        info["height"] = height
        info["bit_depth"] = bit_depth
        color_names = {0: "Grayscale", 2: "RGB", 3: "Indexed", 4: "Grayscale+Alpha", 6: "RGBA"}
        info["color_type"] = color_names.get(color_type, f"Unknown({color_type})")

    # Scan for tEXt chunks
    pos = 8
    while pos + 8 < len(data):
        chunk_len = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8].decode("ascii", errors="replace")

        if chunk_type == "tEXt" and chunk_len > 0:
            chunk_data = data[pos + 8:pos + 8 + min(chunk_len, 500)]
            parts = chunk_data.split(b"\x00", 1)
            if len(parts) == 2:
                key = parts[0].decode("ascii", errors="replace")
                val = parts[1].decode("utf-8", errors="replace")
                info[f"text_{key}"] = val

        if chunk_type == "IEND":
            break

        pos += 12 + chunk_len  # 4 (len) + 4 (type) + data + 4 (CRC)

    return info


def _analyze_executable(data: bytes, suffix: str) -> dict[str, Any]:
    """Extract basic info from executable files."""
    info: dict[str, Any] = {}

    # PE (Windows .exe/.dll)
    if data[:2] == b"MZ":
        info["format"] = "PE (Windows Executable)"
        if len(data) > 0x3C + 4:
            pe_offset = struct.unpack("<I", data[0x3C:0x3C + 4])[0]
            if pe_offset + 6 < len(data) and data[pe_offset:pe_offset + 4] == b"PE\x00\x00":
                machine = struct.unpack("<H", data[pe_offset + 4:pe_offset + 6])[0]
                arch_map = {0x14C: "x86 (32-bit)", 0x8664: "x86-64 (64-bit)", 0xAA64: "ARM64"}
                info["architecture"] = arch_map.get(machine, f"Unknown (0x{machine:04x})")

    # ELF (Linux)
    elif data[:4] == b"\x7fELF":
        info["format"] = "ELF (Linux Executable)"
        if len(data) > 18:
            elf_class = data[4]
            info["bits"] = "64-bit" if elf_class == 2 else "32-bit"
            endian = data[5]
            info["endianness"] = "big" if endian == 2 else "little"
            machine = struct.unpack("<H" if endian == 1 else ">H", data[18:20])[0]
            arch_map = {3: "x86", 62: "x86-64", 183: "ARM64", 40: "ARM"}
            info["architecture"] = arch_map.get(machine, f"Unknown (0x{machine:04x})")
    else:
        info["format"] = f"Unknown binary ({suffix})"

    # Extract printable strings preview (first 4KB)
    preview = data[:4096]
    strings = re.findall(rb"[\x20-\x7E]{6,}", preview)
    if strings:
        decoded = [s.decode("ascii") for s in strings[:10]]
        info["strings_preview"] = ", ".join(decoded)

    return info


# ---------------------------------------------------------------------------
# 2. stego_detect -- Detect steganography in images
# ---------------------------------------------------------------------------

async def stego_detect(file_path: str, method: str = "all") -> ToolResult:
    """Detect potential steganography in image files.

    Analyzes LSB distribution, metadata anomalies, and file structure
    for indicators of hidden data. Uses Python stdlib only.
    """
    start = time.monotonic()

    resolved, err = _validate_file_path(file_path)
    if err:
        return ToolResult(success=False, output="", error=err)

    valid_methods = ("lsb", "metadata", "visual", "all")
    method = method.lower()
    if method not in valid_methods:
        return ToolResult(
            success=False, output="",
            error=f"method phai la mot trong: {', '.join(valid_methods)}",
        )

    suffix = resolved.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".bmp", ".gif"):
        return ToolResult(
            success=False, output="",
            error=f"Chi ho tro file anh (PNG, JPG, BMP, GIF), nhan: {suffix}",
        )

    try:
        file_data = await asyncio.to_thread(resolved.read_bytes)
        file_size = len(file_data)

        findings: list[dict[str, Any]] = []
        lines = [
            f"Steganography Analysis: {resolved.name}",
            f"File size: {_format_size(file_size)}",
            "",
        ]

        run_lsb = method in ("lsb", "all")
        run_meta = method in ("metadata", "all")
        run_visual = method in ("visual", "all")

        # --- LSB Analysis ---
        if run_lsb:
            lsb_results = _analyze_lsb(file_data, suffix)
            lines.append("=== LSB Analysis ===")
            for item in lsb_results:
                findings.append(item)
                indicator = "[!]" if item["suspicious"] else "[OK]"
                lines.append(f"  {indicator} {item['description']}")
            lines.append("")

        # --- Metadata Analysis ---
        if run_meta:
            meta_results = _analyze_metadata_stego(file_data, suffix)
            lines.append("=== Metadata Analysis ===")
            for item in meta_results:
                findings.append(item)
                indicator = "[!]" if item["suspicious"] else "[OK]"
                lines.append(f"  {indicator} {item['description']}")
            lines.append("")

        # --- Visual / Structural Analysis ---
        if run_visual:
            visual_results = _analyze_visual_stego(file_data, suffix, file_size)
            lines.append("=== Structural Analysis ===")
            for item in visual_results:
                findings.append(item)
                indicator = "[!]" if item["suspicious"] else "[OK]"
                lines.append(f"  {indicator} {item['description']}")
            lines.append("")

        # Summary
        suspicious_count = sum(1 for f in findings if f["suspicious"])
        total_checks = len(findings)

        if suspicious_count == 0:
            confidence = "LOW"
            summary = "Khong phat hien dau hieu steganography"
        elif suspicious_count <= 2:
            confidence = "MEDIUM"
            summary = "Mot so dau hieu bat thuong, co the co du lieu an"
        else:
            confidence = "HIGH"
            summary = "Nhieu dau hieu bat thuong, kha nang cao co steganography"

        lines.append(f"Summary: {suspicious_count}/{total_checks} suspicious indicators")
        lines.append(f"Confidence: {confidence}")
        lines.append(f"Assessment: {summary}")

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "file": str(resolved),
                "suspicious_count": suspicious_count,
                "total_checks": total_checks,
                "confidence": confidence,
                "findings": findings,
            },
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("stego_detect_error", path=file_path, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Stego detection failed: {e}",
            execution_time_ms=elapsed,
        )


def _analyze_lsb(data: bytes, suffix: str) -> list[dict[str, Any]]:
    """Analyze LSB distribution for steganography indicators."""
    results: list[dict[str, Any]] = []

    # Skip headers: find pixel data region
    if suffix in (".jpg", ".jpeg"):
        # JPEG: check after SOS marker for LSB patterns in compressed data
        pixel_start = data.find(b"\xff\xda")
        if pixel_start == -1:
            pixel_start = min(200, len(data))
        else:
            pixel_start += 2
    elif suffix == ".png":
        # PNG: check IDAT chunks
        pixel_start = data.find(b"IDAT")
        if pixel_start == -1:
            pixel_start = min(200, len(data))
    else:
        pixel_start = min(100, len(data))

    sample = data[pixel_start:pixel_start + 10000]
    if len(sample) < 100:
        results.append({
            "check": "lsb_distribution",
            "suspicious": False,
            "description": "File qua nho de phan tich LSB",
        })
        return results

    # Count LSBs
    lsb_zeros = 0
    lsb_ones = 0
    for byte in sample:
        if byte & 1:
            lsb_ones += 1
        else:
            lsb_zeros += 1

    total = lsb_zeros + lsb_ones
    ratio = lsb_ones / total if total > 0 else 0.5

    # Natural images have roughly 50/50 LSB distribution
    # Stego tools often create patterns (sequential, biased, etc.)
    deviation = abs(ratio - 0.5)

    results.append({
        "check": "lsb_distribution",
        "suspicious": deviation > 0.1,
        "description": (
            f"LSB ratio: {ratio:.3f} (0s: {lsb_zeros}, 1s: {lsb_ones}). "
            f"Deviation from 0.5: {deviation:.3f}"
            + (" - Significant bias detected" if deviation > 0.1 else " - Normal range")
        ),
    })

    # Check for sequential LSB patterns (common in simple LSB stego)
    lsb_sequence = bytes([b & 1 for b in sample[:2000]])
    # Count runs of same bit
    runs = 1
    for i in range(1, len(lsb_sequence)):
        if lsb_sequence[i] != lsb_sequence[i - 1]:
            runs += 1

    expected_runs = len(lsb_sequence) / 2
    run_ratio = runs / expected_runs if expected_runs > 0 else 1.0

    results.append({
        "check": "lsb_patterns",
        "suspicious": run_ratio < 0.7 or run_ratio > 1.3,
        "description": (
            f"LSB run test: {runs} runs (expected ~{int(expected_runs)}), "
            f"ratio: {run_ratio:.2f}"
            + (" - Pattern anomaly detected" if run_ratio < 0.7 or run_ratio > 1.3 else " - Normal")
        ),
    })

    # Chi-squared test on LSB byte values
    lsb_bytes_sample = sample[:4000]
    byte_counts = Counter(lsb_bytes_sample)
    n = len(lsb_bytes_sample)
    expected = n / 256
    if expected > 0:
        chi_sq = sum((count - expected) ** 2 / expected for count in byte_counts.values())
        # Very high chi-squared suggests non-random distribution
        results.append({
            "check": "lsb_chi_squared",
            "suspicious": chi_sq > 300,
            "description": (
                f"Chi-squared statistic: {chi_sq:.1f}"
                + (" - Distribution anomaly" if chi_sq > 300 else " - Normal distribution")
            ),
        })

    return results


def _analyze_metadata_stego(data: bytes, suffix: str) -> list[dict[str, Any]]:
    """Check metadata for steganography indicators."""
    results: list[dict[str, Any]] = []

    # Check for unusually large EXIF/metadata
    if suffix in (".jpg", ".jpeg"):
        # Find APP1 segment
        pos = 2
        exif_size = 0
        while pos < min(len(data), 65535) - 4:
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            if marker == 0xE1:
                exif_size = struct.unpack(">H", data[pos + 2:pos + 4])[0]
                break
            if marker in (0xD9, 0xDA):
                break
            seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
            pos += 2 + seg_len

        results.append({
            "check": "exif_size",
            "suspicious": exif_size > 10000,
            "description": (
                f"EXIF data size: {exif_size} bytes"
                + (" - Unusually large EXIF (possible hidden data)" if exif_size > 10000 else " - Normal")
            ),
        })

    # Check for known stego tool signatures
    tool_signatures = [
        (b"OpenStego", "OpenStego"),
        (b"Steghide", "Steghide"),
        (b"steganography", "Generic stego marker"),
        (b"OPENSTEGO", "OpenStego (uppercase)"),
        (b"LSB-Steg", "LSB-Steg tool"),
        (b"stegano", "Stegano library"),
    ]

    found_tools = []
    for sig, name in tool_signatures:
        if sig in data:
            found_tools.append(name)

    results.append({
        "check": "tool_signatures",
        "suspicious": len(found_tools) > 0,
        "description": (
            f"Tool signatures found: {', '.join(found_tools)}"
            if found_tools else "No known stego tool signatures detected"
        ),
    })

    # Check for unusual comment fields
    if suffix in (".jpg", ".jpeg"):
        comment_markers = data.count(b"\xff\xfe")  # COM marker
        results.append({
            "check": "jpeg_comments",
            "suspicious": comment_markers > 2,
            "description": (
                f"JPEG comment markers: {comment_markers}"
                + (" - Multiple comments (unusual)" if comment_markers > 2 else " - Normal")
            ),
        })

    return results


def _analyze_visual_stego(data: bytes, suffix: str,
                          file_size: int) -> list[dict[str, Any]]:
    """Check file structure for appended or hidden data."""
    results: list[dict[str, Any]] = []

    if suffix in (".jpg", ".jpeg"):
        # Check for data after JPEG EOI marker (0xFFD9)
        eoi_pos = data.rfind(b"\xff\xd9")
        if eoi_pos != -1:
            trailing = file_size - eoi_pos - 2
            results.append({
                "check": "trailing_data",
                "suspicious": trailing > 10,
                "description": (
                    f"Data after JPEG EOI: {trailing} bytes"
                    + (f" - Appended data detected!" if trailing > 10 else " - Clean")
                ),
            })
        else:
            results.append({
                "check": "trailing_data",
                "suspicious": True,
                "description": "No JPEG EOI marker found - corrupted or modified file",
            })

    elif suffix == ".png":
        # Check for data after PNG IEND chunk
        iend_pos = data.find(b"IEND")
        if iend_pos != -1:
            # IEND chunk: 4 (len) + 4 (IEND) + 0 (data) + 4 (CRC) = at iend_pos - 4
            end_of_iend = iend_pos + 4 + 4  # IEND + CRC
            trailing = file_size - end_of_iend
            results.append({
                "check": "trailing_data",
                "suspicious": trailing > 10,
                "description": (
                    f"Data after PNG IEND: {trailing} bytes"
                    + (f" - Appended data detected!" if trailing > 10 else " - Clean")
                ),
            })
        else:
            results.append({
                "check": "trailing_data",
                "suspicious": True,
                "description": "No PNG IEND chunk found - corrupted or modified file",
            })

    # Check file size vs expected (rough heuristic)
    if suffix == ".png":
        # Parse PNG IHDR for dimensions
        if len(data) > 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
            width = struct.unpack(">I", data[16:20])[0]
            height = struct.unpack(">I", data[20:24])[0]
            # Rough estimate: 3 bytes/pixel for RGB uncompressed, PNG typically 30-70% of that
            expected_max = width * height * 4  # RGBA uncompressed
            expected_min = width * height * 0.1  # Heavily compressed

            if expected_max > 0:
                size_ratio = file_size / expected_max
                results.append({
                    "check": "size_analysis",
                    "suspicious": file_size > expected_max * 1.5,
                    "description": (
                        f"Image: {width}x{height}, size ratio: {size_ratio:.2f}x of max expected"
                        + (" - File significantly larger than expected" if file_size > expected_max * 1.5 else " - Normal")
                    ),
                })

    return results


# ---------------------------------------------------------------------------
# 3. ioc_extract -- Extract Indicators of Compromise from text
# ---------------------------------------------------------------------------

# Common false-positive domains to filter
_FP_DOMAINS = frozenset({
    "google.com", "gmail.com", "facebook.com", "twitter.com", "github.com",
    "microsoft.com", "apple.com", "amazon.com", "linkedin.com", "youtube.com",
    "example.com", "example.org", "example.net", "localhost.localdomain",
    "schema.org", "w3.org", "xmlns.com", "purl.org",
})


async def ioc_extract(text: str, ioc_types: str = "all") -> ToolResult:
    """Extract Indicators of Compromise (IoC) from text input.

    Finds IPs, domains, URLs, hashes, emails, CVEs, Bitcoin addresses,
    and MAC addresses using regex patterns. Deduplicates and categorizes.
    """
    start = time.monotonic()

    if not text or not text.strip():
        return ToolResult(success=False, output="", error="text must not be empty")

    valid_types = ("all", "ip", "domain", "hash", "email", "url", "cve")
    ioc_types = ioc_types.lower()
    if ioc_types not in valid_types:
        return ToolResult(
            success=False, output="",
            error=f"ioc_types phai la mot trong: {', '.join(valid_types)}",
        )

    try:
        iocs: dict[str, list[str]] = await asyncio.to_thread(
            _extract_iocs, text, ioc_types,
        )

        elapsed = int((time.monotonic() - start) * 1000)

        total = sum(len(v) for v in iocs.values())

        lines = [
            f"IoC Extraction Results",
            f"Input length: {len(text)} chars",
            f"Total IoCs found: {total}",
            "",
        ]

        for category, items in sorted(iocs.items()):
            if not items:
                continue
            lines.append(f"[{category}] ({len(items)}):")
            for item in items:
                lines.append(f"  {item}")
            lines.append("")

        if total == 0:
            lines.append("Khong tim thay IoC nao trong text.")

        return ToolResult(
            success=True,
            output=_truncate("\n".join(lines)),
            execution_time_ms=elapsed,
            data={"total": total, "iocs": iocs},
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("ioc_extract_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"IoC extraction failed: {e}",
            execution_time_ms=elapsed,
        )


def _extract_iocs(text: str, ioc_types: str) -> dict[str, list[str]]:
    """Extract and categorize IoCs from text (runs in thread)."""
    results: dict[str, list[str]] = {}
    extract_all = ioc_types == "all"

    # IPv4
    if extract_all or ioc_types == "ip":
        ipv4_pattern = r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
        ipv4_matches = re.findall(ipv4_pattern, text)
        valid_ips = []
        for ip in ipv4_matches:
            parts = ip.split(".")
            if all(0 <= int(p) <= 255 for p in parts):
                # Filter out version numbers (e.g., 1.2.3.4 in "version 1.2.3.4")
                if not _looks_like_version(text, ip):
                    valid_ips.append(ip)
        results["IPv4"] = sorted(set(valid_ips))

        # IPv6
        ipv6_pattern = (
            r"\b([0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7})\b"
            r"|"
            r"\b((?:[0-9a-fA-F]{1,4}:){1,7}:)\b"
            r"|"
            r"\b(::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4})\b"
        )
        ipv6_raw = re.findall(ipv6_pattern, text)
        ipv6_list = []
        for match_groups in ipv6_raw:
            for group in match_groups:
                if group:
                    ipv6_list.append(group)
        results["IPv6"] = sorted(set(ipv6_list))

    # URLs (extract before domains to avoid double-counting)
    urls_found: set[str] = set()
    if extract_all or ioc_types == "url":
        url_pattern = r"https?://[^\s<>\"')\]]+"
        urls_found = set(re.findall(url_pattern, text))
        # Clean trailing punctuation
        cleaned_urls = []
        for url in urls_found:
            url = url.rstrip(".,;:!?")
            cleaned_urls.append(url)
        urls_found = set(cleaned_urls)
        results["URLs"] = sorted(urls_found)

    # Domains
    if extract_all or ioc_types == "domain":
        domain_pattern = r"\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.(?:[a-zA-Z]{2,}))\b"
        domain_matches = re.findall(domain_pattern, text)
        filtered_domains = []
        for domain in domain_matches:
            domain_lower = domain.lower()
            if domain_lower in _FP_DOMAINS:
                continue
            # Skip if domain is part of a URL we already captured
            if any(domain_lower in url for url in urls_found):
                continue
            # Skip version-like strings
            if re.match(r"^\d+\.\d+$", domain):
                continue
            filtered_domains.append(domain_lower)
        results["Domains"] = sorted(set(filtered_domains))

    # Hashes
    if extract_all or ioc_types == "hash":
        # SHA256 (64 hex chars) - check first to avoid substring matches
        sha256_pattern = r"\b([a-fA-F0-9]{64})\b"
        sha256_matches = set(re.findall(sha256_pattern, text))

        # SHA1 (40 hex chars)
        sha1_pattern = r"\b([a-fA-F0-9]{40})\b"
        sha1_raw = set(re.findall(sha1_pattern, text))
        # Remove SHA1 matches that are substrings of SHA256
        sha1_matches = {h for h in sha1_raw if not any(h in s for s in sha256_matches)}

        # MD5 (32 hex chars)
        md5_pattern = r"\b([a-fA-F0-9]{32})\b"
        md5_raw = set(re.findall(md5_pattern, text))
        # Remove matches that are substrings of longer hashes
        all_longer = sha256_matches | sha1_matches
        md5_matches = {h for h in md5_raw if not any(h in s for s in all_longer)}

        hashes: dict[str, list[str]] = {}
        if md5_matches:
            hashes["MD5"] = sorted(md5_matches)
        if sha1_matches:
            hashes["SHA1"] = sorted(sha1_matches)
        if sha256_matches:
            hashes["SHA256"] = sorted(sha256_matches)

        for hash_type, hash_list in hashes.items():
            results[hash_type] = hash_list

    # Email
    if extract_all or ioc_types == "email":
        email_pattern = r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b"
        emails = set(re.findall(email_pattern, text))
        results["Emails"] = sorted(emails)

    # CVE
    if extract_all or ioc_types == "cve":
        cve_pattern = r"\b(CVE-\d{4}-\d{4,})\b"
        cves = set(re.findall(cve_pattern, text))
        results["CVEs"] = sorted(cves)

    # Bitcoin addresses
    if extract_all:
        btc_pattern = r"\b([13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"
        btc = set(re.findall(btc_pattern, text))
        results["Bitcoin"] = sorted(btc)

    # MAC addresses
    if extract_all:
        mac_pattern = r"\b((?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2})\b"
        macs = set(re.findall(mac_pattern, text))
        results["MAC"] = sorted(macs)

    # Remove empty categories
    return {k: v for k, v in results.items() if v}


def _looks_like_version(text: str, ip: str) -> bool:
    """Check if an IP-like string is actually a version number."""
    # Look for "version X.X.X.X" or "vX.X.X.X" patterns
    escaped = re.escape(ip)
    version_patterns = [
        rf"v(?:ersion)?\s*{escaped}",
        rf"{escaped}\s*[-/]\s*(?:alpha|beta|rc|release)",
    ]
    for pattern in version_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# 4. log_analyze -- Analyze log files for suspicious patterns
# ---------------------------------------------------------------------------

_APACHE_PATTERN = re.compile(
    r'^(\S+)\s+\S+\s+\S+\s+\[([^\]]+)\]\s+"(\S+)\s+(\S+)\s+\S+"\s+(\d{3})\s+(\S+)'
)

_NGINX_PATTERN = _APACHE_PATTERN  # Combined log format is the same

_AUTH_FAIL_PATTERNS = [
    re.compile(r"Failed password for (?:invalid user )?(\S+) from (\S+)"),
    re.compile(r"authentication failure.*rhost=(\S+).*user=(\S+)"),
    re.compile(r"pam_unix.*authentication failure.*user=(\S+)"),
]

_AUTH_SUCCESS_PATTERN = re.compile(
    r"Accepted (?:password|publickey) for (\S+) from (\S+)",
)

_SQL_INJECTION_PATTERNS = [
    r"(?:UNION\s+(?:ALL\s+)?SELECT)",
    r"(?:OR\s+1\s*=\s*1)",
    r"(?:AND\s+1\s*=\s*1)",
    r"(?:DROP\s+TABLE)",
    r"(?:INSERT\s+INTO)",
    r"(?:DELETE\s+FROM)",
    r"(?:--\s*$)",
    r"(?:;\s*(?:DROP|DELETE|INSERT|UPDATE))",
    r"(?:SLEEP\s*\(\d+\))",
    r"(?:BENCHMARK\s*\()",
    r"(?:WAITFOR\s+DELAY)",
]

_XSS_PATTERNS = [
    r"<script[^>]*>",
    r"javascript:",
    r"on(?:error|load|click|mouseover)\s*=",
    r"<iframe[^>]*>",
    r"<object[^>]*>",
    r"eval\s*\(",
    r"document\.cookie",
    r"alert\s*\(",
]

_SCANNER_SIGNATURES = [
    r"nikto",
    r"nmap",
    r"sqlmap",
    r"dirbuster",
    r"gobuster",
    r"wpscan",
    r"burpsuite",
    r"masscan",
    r"nuclei",
    r"nessus",
    r"acunetix",
    r"openvas",
    r"w3af",
    r"zap",
]

_TRAVERSAL_PATTERNS = [
    r"\.\./",
    r"\.\.\\",
    r"%2e%2e%2f",
    r"%2e%2e/",
    r"\.\.%2f",
    r"/etc/passwd",
    r"/etc/shadow",
    r"\\windows\\system32",
]


async def log_analyze(file_path: str, log_type: str = "auto") -> ToolResult:
    """Analyze log files for suspicious patterns and security events.

    Supports Apache/Nginx access logs, auth logs, syslog, and JSON logs.
    Detects brute force, SQL injection, XSS, directory traversal, and scanner activity.
    """
    start = time.monotonic()

    resolved, err = _validate_file_path(file_path)
    if err:
        return ToolResult(success=False, output="", error=err)

    valid_types = ("auto", "apache", "nginx", "auth", "syslog", "json")
    log_type = log_type.lower()
    if log_type not in valid_types:
        return ToolResult(
            success=False, output="",
            error=f"log_type phai la mot trong: {', '.join(valid_types)}",
        )

    try:
        raw_text = await asyncio.to_thread(resolved.read_text, "utf-8", "replace")
        lines_list = raw_text.splitlines()

        if not lines_list:
            return ToolResult(
                success=True,
                output="File log rong.",
                data={"total_lines": 0},
            )

        # Auto-detect log type
        if log_type == "auto":
            log_type = _detect_log_type(lines_list[:20])

        elapsed_detect = int((time.monotonic() - start) * 1000)

        if log_type in ("apache", "nginx"):
            analysis = await asyncio.to_thread(
                _analyze_access_log, lines_list, log_type,
            )
        elif log_type == "auth":
            analysis = await asyncio.to_thread(_analyze_auth_log, lines_list)
        elif log_type == "json":
            analysis = await asyncio.to_thread(_analyze_json_log, lines_list)
        else:
            analysis = await asyncio.to_thread(_analyze_syslog, lines_list)

        elapsed = int((time.monotonic() - start) * 1000)

        output_lines = [
            f"Log Analysis: {resolved.name}",
            f"Detected format: {log_type}",
            f"Total lines: {len(lines_list)}",
            "",
        ]
        output_lines.extend(analysis["output_lines"])

        return ToolResult(
            success=True,
            output=_truncate("\n".join(output_lines)),
            execution_time_ms=elapsed,
            data=analysis.get("data", {}),
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("log_analyze_error", path=file_path, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Log analysis failed: {e}",
            execution_time_ms=elapsed,
        )


def _detect_log_type(sample_lines: list[str]) -> str:
    """Auto-detect log format from sample lines."""
    if not sample_lines:
        return "syslog"

    json_count = 0
    apache_count = 0
    auth_count = 0

    for line in sample_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("{"):
            json_count += 1

        if _APACHE_PATTERN.match(stripped):
            apache_count += 1

        if any(keyword in stripped for keyword in ("sshd", "pam_unix", "Failed password", "Accepted password")):
            auth_count += 1

    if json_count > len(sample_lines) * 0.5:
        return "json"
    if apache_count > len(sample_lines) * 0.3:
        return "apache"
    if auth_count > len(sample_lines) * 0.3:
        return "auth"

    return "syslog"


def _analyze_access_log(lines: list[str],
                        log_type: str) -> dict[str, Any]:
    """Analyze Apache/Nginx access log lines."""
    ip_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    path_counter: Counter[str] = Counter()
    suspicious_entries: list[str] = []
    attack_patterns: dict[str, int] = {
        "SQL Injection": 0,
        "XSS": 0,
        "Directory Traversal": 0,
        "Scanner Activity": 0,
        "Brute Force (401)": 0,
    }

    parsed_count = 0
    for line in lines:
        match = _APACHE_PATTERN.match(line)
        if not match:
            continue

        parsed_count += 1
        ip = match.group(1)
        method = match.group(3)
        path = match.group(4)
        status = match.group(5)

        ip_counter[ip] += 1
        status_counter[status] += 1
        path_counter[path] += 1

        path_lower = path.lower()

        # Check for attacks
        is_suspicious = False

        # SQL injection
        for pattern in _SQL_INJECTION_PATTERNS:
            if re.search(pattern, path_lower, re.IGNORECASE):
                attack_patterns["SQL Injection"] += 1
                is_suspicious = True
                break

        # XSS
        for pattern in _XSS_PATTERNS:
            if re.search(pattern, path_lower, re.IGNORECASE):
                attack_patterns["XSS"] += 1
                is_suspicious = True
                break

        # Directory traversal
        for pattern in _TRAVERSAL_PATTERNS:
            if re.search(pattern, path_lower, re.IGNORECASE):
                attack_patterns["Directory Traversal"] += 1
                is_suspicious = True
                break

        # Scanner signatures
        for pattern in _SCANNER_SIGNATURES:
            if re.search(pattern, line.lower()):
                attack_patterns["Scanner Activity"] += 1
                is_suspicious = True
                break

        # Brute force
        if status == "401":
            attack_patterns["Brute Force (401)"] += 1
            is_suspicious = True

        if is_suspicious and len(suspicious_entries) < 10:
            suspicious_entries.append(f"  [{status}] {ip} {method} {path}")

    # Build output
    output_lines = [
        f"Parsed entries: {parsed_count}/{len(lines)}",
        "",
        "=== Status Code Distribution ===",
    ]
    for status, count in status_counter.most_common(10):
        output_lines.append(f"  {status}: {count}")

    output_lines.append("")
    output_lines.append("=== Top IPs ===")
    for ip, count in ip_counter.most_common(10):
        output_lines.append(f"  {ip}: {count} requests")

    output_lines.append("")
    output_lines.append("=== Attack Patterns ===")
    active_attacks = {k: v for k, v in attack_patterns.items() if v > 0}
    if active_attacks:
        for attack, count in sorted(active_attacks.items(), key=lambda x: -x[1]):
            output_lines.append(f"  [!] {attack}: {count}")
    else:
        output_lines.append("  No attack patterns detected")

    if suspicious_entries:
        output_lines.append("")
        output_lines.append("=== Suspicious Entries (top 10) ===")
        output_lines.extend(suspicious_entries)

    return {
        "output_lines": output_lines,
        "data": {
            "parsed_count": parsed_count,
            "total_lines": len(lines),
            "unique_ips": len(ip_counter),
            "status_codes": dict(status_counter.most_common()),
            "top_ips": dict(ip_counter.most_common(10)),
            "attack_patterns": attack_patterns,
            "suspicious_count": len(suspicious_entries),
        },
    }


def _analyze_auth_log(lines: list[str]) -> dict[str, Any]:
    """Analyze authentication logs (auth.log / secure)."""
    failed_logins: Counter[str] = Counter()  # IP -> count
    failed_users: Counter[str] = Counter()
    successful_logins: list[str] = []
    suspicious_entries: list[str] = []

    for line in lines:
        # Check failed logins
        for pattern in _AUTH_FAIL_PATTERNS:
            match = pattern.search(line)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    failed_logins[groups[1]] += 1
                    failed_users[groups[0]] += 1
                elif len(groups) == 1:
                    failed_users[groups[0]] += 1
                break

        # Check successful logins
        match = _AUTH_SUCCESS_PATTERN.search(line)
        if match:
            user = match.group(1)
            ip = match.group(2)
            successful_logins.append(f"{user} from {ip}")

        # Check for sudo usage
        if "sudo:" in line and "COMMAND=" in line:
            if len(suspicious_entries) < 10:
                suspicious_entries.append(f"  [SUDO] {line.strip()[:150]}")

        # Check for new user creation
        if "useradd" in line or "adduser" in line:
            if len(suspicious_entries) < 10:
                suspicious_entries.append(f"  [NEW USER] {line.strip()[:150]}")

    # Detect brute force: IPs with many failures
    brute_force_ips = {ip: count for ip, count in failed_logins.items() if count >= 5}

    # Detect success after failure
    success_after_fail: list[str] = []
    for entry in successful_logins:
        parts = entry.split(" from ")
        if len(parts) == 2:
            ip = parts[1]
            if ip in failed_logins:
                success_after_fail.append(entry)

    output_lines = [
        "=== Authentication Summary ===",
        f"  Failed login attempts: {sum(failed_logins.values())}",
        f"  Unique attacking IPs: {len(failed_logins)}",
        f"  Successful logins: {len(successful_logins)}",
        "",
    ]

    if brute_force_ips:
        output_lines.append("=== Brute Force Detected ===")
        for ip, count in sorted(brute_force_ips.items(), key=lambda x: -x[1]):
            output_lines.append(f"  [!] {ip}: {count} failed attempts")
        output_lines.append("")

    if failed_users:
        output_lines.append("=== Targeted Users ===")
        for user, count in failed_users.most_common(10):
            output_lines.append(f"  {user}: {count} failures")
        output_lines.append("")

    if success_after_fail:
        output_lines.append("=== Successful Login After Failures (Possible Compromise) ===")
        for entry in success_after_fail[:5]:
            output_lines.append(f"  [!] {entry}")
        output_lines.append("")

    if suspicious_entries:
        output_lines.append("=== Suspicious Activity ===")
        output_lines.extend(suspicious_entries)

    return {
        "output_lines": output_lines,
        "data": {
            "failed_attempts": sum(failed_logins.values()),
            "unique_attacking_ips": len(failed_logins),
            "successful_logins": len(successful_logins),
            "brute_force_ips": brute_force_ips,
            "success_after_fail": success_after_fail,
            "top_failed_users": dict(failed_users.most_common(10)),
        },
    }


def _analyze_json_log(lines: list[str]) -> dict[str, Any]:
    """Analyze JSON-formatted log lines."""
    import json

    level_counter: Counter[str] = Counter()
    error_messages: list[str] = []
    parsed_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
            parsed_count += 1
        except (json.JSONDecodeError, ValueError):
            continue

        # Count log levels
        level = str(
            entry.get("level", entry.get("severity", entry.get("log_level", "unknown")))
        ).upper()
        level_counter[level] += 1

        # Capture error messages
        if level in ("ERROR", "CRITICAL", "FATAL"):
            msg = entry.get("message", entry.get("msg", entry.get("error", str(entry))))
            if len(error_messages) < 10:
                error_messages.append(f"  [{level}] {str(msg)[:150]}")

    output_lines = [
        f"Parsed JSON entries: {parsed_count}/{len(lines)}",
        "",
        "=== Log Level Distribution ===",
    ]
    for level, count in level_counter.most_common():
        output_lines.append(f"  {level}: {count}")

    total = sum(level_counter.values())
    error_count = level_counter.get("ERROR", 0) + level_counter.get("CRITICAL", 0) + level_counter.get("FATAL", 0)
    error_rate = (error_count / total * 100) if total > 0 else 0

    output_lines.append("")
    output_lines.append(f"Error rate: {error_rate:.1f}%")

    if error_rate > 10:
        output_lines.append("[!] High error rate detected")

    if error_messages:
        output_lines.append("")
        output_lines.append("=== Recent Errors ===")
        output_lines.extend(error_messages)

    return {
        "output_lines": output_lines,
        "data": {
            "parsed_count": parsed_count,
            "total_lines": len(lines),
            "level_distribution": dict(level_counter),
            "error_rate": error_rate,
            "error_count": error_count,
        },
    }


def _analyze_syslog(lines: list[str]) -> dict[str, Any]:
    """Analyze generic syslog-format logs."""
    error_count = 0
    warning_count = 0
    service_restarts: list[str] = []
    suspicious_entries: list[str] = []
    service_counter: Counter[str] = Counter()

    for line in lines:
        line_lower = line.lower()

        # Count severity
        if any(word in line_lower for word in ("error", "fail", "critical", "fatal", "segfault", "panic")):
            error_count += 1
        elif any(word in line_lower for word in ("warn", "warning")):
            warning_count += 1

        # Detect service restarts
        if any(word in line_lower for word in ("started", "restarted", "stopped", "restart")):
            if len(service_restarts) < 10:
                service_restarts.append(f"  {line.strip()[:150]}")

        # Extract service name (syslog format: ... hostname service[pid]: ...)
        syslog_match = re.match(r"^\S+\s+\d+\s+\S+\s+\S+\s+(\S+?)(?:\[\d+\])?:", line)
        if syslog_match:
            service_counter[syslog_match.group(1)] += 1

        # Detect unusual patterns
        unusual_keywords = [
            "segfault", "out of memory", "oom", "killed process",
            "disk full", "no space", "permission denied",
            "connection refused", "timeout",
        ]
        for keyword in unusual_keywords:
            if keyword in line_lower:
                if len(suspicious_entries) < 10:
                    suspicious_entries.append(f"  [{keyword.upper()}] {line.strip()[:150]}")
                break

    output_lines = [
        "=== Syslog Summary ===",
        f"  Total lines: {len(lines)}",
        f"  Errors/Failures: {error_count}",
        f"  Warnings: {warning_count}",
        "",
    ]

    if service_counter:
        output_lines.append("=== Top Services ===")
        for service, count in service_counter.most_common(10):
            output_lines.append(f"  {service}: {count} entries")
        output_lines.append("")

    if service_restarts:
        output_lines.append("=== Service Restarts ===")
        output_lines.extend(service_restarts)
        output_lines.append("")

    if suspicious_entries:
        output_lines.append("=== Suspicious Entries ===")
        output_lines.extend(suspicious_entries)

    return {
        "output_lines": output_lines,
        "data": {
            "total_lines": len(lines),
            "error_count": error_count,
            "warning_count": warning_count,
            "services": dict(service_counter.most_common(10)),
            "suspicious_count": len(suspicious_entries),
        },
    }


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

file_metadata_tool = ToolDefinition(
    name="file_metadata",
    description=(
        "Trích xuất metadata từ file. Hỗ trợ EXIF (ảnh JPEG), PNG info, "
        "PDF metadata, và thông tin file thực thi. Dùng cho phân tích forensics và CTF."
    ),
    parameters=[
        ToolParameter(
            name="file_path", type="string",
            description="Đường dẫn tới file cần phân tích",
        ),
    ],
    handler=file_metadata,
    timeout_seconds=30,
)

stego_detect_tool = ToolDefinition(
    name="stego_detect",
    description=(
        "Phát hiện steganography trong file ảnh. Phân tích LSB, metadata bất thường, "
        "và cấu trúc file để tìm dữ liệu ẩn. Dùng cho forensics và CTF."
    ),
    parameters=[
        ToolParameter(
            name="file_path", type="string",
            description="Đường dẫn tới file ảnh (PNG, JPG, BMP, GIF)",
        ),
        ToolParameter(
            name="method", type="string",
            description="Phương pháp phân tích: lsb, metadata, visual, hoặc all",
            required=False,
            enum=["lsb", "metadata", "visual", "all"],
            default="all",
        ),
    ],
    handler=stego_detect,
    timeout_seconds=30,
)

ioc_extract_tool = ToolDefinition(
    name="ioc_extract",
    description=(
        "Trích xuất Indicators of Compromise (IoC) từ text. Tìm IP, domain, URL, "
        "hash (MD5/SHA1/SHA256), email, CVE, Bitcoin address, MAC address."
    ),
    parameters=[
        ToolParameter(
            name="text", type="string",
            description="Đoạn text cần phân tích để trích xuất IoC",
        ),
        ToolParameter(
            name="ioc_types", type="string",
            description="Loại IoC cần tìm: all, ip, domain, hash, email, url, cve",
            required=False,
            enum=["all", "ip", "domain", "hash", "email", "url", "cve"],
            default="all",
        ),
    ],
    handler=ioc_extract,
    timeout_seconds=15,
)

log_analyze_tool = ToolDefinition(
    name="log_analyze",
    description=(
        "Phân tích file log để phát hiện hoạt động đáng ngờ. Hỗ trợ Apache/Nginx, "
        "auth log, syslog, JSON log. Phát hiện brute force, SQL injection, XSS, "
        "directory traversal, và scanner."
    ),
    parameters=[
        ToolParameter(
            name="file_path", type="string",
            description="Đường dẫn tới file log cần phân tích",
        ),
        ToolParameter(
            name="log_type", type="string",
            description="Loại log: auto, apache, nginx, auth, syslog, json",
            required=False,
            enum=["auto", "apache", "nginx", "auth", "syslog", "json"],
            default="auto",
        ),
    ],
    handler=log_analyze,
    timeout_seconds=30,
)
