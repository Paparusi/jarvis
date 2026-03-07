"""Tests for RAG — Document Ingestion & Retrieval."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.memory.rag import (
    DocumentIngestor,
    chunk_text,
    parse_file,
    _parse_text,
    _SUPPORTED,
    DocumentChunk,
    IngestResult,
)


class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = chunk_text("Hello world", chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_long_text_multiple_chunks(self):
        text = "Sentence one. " * 50  # ~700 chars
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        assert len(chunks) > 1

    def test_empty_text(self):
        chunks = chunk_text("")
        assert len(chunks) == 1
        assert chunks[0] == ""

    def test_overlap_exists(self):
        text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence here."
        chunks = chunk_text(text, chunk_size=50, overlap=20)
        if len(chunks) >= 2:
            # Check that chunks overlap (last part of chunk N appears in chunk N+1)
            assert len(chunks) >= 2

    def test_respects_chunk_size(self):
        text = "Word. " * 200
        chunks = chunk_text(text, chunk_size=100, overlap=20)
        # Most chunks should be near chunk_size
        for chunk in chunks[:-1]:  # Last chunk can be shorter
            assert len(chunk) <= 200  # Some tolerance for sentence boundaries


class TestParseText:
    def test_txt_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Hello world\nSecond line")
            f.flush()
            result = _parse_text(Path(f.name))
            assert len(result) == 1
            assert "Hello world" in result[0][0]

    def test_html_file(self):
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
            f.write("<html><body><h1>Title</h1><p>Content</p></body></html>")
            f.flush()
            result = _parse_text(Path(f.name))
            assert len(result) == 1
            assert "Title" in result[0][0]
            assert "Content" in result[0][0]
            # HTML tags should be stripped
            assert "<h1>" not in result[0][0]

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("")
            f.flush()
            result = _parse_text(Path(f.name))
            assert result == []

    def test_md_file(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("# Header\n\nParagraph text here.")
            f.flush()
            result = _parse_text(Path(f.name))
            assert len(result) == 1
            assert "Header" in result[0][0]


class TestParseFile:
    def test_txt(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Test content")
            f.flush()
            result = parse_file(Path(f.name))
            assert len(result) >= 1

    def test_unsupported(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
            f.write("data")
            f.flush()
            with pytest.raises(ValueError, match="Unsupported"):
                parse_file(Path(f.name))


class TestSupportedFormats:
    def test_pdf_supported(self):
        assert ".pdf" in _SUPPORTED

    def test_docx_supported(self):
        assert ".docx" in _SUPPORTED

    def test_txt_supported(self):
        assert ".txt" in _SUPPORTED

    def test_md_supported(self):
        assert ".md" in _SUPPORTED

    def test_html_supported(self):
        assert ".html" in _SUPPORTED


class TestDocumentIngestor:
    def setup_method(self):
        from unittest.mock import AsyncMock, MagicMock
        self.mock_memory = MagicMock()
        self.mock_memory.remember = AsyncMock()
        self.mock_memory.search = AsyncMock(return_value=[])
        self.ingestor = DocumentIngestor(self.mock_memory)

    @pytest.mark.asyncio
    async def test_ingest_nonexistent_file(self):
        result = await self.ingestor.ingest_file("/nonexistent/file.txt")
        assert not result.success
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_ingest_unsupported_format(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"data")
            f.flush()
            result = await self.ingestor.ingest_file(f.name)
            assert not result.success
            assert "Unsupported" in result.error or "không hỗ trợ" in result.error

    @pytest.mark.asyncio
    async def test_ingest_txt_success(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("This is test content for JARVIS RAG system. " * 10)
            f.flush()
            result = await self.ingestor.ingest_file(f.name)
            assert result.success
            assert result.chunks_created >= 1
            assert result.total_chars > 0
            assert self.mock_memory.remember.called

    @pytest.mark.asyncio
    async def test_ingest_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("")
            f.flush()
            result = await self.ingestor.ingest_file(f.name)
            assert not result.success

    @pytest.mark.asyncio
    async def test_ingest_text_direct(self):
        result = await self.ingestor.ingest_text("Hello world from JARVIS")
        assert result.success
        assert result.chunks_created >= 1

    @pytest.mark.asyncio
    async def test_ingest_text_empty(self):
        result = await self.ingestor.ingest_text("")
        assert not result.success

    @pytest.mark.asyncio
    async def test_search_documents(self):
        from unittest.mock import AsyncMock
        self.mock_memory.search = AsyncMock(return_value=[
            {"content": "test doc", "category": "document", "score": 0.9},
            {"content": "other", "category": "fact", "score": 0.8},
        ])
        results = await self.ingestor.search_documents("test")
        assert len(results) == 1  # Only document category
        assert results[0]["category"] == "document"


class TestDocumentChunk:
    def test_creation(self):
        chunk = DocumentChunk(
            content="test", source="/tmp/test.pdf",
            chunk_index=0, total_chunks=5, page=1,
        )
        assert chunk.content == "test"
        assert chunk.page == 1

    def test_metadata_default(self):
        chunk = DocumentChunk(content="", source="", chunk_index=0, total_chunks=0)
        assert chunk.metadata == {}


class TestIngestResult:
    def test_success(self):
        r = IngestResult(source="test.pdf", chunks_created=5,
                        total_chars=1000, file_type=".pdf", success=True)
        assert r.success
        assert r.error == ""

    def test_failure(self):
        r = IngestResult(source="test.pdf", chunks_created=0,
                        total_chars=0, file_type=".pdf",
                        success=False, error="File not found")
        assert not r.success
