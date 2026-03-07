---
name: mcp-memory
description: >
  Tools from MCP server 'memory'.
  Available tools: create_entities, create_relations, add_observations, delete_entities, delete_observations, delete_relations, read_graph, search_nodes, open_nodes
version: 1.0.0
metadata:
  jarvis:
    auto_generated: true
    created_by: mcp_bridge
    category: mcp
    priority: 0.7
---

# MCP: memory

## Available Tools
- `create_entities`: Create multiple new entities in the knowledge graph
- `create_relations`: Create multiple new relations between entities in the knowledge graph. Relations should be in active voice
- `add_observations`: Add new observations to existing entities in the knowledge graph
- `delete_entities`: Delete multiple entities and their associated relations from the knowledge graph
- `delete_observations`: Delete specific observations from entities in the knowledge graph
- `delete_relations`: Delete multiple relations from the knowledge graph
- `read_graph`: Read the entire knowledge graph
- `search_nodes`: Search for nodes in the knowledge graph based on a query
- `open_nodes`: Open specific nodes in the knowledge graph by their names

## Workflow
1. Identify which MCP tool is needed for the user's request
2. Call the appropriate tool with correct parameters
3. Format the result for the user

## Rules
- Always use the MCP-prefixed tool name (e.g., `mcp_memory_toolname`)
- Handle errors gracefully — MCP servers may be unavailable
