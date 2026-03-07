"""TTS Tool — Text-to-Speech using edge-tts (Microsoft, free).

Converts text to speech audio files. Supports Vietnamese and English.
Uses Microsoft Edge's TTS service (no API key needed).
"""

from __future__ import annotations

import time
from pathlib import Path

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.tts")

_OUTPUT_DIR = Path("/tmp/jarvis_tts")
_MAX_TEXT_LENGTH = 5000

# Vietnamese and English voices
_VOICES = {
    "vi-female": "vi-VN-HoaiMyNeural",
    "vi-male": "vi-VN-NamMinhNeural",
    "en-female": "en-US-JennyNeural",
    "en-male": "en-US-GuyNeural",
}
_DEFAULT_VOICE = "vi-female"


def _detect_language(text: str) -> str:
    """Simple language detection: Vietnamese or English."""
    import re
    vn_chars = re.findall(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", text.lower())
    return "vi" if len(vn_chars) > 2 else "en"


async def text_to_speech(
    text: str,
    voice: str = "",
    rate: str = "+0%",
) -> ToolResult:
    """Convert text to speech audio file.

    Returns path to generated .mp3 file.
    """
    start = time.monotonic()

    if not text or not text.strip():
        return ToolResult(success=False, output="", error="Text không được để trống")

    if len(text) > _MAX_TEXT_LENGTH:
        text = text[:_MAX_TEXT_LENGTH]

    # Auto-detect voice
    if not voice:
        lang = _detect_language(text)
        voice = _VOICES.get(f"{lang}-female", _VOICES[_DEFAULT_VOICE])
    elif voice in _VOICES:
        voice = _VOICES[voice]
    # else: use as-is (full voice name)

    try:
        import edge_tts

        _OUTPUT_DIR.mkdir(exist_ok=True)
        ts = int(time.time() * 1000)
        output_path = _OUTPUT_DIR / f"tts_{ts}.mp3"

        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(output_path))

        elapsed = int((time.monotonic() - start) * 1000)
        size = output_path.stat().st_size

        return ToolResult(
            success=True,
            output=f"Audio saved: {output_path} ({size:,} bytes)",
            execution_time_ms=elapsed,
            data={
                "path": str(output_path),
                "voice": voice,
                "size": size,
                "text_length": len(text),
            },
        )

    except ImportError:
        return ToolResult(
            success=False, output="",
            error="edge-tts chưa cài. Chạy: pip install edge-tts",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("tts_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"TTS failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

tts_tool = ToolDefinition(
    name="text_to_speech",
    description="Chuyển văn bản thành giọng nói (TTS). Tự nhận diện tiếng Việt/Anh. Trả về file audio .mp3.",
    parameters=[
        ToolParameter(name="text", type="string", description="Văn bản cần đọc"),
        ToolParameter(
            name="voice", type="string",
            description="Giọng đọc: vi-female, vi-male, en-female, en-male (mặc định: auto)",
            required=False, default="",
        ),
        ToolParameter(
            name="rate", type="string",
            description="Tốc độ đọc: -20%, +0%, +20%, +50% (mặc định: +0%)",
            required=False, default="+0%",
        ),
    ],
    handler=text_to_speech,
    timeout_seconds=30,
)
