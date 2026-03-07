"""Tests for streaming response infrastructure."""

import pytest

from src.intelligence.router import ResponseStream, StreamChunk


class TestStreamChunk:
    def test_text_chunk(self):
        chunk = StreamChunk(text="hello")
        assert chunk.text == "hello"
        assert not chunk.is_done

    def test_done_chunk(self):
        chunk = StreamChunk(is_done=True)
        assert chunk.is_done
        assert chunk.text == ""

    def test_tool_start(self):
        chunk = StreamChunk(tool_name="web_search", is_tool_start=True)
        assert chunk.is_tool_start
        assert chunk.tool_name == "web_search"


class TestResponseStream:
    def test_init(self):
        stream = ResponseStream()
        assert stream.response is None

    def test_no_generator_raises(self):
        stream = ResponseStream()
        with pytest.raises(RuntimeError, match="not initialized"):
            stream.__aiter__()

    @pytest.mark.asyncio
    async def test_generator_set(self):
        stream = ResponseStream()

        async def mock_gen():
            yield StreamChunk(text="hello ")
            yield StreamChunk(text="world")
            yield StreamChunk(is_done=True)

        stream._set_generator(mock_gen())

        collected = []
        async for chunk in stream:
            collected.append(chunk)

        assert len(collected) == 3
        assert collected[0].text == "hello "
        assert collected[1].text == "world"
        assert collected[2].is_done
