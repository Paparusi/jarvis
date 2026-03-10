"""Vision Tool — Image understanding via Claude Vision API.

Supports: analyze images, OCR text extraction, describe screenshots.
Uses Claude's native multimodal capability (base64 image input).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.vision")

_MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
_SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _get_mime_type(path: Path) -> str:
    """Get MIME type from file extension."""
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")


def _load_image_b64(path: Path) -> tuple[str, str]:
    """Load image as base64, return (b64_data, mime_type)."""
    data = path.read_bytes()
    if len(data) > _MAX_IMAGE_SIZE:
        raise ValueError(f"Image too large: {len(data):,} bytes (max {_MAX_IMAGE_SIZE:,})")
    return base64.b64encode(data).decode(), _get_mime_type(path)


async def analyze_image(
    image_path: str,
    question: str = "Mô tả chi tiết nội dung hình ảnh này.",
) -> ToolResult:
    """Analyze an image using Claude Vision API.

    Sends the image to Claude with a question/prompt and returns the analysis.
    """
    start = time.monotonic()

    path = Path(image_path)
    if not path.exists():
        return ToolResult(success=False, output="", error=f"File not found: {image_path}")

    if path.suffix.lower() not in _SUPPORTED_FORMATS:
        return ToolResult(
            success=False, output="",
            error=f"Unsupported format: {path.suffix}. Supported: {', '.join(_SUPPORTED_FORMATS)}",
        )

    try:
        b64_data, mime_type = _load_image_b64(path)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    try:
        from src.intelligence.claude_client import get_claude_client

        client = get_claude_client()
        response = await client.complete(
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}",
                        },
                    },
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }],
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
        )

        content = response.choices[0].message.content
        elapsed = int((time.monotonic() - start) * 1000)

        return ToolResult(
            success=True,
            output=content,
            execution_time_ms=elapsed,
            data={
                "image_path": str(path),
                "mime_type": mime_type,
                "question": question,
            },
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("vision_error", path=str(path), error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Vision analysis failed: {e}",
            execution_time_ms=elapsed,
        )


async def ocr_image(image_path: str) -> ToolResult:
    """Extract text from an image using Claude Vision (OCR)."""
    return await analyze_image(
        image_path=image_path,
        question="Trích xuất TẤT CẢ văn bản/chữ trong hình ảnh này. "
                 "Giữ nguyên format, layout, và ngôn ngữ gốc. "
                 "Nếu không có chữ, trả lời 'Không tìm thấy văn bản trong hình.'",
    )


async def analyze_image_from_bytes(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    question: str = "Mô tả chi tiết nội dung hình ảnh này.",
) -> ToolResult:
    """Analyze image from raw bytes (for Telegram photo handling)."""
    start = time.monotonic()

    if len(image_bytes) > _MAX_IMAGE_SIZE:
        return ToolResult(
            success=False, output="",
            error=f"Image too large: {len(image_bytes):,} bytes",
        )

    b64_data = base64.b64encode(image_bytes).decode()

    try:
        from src.intelligence.claude_client import get_claude_client

        client = get_claude_client()
        response = await client.complete(
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}",
                        },
                    },
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }],
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
        )

        content = response.choices[0].message.content
        elapsed = int((time.monotonic() - start) * 1000)

        return ToolResult(
            success=True,
            output=content,
            execution_time_ms=elapsed,
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("vision_bytes_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Vision analysis failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

analyze_image_tool = ToolDefinition(
    name="analyze_image",
    description="Phân tích hình ảnh bằng AI vision. Mô tả nội dung, nhận diện đối tượng, đọc text trong ảnh. Dùng khi user gửi ảnh hoặc yêu cầu phân tích hình.",
    parameters=[
        ToolParameter(name="image_path", type="string", description="Đường dẫn file ảnh"),
        ToolParameter(
            name="question", type="string",
            description="Câu hỏi về hình ảnh (mặc định: mô tả chi tiết)",
            required=False, default="Mô tả chi tiết nội dung hình ảnh này.",
        ),
    ],
    handler=analyze_image,
    timeout_seconds=30,
)

ocr_tool = ToolDefinition(
    name="ocr_image",
    description="Trích xuất văn bản từ hình ảnh (OCR). Đọc chữ, số, bảng biểu trong ảnh chụp màn hình, tài liệu scan, biển hiệu.",
    parameters=[
        ToolParameter(name="image_path", type="string", description="Đường dẫn file ảnh cần OCR"),
    ],
    handler=ocr_image,
    timeout_seconds=30,
)
