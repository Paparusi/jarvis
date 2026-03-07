"""Document Tool — Ingest and query documents for RAG.

Allows JARVIS to read PDFs, DOCX, and text files, store them in memory,
and answer questions based on document content.
"""

from __future__ import annotations

import time
from pathlib import Path

from src.memory.rag import DocumentIngestor, _SUPPORTED
from src.memory.semantic import SemanticMemory
from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.document")

# Shared ingestor instance (lazy init)
_ingestor: DocumentIngestor | None = None


def _get_ingestor() -> DocumentIngestor:
    global _ingestor
    if _ingestor is None:
        _ingestor = DocumentIngestor(SemanticMemory())
    return _ingestor


async def ingest_document(file_path: str) -> ToolResult:
    """Ingest a document into JARVIS memory for RAG retrieval."""
    start = time.monotonic()

    path = Path(file_path)
    if not path.exists():
        return ToolResult(success=False, output="", error=f"File không tồn tại: {file_path}")

    if path.suffix.lower() not in _SUPPORTED:
        return ToolResult(
            success=False, output="",
            error=f"Định dạng không hỗ trợ: {path.suffix}. Hỗ trợ: {', '.join(sorted(_SUPPORTED))}",
        )

    ingestor = _get_ingestor()
    result = await ingestor.ingest_file(path)
    elapsed = int((time.monotonic() - start) * 1000)

    if result.success:
        return ToolResult(
            success=True,
            output=(
                f"Đã nạp tài liệu: {path.name}\n"
                f"- Loại: {result.file_type}\n"
                f"- Số đoạn: {result.chunks_created}\n"
                f"- Tổng ký tự: {result.total_chars:,}\n"
                f"Giờ có thể hỏi về nội dung tài liệu này."
            ),
            execution_time_ms=elapsed,
            data={"chunks": result.chunks_created, "chars": result.total_chars},
        )
    else:
        return ToolResult(
            success=False, output="",
            error=f"Lỗi nạp tài liệu: {result.error}",
            execution_time_ms=elapsed,
        )


async def query_documents(query: str, top_k: int = 5) -> ToolResult:
    """Search ingested documents by semantic query."""
    start = time.monotonic()

    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Query không được để trống")

    ingestor = _get_ingestor()
    results = await ingestor.search_documents(query, top_k=top_k)
    elapsed = int((time.monotonic() - start) * 1000)

    if not results:
        return ToolResult(
            success=True,
            output="Không tìm thấy nội dung liên quan trong tài liệu đã nạp.",
            execution_time_ms=elapsed,
            data={"results": []},
        )

    lines = [f"Kết quả từ tài liệu cho: '{query}'\n"]
    for i, r in enumerate(results, 1):
        content = r.get("content", "")
        score = r.get("score", 0)
        lines.append(f"{i}. [Score: {score:.2f}]")
        lines.append(f"   {content[:500]}")
        lines.append("")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"results": len(results)},
    )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

ingest_document_tool = ToolDefinition(
    name="ingest_document",
    description="Nạp tài liệu (PDF, DOCX, TXT, MD) vào bộ nhớ JARVIS. Sau khi nạp, có thể hỏi đáp về nội dung tài liệu.",
    parameters=[
        ToolParameter(name="file_path", type="string", description="Đường dẫn file tài liệu"),
    ],
    handler=ingest_document,
    timeout_seconds=60,
)

query_documents_tool = ToolDefinition(
    name="query_documents",
    description="Tìm kiếm nội dung trong tài liệu đã nạp. Dùng khi user hỏi về tài liệu đã upload hoặc đã đọc trước đó.",
    parameters=[
        ToolParameter(name="query", type="string", description="Câu hỏi hoặc từ khóa tìm kiếm"),
        ToolParameter(name="top_k", type="integer", description="Số kết quả (mặc định 5)", required=False, default=5),
    ],
    handler=query_documents,
    timeout_seconds=15,
)
