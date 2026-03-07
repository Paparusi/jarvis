"""RAG — Document Ingestion & Retrieval-Augmented Generation.

Supports: PDF, DOCX, TXT, MD, HTML
Pipeline: Parse → Chunk → Embed → Store → Retrieve

Chunks stored in semantic_memories table with source metadata.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.memory.semantic import SemanticMemory
from src.utils.logging import get_logger

log = get_logger("memory.rag")

# Supported file extensions
_SUPPORTED = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm", ".csv", ".json"}

# Chunking defaults
_CHUNK_SIZE = 800       # chars per chunk
_CHUNK_OVERLAP = 100    # overlap between chunks


@dataclass
class DocumentChunk:
    """A chunk of a parsed document."""
    content: str
    source: str          # file path or URL
    chunk_index: int
    total_chunks: int
    page: int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class IngestResult:
    """Result of ingesting a document."""
    source: str
    chunks_created: int
    total_chars: int
    file_type: str
    success: bool
    error: str = ""


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_pdf(path: Path) -> list[tuple[str, int]]:
    """Parse PDF → list of (text, page_number)."""
    import fitz  # pymupdf

    pages = []
    with fitz.open(str(path)) as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if text:
                pages.append((text, i + 1))
    return pages


def _parse_docx(path: Path) -> list[tuple[str, int]]:
    """Parse DOCX → list of (text, paragraph_group)."""
    from docx import Document

    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if not full_text:
        return []
    return [(full_text, 1)]


def _parse_text(path: Path) -> list[tuple[str, int]]:
    """Parse plain text / markdown / HTML."""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []

    # Strip HTML tags if HTML file
    if path.suffix.lower() in (".html", ".htm"):
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

    return [(text, 1)]


def parse_file(path: Path) -> list[tuple[str, int]]:
    """Parse a file into (text, page/section) pairs."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(path)
    elif ext in (".docx", ".doc"):
        return _parse_docx(path)
    elif ext in (".txt", ".md", ".html", ".htm", ".csv", ".json"):
        return _parse_text(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    chunk_size: int = _CHUNK_SIZE,
    overlap: int = _CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks, respecting sentence boundaries."""
    if len(text) <= chunk_size:
        return [text]

    # Split by sentences/paragraphs
    sentences = re.split(r"(?<=[.!?。\n])\s+", text)

    chunks = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > chunk_size and current:
            chunks.append(current.strip())
            # Overlap: keep last N chars
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:] + " " + sentence
            else:
                current = sentence
        else:
            current = current + " " + sentence if current else sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


# ---------------------------------------------------------------------------
# DocumentIngestor
# ---------------------------------------------------------------------------

class DocumentIngestor:
    """Ingest documents into semantic memory for RAG retrieval."""

    def __init__(
        self,
        memory: SemanticMemory,
        chunk_size: int = _CHUNK_SIZE,
        chunk_overlap: int = _CHUNK_OVERLAP,
    ) -> None:
        self._memory = memory
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    async def ingest_file(self, file_path: str | Path) -> IngestResult:
        """Ingest a single file into memory."""
        path = Path(file_path)

        if not path.exists():
            return IngestResult(
                source=str(path), chunks_created=0, total_chars=0,
                file_type="", success=False, error=f"File not found: {path}",
            )

        ext = path.suffix.lower()
        if ext not in _SUPPORTED:
            return IngestResult(
                source=str(path), chunks_created=0, total_chars=0,
                file_type=ext, success=False,
                error=f"Unsupported: {ext}. Supported: {', '.join(sorted(_SUPPORTED))}",
            )

        try:
            # Parse
            pages = parse_file(path)
            if not pages:
                return IngestResult(
                    source=str(path), chunks_created=0, total_chars=0,
                    file_type=ext, success=False, error="No text extracted",
                )

            # Chunk each page/section
            all_chunks: list[DocumentChunk] = []
            for text, page_num in pages:
                text_chunks = chunk_text(text, self._chunk_size, self._chunk_overlap)
                for i, chunk_text_content in enumerate(text_chunks):
                    all_chunks.append(DocumentChunk(
                        content=chunk_text_content,
                        source=str(path),
                        chunk_index=len(all_chunks),
                        total_chunks=0,  # Updated below
                        page=page_num,
                        metadata={"filename": path.name, "type": ext},
                    ))

            # Update total_chunks
            for c in all_chunks:
                c.total_chunks = len(all_chunks)

            # Store in memory
            source_hash = hashlib.md5(str(path).encode()).hexdigest()[:8]
            stored = 0
            total_chars = 0

            for chunk in all_chunks:
                # Format content with source info
                content = f"[DOC:{path.name}|p{chunk.page}|{chunk.chunk_index+1}/{chunk.total_chunks}] {chunk.content}"
                total_chars += len(chunk.content)

                await self._memory.remember(
                    content=content,
                    category="document",
                    importance=0.7,
                    metadata={
                        "source": str(path),
                        "filename": path.name,
                        "file_type": ext,
                        "page": chunk.page,
                        "chunk_index": chunk.chunk_index,
                        "total_chunks": chunk.total_chunks,
                        "source_hash": source_hash,
                    },
                )
                stored += 1

            log.info("document_ingested",
                    file=path.name, chunks=stored, chars=total_chars)

            return IngestResult(
                source=str(path),
                chunks_created=stored,
                total_chars=total_chars,
                file_type=ext,
                success=True,
            )

        except ImportError as e:
            missing = "pymupdf" if "fitz" in str(e) else "python-docx" if "docx" in str(e) else str(e)
            return IngestResult(
                source=str(path), chunks_created=0, total_chars=0,
                file_type=ext, success=False,
                error=f"Missing dependency: {missing}. Install with pip.",
            )
        except Exception as e:
            log.error("ingest_error", file=str(path), error=str(e))
            return IngestResult(
                source=str(path), chunks_created=0, total_chars=0,
                file_type=ext, success=False, error=str(e),
            )

    async def ingest_directory(
        self, dir_path: str | Path, recursive: bool = True,
    ) -> list[IngestResult]:
        """Ingest all supported files from a directory."""
        path = Path(dir_path)
        if not path.is_dir():
            return [IngestResult(
                source=str(path), chunks_created=0, total_chars=0,
                file_type="", success=False, error="Not a directory",
            )]

        results = []
        pattern = "**/*" if recursive else "*"
        for f in sorted(path.glob(pattern)):
            if f.is_file() and f.suffix.lower() in _SUPPORTED:
                result = await self.ingest_file(f)
                results.append(result)

        return results

    async def ingest_text(
        self, text: str, source: str = "pasted_text",
    ) -> IngestResult:
        """Ingest raw text (e.g., pasted content)."""
        if not text.strip():
            return IngestResult(
                source=source, chunks_created=0, total_chars=0,
                file_type="text", success=False, error="Empty text",
            )

        chunks = chunk_text(text, self._chunk_size, self._chunk_overlap)
        stored = 0

        for i, chunk_content in enumerate(chunks):
            content = f"[DOC:{source}|{i+1}/{len(chunks)}] {chunk_content}"
            await self._memory.remember(
                content=content,
                category="document",
                importance=0.7,
                metadata={
                    "source": source,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                },
            )
            stored += 1

        return IngestResult(
            source=source,
            chunks_created=stored,
            total_chars=len(text),
            file_type="text",
            success=True,
        )

    async def search_documents(
        self, query: str, top_k: int = 5,
    ) -> list[dict]:
        """Search ingested documents using semantic memory."""
        results = await self._memory.search(query, top_k=top_k * 2)

        # Filter to document category
        doc_results = [
            r for r in results
            if r.get("category") == "document"
        ][:top_k]

        return doc_results
