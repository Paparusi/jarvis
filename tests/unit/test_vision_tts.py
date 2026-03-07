"""Tests for Vision and TTS tools."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.tools.vision import (
    analyze_image_tool,
    ocr_tool,
    _get_mime_type,
    _SUPPORTED_FORMATS,
    _MAX_IMAGE_SIZE,
    analyze_image,
)
from src.tools.tts import (
    tts_tool,
    text_to_speech,
    _detect_language,
    _VOICES,
)


# === Vision Tests ===

class TestVisionMimeType:
    def test_jpg(self):
        assert _get_mime_type(Path("test.jpg")) == "image/jpeg"

    def test_jpeg(self):
        assert _get_mime_type(Path("test.jpeg")) == "image/jpeg"

    def test_png(self):
        assert _get_mime_type(Path("test.png")) == "image/png"

    def test_gif(self):
        assert _get_mime_type(Path("test.gif")) == "image/gif"

    def test_webp(self):
        assert _get_mime_type(Path("test.webp")) == "image/webp"

    def test_unknown(self):
        assert _get_mime_type(Path("test.xyz")) == "image/jpeg"  # fallback


class TestVisionFormats:
    def test_jpg_supported(self):
        assert ".jpg" in _SUPPORTED_FORMATS

    def test_png_supported(self):
        assert ".png" in _SUPPORTED_FORMATS

    def test_gif_supported(self):
        assert ".gif" in _SUPPORTED_FORMATS

    def test_bmp_supported(self):
        assert ".bmp" in _SUPPORTED_FORMATS


class TestAnalyzeImage:
    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        result = await analyze_image("/nonexistent/image.jpg")
        assert not result.success
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unsupported_format(self):
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            f.write(b"<svg></svg>")
            f.flush()
            result = await analyze_image(f.name)
            assert not result.success
            assert "Unsupported" in result.error or "không hỗ trợ" in result.error


class TestVisionToolDefinitions:
    def test_analyze_image_tool(self):
        assert analyze_image_tool.name == "analyze_image"
        params = [p.name for p in analyze_image_tool.parameters]
        assert "image_path" in params
        assert "question" in params

    def test_ocr_tool(self):
        assert ocr_tool.name == "ocr_image"
        params = [p.name for p in ocr_tool.parameters]
        assert "image_path" in params


# === TTS Tests ===

class TestLanguageDetection:
    def test_vietnamese(self):
        assert _detect_language("Xin chào, tôi là JARVIS") == "vi"

    def test_english(self):
        assert _detect_language("Hello, I am JARVIS") == "en"

    def test_mixed_defaults_vi(self):
        assert _detect_language("Tôi muốn test cái này") == "vi"

    def test_short_english(self):
        assert _detect_language("Hi") == "en"


class TestVoices:
    def test_vi_female_exists(self):
        assert "vi-female" in _VOICES

    def test_vi_male_exists(self):
        assert "vi-male" in _VOICES

    def test_en_female_exists(self):
        assert "en-female" in _VOICES

    def test_en_male_exists(self):
        assert "en-male" in _VOICES


class TestTextToSpeech:
    @pytest.mark.asyncio
    async def test_empty_text(self):
        result = await text_to_speech("")
        assert not result.success
        assert "trống" in result.error

    @pytest.mark.asyncio
    async def test_basic_tts(self):
        result = await text_to_speech("Xin chào")
        assert result.success
        assert "path" in result.data
        # Cleanup
        p = Path(result.data["path"])
        if p.exists():
            p.unlink()

    @pytest.mark.asyncio
    async def test_english_tts(self):
        result = await text_to_speech("Hello world", voice="en-female")
        assert result.success
        p = Path(result.data["path"])
        if p.exists():
            p.unlink()

    @pytest.mark.asyncio
    async def test_auto_voice_detection(self):
        result = await text_to_speech("Xin chào, tôi là JARVIS của bạn")
        assert result.success
        assert "vi-VN" in result.data["voice"]
        p = Path(result.data["path"])
        if p.exists():
            p.unlink()


class TestTTSToolDefinition:
    def test_name(self):
        assert tts_tool.name == "text_to_speech"

    def test_params(self):
        params = [p.name for p in tts_tool.parameters]
        assert "text" in params
        assert "voice" in params
        assert "rate" in params

    def test_timeout(self):
        assert tts_tool.timeout_seconds >= 20

    def test_description_vietnamese(self):
        vn_chars = "ăâấầàáảãạắằẳẵặéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ"
        assert any(c in tts_tool.description for c in vn_chars)
