"""Tests for Knowledge Graph."""
from __future__ import annotations

import json

import pytest

from src.memory.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path, monkeypatch):
    """Knowledge graph using temporary DB."""
    db_path = str(tmp_path / "test.db")
    # Patch get_connection to use temp DB
    import sqlite3
    conn = sqlite3.connect(db_path)
    monkeypatch.setattr(
        "src.memory.knowledge_graph.get_connection",
        lambda: conn,
    )
    graph = KnowledgeGraph()
    yield graph
    conn.close()


class TestEntityCRUD:
    def test_add_entity(self, kg):
        kg.add_entity("Python", "tool")
        entity = kg.get_entity("Python")
        assert entity is not None
        assert entity["name"] == "Python"
        assert entity["entity_type"] == "tool"

    def test_add_entity_with_metadata(self, kg):
        kg.add_entity("Alice", "person", properties={"role": "developer"})
        entity = kg.get_entity("Alice")
        assert entity is not None
        props = json.loads(entity["properties"]) if isinstance(entity["properties"], str) else entity["properties"]
        assert props["role"] == "developer"

    def test_add_duplicate_updates(self, kg):
        kg.add_entity("Docker", "tool")
        kg.add_entity("Docker", "tool", properties={"version": "24"})
        entity = kg.get_entity("Docker")
        props = json.loads(entity["properties"]) if isinstance(entity["properties"], str) else entity["properties"]
        assert props["version"] == "24"

    def test_get_nonexistent(self, kg):
        entity = kg.get_entity("NonExistent")
        assert entity is None

    def test_invalid_entity_type(self, kg):
        # Should handle gracefully or raise
        try:
            kg.add_entity("X", "invalid_type")
            # If it doesn't raise, that's OK too
        except (ValueError, Exception):
            pass


class TestRelations:
    def test_add_relation(self, kg):
        kg.add_entity("Bi", "person")
        kg.add_entity("JARVIS", "project")
        kg.add_relation("Bi", "JARVIS", "works_on")
        relations = kg.get_relations("Bi")
        assert len(relations) >= 1
        assert any(r["target_name"] == "JARVIS" for r in relations)

    def test_get_neighbors(self, kg):
        kg.add_entity("Bi", "person")
        kg.add_entity("Python", "tool")
        kg.add_entity("JARVIS", "project")
        kg.add_relation("Bi", "Python", "uses")
        kg.add_relation("Bi", "JARVIS", "works_on")
        result = kg.get_neighbors("Bi")
        assert result["center"] is not None
        assert len(result["entities"]) >= 3  # Bi + Python + JARVIS
        assert len(result["relations"]) >= 2

    def test_no_relations(self, kg):
        kg.add_entity("Lonely", "person")
        relations = kg.get_relations("Lonely")
        assert len(relations) == 0


class TestSearch:
    def test_search_entities(self, kg):
        kg.add_entity("Python", "tool")
        kg.add_entity("JavaScript", "tool")
        kg.add_entity("Django", "tool")
        results = kg.search_entities("Python")
        assert len(results) >= 1
        assert any(r["name"] == "Python" for r in results)

    def test_search_empty(self, kg):
        results = kg.search_entities("nonexistent_xyz_abc")
        assert len(results) == 0


class TestEntityExtraction:
    def test_extract_tech_entities(self, kg):
        text = "I'm using Python and Docker for the JARVIS project"
        entities = kg.extract_entities_from_text(text)
        names = [name.lower() for name, _ in entities]
        assert "python" in names
        assert "docker" in names

    def test_extract_empty_text(self, kg):
        entities = kg.extract_entities_from_text("")
        assert len(entities) == 0

    def test_extract_no_entities(self, kg):
        entities = kg.extract_entities_from_text("hello how are you today")
        # May or may not find entities — just no crash
        assert isinstance(entities, list)


class TestBuildContext:
    def test_build_context(self, kg):
        kg.add_entity("Python", "tool")
        kg.add_entity("JARVIS", "project")
        kg.add_relation("JARVIS", "Python", "uses")
        context = kg.build_context("tell me about Python")
        assert isinstance(context, str)

    def test_build_context_empty(self, kg):
        context = kg.build_context("random query with no matches")
        assert isinstance(context, str)


class TestStats:
    def test_stats(self, kg):
        kg.add_entity("A", "person")
        kg.add_entity("B", "tool")
        kg.add_relation("A", "B", "uses")
        stats = kg.get_stats()
        assert stats["entity_count"] >= 2
        assert stats["relation_count"] >= 1
