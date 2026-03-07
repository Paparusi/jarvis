---
name: mcp-builder
description: >
  Guide for creating MCP (Model Context Protocol) servers that enable LLMs
  to interact with external services through well-designed tools. Use when
  building MCP servers to integrate external APIs or services, whether in
  Python (FastMCP) or Node/TypeScript (MCP SDK).
version: 1.0.0
license: MIT
metadata:
  jarvis:
    emoji: "🔌"
    category: meta
    priority: 0.75
    created_by: ported
    requires:
      bins: [python3]
    mcp_tools: []
---

# MCP Server Development Guide

## Khi nào kích hoạt
- User muốn tạo MCP server mới
- User muốn integrate external API vào JARVIS qua MCP
- User hỏi về cách viết MCP tools

## Workflow

### Phase 1: Research & Planning

1. **Hiểu MCP Protocol:**
   - MCP Spec: `https://modelcontextprotocol.io/sitemap.xml`
   - Transport: streamable HTTP (remote) hoặc stdio (local)

2. **Chọn stack:**
   - **TypeScript (recommended)**: `@modelcontextprotocol/sdk`
   - **Python**: `fastmcp` hoặc `mcp` SDK

3. **Plan tools:**
   - List endpoints cần implement
   - Ưu tiên comprehensive API coverage
   - Naming convention: `prefix_action_object` (e.g., `github_create_issue`)

### Phase 2: Implementation

1. **Project structure:**
   ```
   my-mcp-server/
   ├── src/
   │   ├── server.py     # Main server
   │   ├── tools/        # Tool implementations
   │   └── utils/        # Helpers
   ├── pyproject.toml
   └── README.md
   ```

2. **Core infrastructure:**
   - API client with authentication
   - Error handling (actionable messages)
   - Response formatting (JSON/Markdown)
   - Pagination support

3. **Tool implementation checklist:**
   - Input schema (Pydantic/Zod)
   - Output schema (structured data)
   - Clear description
   - Annotations: readOnlyHint, destructiveHint, idempotentHint

### Phase 3: Review & Test

1. **Code quality:** DRY, consistent errors, full types
2. **Test with MCP Inspector:**
   - Python: `python -m py_compile server.py`
   - TS: `npx @modelcontextprotocol/inspector`

### Phase 4: Integration với JARVIS

1. Add config vào `config/mcp.yaml`
2. Test connection: `python -c "from src.skills.mcp_bridge import MCPBridge; ..."`
3. Tạo SKILL.md wrapper cho MCP tools mới

## Quy tắc
- Tool names phải descriptive, consistent prefix
- Error messages phải actionable (gợi ý solution)
- Always support pagination cho list operations
- Return focused, relevant data (tránh response quá lớn)
- Security: validate inputs, handle auth properly

## Best Practices
- API Coverage > Workflow Tools (cho flexibility)
- Concise tool descriptions (giúp LLM chọn tool đúng)
- Support both JSON và Markdown output
- Annotations giúp client hiểu tool behavior
