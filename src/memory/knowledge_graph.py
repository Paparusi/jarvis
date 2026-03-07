"""Knowledge Graph — entity-relationship tracking for JARVIS memory.

Tracks entities (people, projects, tools, concepts, locations, orgs)
and their relationships. Uses SQLite for persistence, no embeddings needed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("memory.knowledge_graph")

# Valid entity types
ENTITY_TYPES = {"person", "project", "tool", "concept", "location", "org"}

# Valid relation types
RELATION_TYPES = {
    "uses",
    "knows",
    "works_on",
    "located_in",
    "related_to",
    "created",
    "owns",
    "part_of",
}

# Known tech keywords for entity extraction
_TECH_KEYWORDS: set[str] = {
    "python", "javascript", "typescript", "java", "rust", "go", "c++", "c#",
    "ruby", "php", "swift", "kotlin", "scala", "haskell", "elixir", "clojure",
    "docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins", "nginx",
    "apache", "git", "github", "gitlab", "bitbucket",
    "react", "vue", "angular", "svelte", "nextjs", "nuxt", "remix", "astro",
    "django", "flask", "fastapi", "express", "nestjs", "spring", "rails",
    "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis", "kafka",
    "elasticsearch", "neo4j", "cassandra", "dynamodb",
    "aws", "azure", "gcp", "vercel", "netlify", "heroku", "cloudflare",
    "linux", "ubuntu", "debian", "centos", "macos", "windows", "wsl", "wsl2",
    "ollama", "langchain", "llamaindex", "openai", "anthropic", "claude",
    "chatgpt", "huggingface", "pytorch", "tensorflow", "keras",
    "html", "css", "sass", "tailwind", "bootstrap",
    "graphql", "rest", "grpc", "websocket",
    "npm", "pip", "cargo", "yarn", "pnpm", "conda",
    "vscode", "vim", "neovim", "emacs", "jetbrains", "cursor",
    "pandas", "numpy", "scipy", "matplotlib", "jupyter",
    "celery", "rabbitmq", "airflow", "spark", "hadoop",
    "litellm", "groq", "whisper", "telegram", "fasttext", "fastembed",
}

# Vietnamese project/tool trigger words
_VN_PROJECT_TRIGGERS = re.compile(
    r"(?:dự\s*án|project)\s+([A-Za-zÀ-ỹ][\w\-]*(?:\s+[\w\-]+){0,3})",
    re.IGNORECASE | re.UNICODE,
)

# URL/domain pattern
_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?([a-zA-Z0-9\-]+(?:\.[a-zA-Z]{2,})+)(?:/\S*)?",
)

# Capitalized name pattern (2-4 words starting with uppercase)
_NAME_PATTERN = re.compile(
    r"\b([A-ZÀ-Ỹ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){0,3})\b",
)

# Common words to exclude from name detection
_NAME_STOPWORDS: set[str] = {
    "The", "This", "That", "These", "Those", "There", "Here",
    "What", "When", "Where", "Which", "Who", "How", "Why",
    "And", "But", "For", "Not", "With", "From", "Into",
    "About", "After", "Before", "Between", "During", "Without",
    "Hello", "Please", "Thanks", "Sorry", "Yes", "No",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "January", "February", "March",
    "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
    "Today", "Tomorrow", "Yesterday",
    "Can", "Could", "Would", "Should", "Will", "Shall",
    "Have", "Has", "Had", "Been", "Being",
}


class KnowledgeGraph:
    """Entity-relationship knowledge graph backed by SQLite.

    Tracks entities and their relationships extracted from conversations.
    Designed to enrich JARVIS's contextual understanding of the user's world.
    """

    def __init__(self) -> None:
        """Initialize the knowledge graph and create tables if needed."""
        self._conn = get_connection()
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        stats = self.get_stats()
        log.info(
            "knowledge_graph_initialized",
            entities=stats["entity_count"],
            relations=stats["relation_count"],
        )

    def _init_tables(self) -> None:
        """Create knowledge graph tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                mention_count INTEGER DEFAULT 1
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_entity_name
                ON kg_entities(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_kg_entity_type
                ON kg_entities(entity_type);

            CREATE TABLE IF NOT EXISTS kg_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                strength REAL DEFAULT 0.5,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                mention_count INTEGER DEFAULT 1,
                FOREIGN KEY (source_id) REFERENCES kg_entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES kg_entities(id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_relation_unique
                ON kg_relations(source_id, target_id, relation_type);
            CREATE INDEX IF NOT EXISTS idx_kg_relation_source
                ON kg_relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_kg_relation_target
                ON kg_relations(target_id);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def add_entity(
        self,
        name: str,
        entity_type: str,
        properties: dict[str, Any] | None = None,
    ) -> int:
        """Add or update an entity. Returns the entity id.

        If the entity already exists (case-insensitive name match),
        its mention_count is incremented, last_seen is updated,
        and any new properties are merged.

        Args:
            name: The entity name.
            entity_type: One of person/project/tool/concept/location/org.
            properties: Optional dict of extra metadata.

        Returns:
            The entity row id.

        Raises:
            ValueError: If entity_type is not valid.
        """
        name = name.strip()
        if not name:
            raise ValueError("Entity name cannot be empty")

        entity_type = entity_type.lower().strip()
        if entity_type not in ENTITY_TYPES:
            raise ValueError(
                f"Invalid entity_type '{entity_type}'. "
                f"Must be one of: {', '.join(sorted(ENTITY_TYPES))}"
            )

        props_json = json.dumps(properties or {}, ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()

        existing = self.get_entity(name)

        if existing is not None:
            # Merge properties
            merged = json.loads(existing["properties"]) if existing["properties"] else {}
            if properties:
                merged.update(properties)

            self._conn.execute(
                """UPDATE kg_entities
                   SET mention_count = mention_count + 1,
                       last_seen = ?,
                       properties = ?,
                       entity_type = ?
                   WHERE id = ?""",
                (now, json.dumps(merged, ensure_ascii=False), entity_type, existing["id"]),
            )
            self._conn.commit()
            log.debug("entity_updated", name=name, mentions=existing["mention_count"] + 1)
            return existing["id"]

        cursor = self._conn.execute(
            """INSERT INTO kg_entities (name, entity_type, properties, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)""",
            (name, entity_type, props_json, now, now),
        )
        self._conn.commit()
        entity_id = cursor.lastrowid
        log.debug("entity_created", name=name, entity_type=entity_type, id=entity_id)
        return entity_id

    def add_relation(
        self,
        source_name: str,
        target_name: str,
        relation_type: str,
        properties: dict[str, Any] | None = None,
    ) -> int:
        """Add or strengthen a relation between two entities. Returns the relation id.

        Both entities must already exist. If the relation already exists,
        its strength is increased (capped at 1.0), mention_count incremented,
        and last_seen updated.

        Args:
            source_name: Name of the source entity.
            target_name: Name of the target entity.
            relation_type: One of uses/knows/works_on/located_in/related_to/created/owns/part_of.
            properties: Optional dict of extra metadata.

        Returns:
            The relation row id.

        Raises:
            ValueError: If relation_type is invalid or entities don't exist.
        """
        relation_type = relation_type.lower().strip()
        if relation_type not in RELATION_TYPES:
            raise ValueError(
                f"Invalid relation_type '{relation_type}'. "
                f"Must be one of: {', '.join(sorted(RELATION_TYPES))}"
            )

        source = self.get_entity(source_name)
        target = self.get_entity(target_name)

        if source is None:
            raise ValueError(f"Source entity '{source_name}' not found")
        if target is None:
            raise ValueError(f"Target entity '{target_name}' not found")

        source_id = source["id"]
        target_id = target["id"]
        now = datetime.now(timezone.utc).isoformat()

        # Check for existing relation
        row = self._conn.execute(
            """SELECT id, strength, properties, mention_count
               FROM kg_relations
               WHERE source_id = ? AND target_id = ? AND relation_type = ?""",
            (source_id, target_id, relation_type),
        ).fetchone()

        if row is not None:
            # Strengthen existing relation
            new_strength = min(1.0, row["strength"] + 0.1)
            merged = json.loads(row["properties"]) if row["properties"] else {}
            if properties:
                merged.update(properties)

            self._conn.execute(
                """UPDATE kg_relations
                   SET strength = ?,
                       mention_count = mention_count + 1,
                       last_seen = ?,
                       properties = ?
                   WHERE id = ?""",
                (new_strength, now, json.dumps(merged, ensure_ascii=False), row["id"]),
            )
            self._conn.commit()
            log.debug(
                "relation_strengthened",
                source=source_name,
                target=target_name,
                relation=relation_type,
                strength=new_strength,
            )
            return row["id"]

        props_json = json.dumps(properties or {}, ensure_ascii=False)
        cursor = self._conn.execute(
            """INSERT INTO kg_relations
               (source_id, target_id, relation_type, properties, strength, first_seen, last_seen)
               VALUES (?, ?, ?, ?, 0.5, ?, ?)""",
            (source_id, target_id, relation_type, props_json, now, now),
        )
        self._conn.commit()
        rel_id = cursor.lastrowid
        log.debug(
            "relation_created",
            source=source_name,
            target=target_name,
            relation=relation_type,
            id=rel_id,
        )
        return rel_id

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_entity(self, name: str) -> dict[str, Any] | None:
        """Find an entity by name (case-insensitive).

        Args:
            name: The entity name to look up.

        Returns:
            A dict with entity fields, or None if not found.
        """
        row = self._conn.execute(
            "SELECT * FROM kg_entities WHERE name = ? COLLATE NOCASE",
            (name.strip(),),
        ).fetchone()

        if row is None:
            return None
        return dict(row)

    def get_relations(
        self,
        entity_name: str,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Get all relations for an entity.

        Args:
            entity_name: The entity name.
            direction: "outgoing", "incoming", or "both" (default).

        Returns:
            List of dicts with relation info including source/target names.
        """
        entity = self.get_entity(entity_name)
        if entity is None:
            return []

        entity_id = entity["id"]
        results: list[dict[str, Any]] = []

        if direction in ("outgoing", "both"):
            rows = self._conn.execute(
                """SELECT r.*, e.name AS target_name, e.entity_type AS target_type
                   FROM kg_relations r
                   JOIN kg_entities e ON e.id = r.target_id
                   WHERE r.source_id = ?
                   ORDER BY r.strength DESC""",
                (entity_id,),
            ).fetchall()
            for row in rows:
                d = dict(row)
                d["source_name"] = entity_name
                d["direction"] = "outgoing"
                results.append(d)

        if direction in ("incoming", "both"):
            rows = self._conn.execute(
                """SELECT r.*, e.name AS source_name, e.entity_type AS source_type
                   FROM kg_relations r
                   JOIN kg_entities e ON e.id = r.source_id
                   WHERE r.target_id = ?
                   ORDER BY r.strength DESC""",
                (entity_id,),
            ).fetchall()
            for row in rows:
                d = dict(row)
                d["target_name"] = entity_name
                d["direction"] = "incoming"
                results.append(d)

        return results

    def get_neighbors(
        self,
        entity_name: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Get an entity and its related entities via BFS up to given depth.

        Args:
            entity_name: Starting entity name.
            depth: How many hops to traverse (default 1).

        Returns:
            Dict with "center" entity, "entities" (all discovered entities),
            and "relations" (all discovered relations).
        """
        center = self.get_entity(entity_name)
        if center is None:
            return {"center": None, "entities": [], "relations": []}

        visited_ids: set[int] = {center["id"]}
        all_entities: list[dict[str, Any]] = [center]
        all_relations: list[dict[str, Any]] = []

        queue: deque[tuple[int, str, int]] = deque()
        queue.append((center["id"], center["name"], 0))

        while queue:
            current_id, current_name, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            relations = self.get_relations(current_name)
            for rel in relations:
                all_relations.append(rel)

                # Determine the neighbor id and name
                if rel["source_id"] == current_id:
                    neighbor_id = rel["target_id"]
                    neighbor_name = rel.get("target_name", "")
                else:
                    neighbor_id = rel["source_id"]
                    neighbor_name = rel.get("source_name", "")

                if neighbor_id not in visited_ids:
                    visited_ids.add(neighbor_id)
                    neighbor = self.get_entity(neighbor_name)
                    if neighbor is not None:
                        all_entities.append(neighbor)
                        queue.append((neighbor_id, neighbor_name, current_depth + 1))

        # Deduplicate relations by id
        seen_rel_ids: set[int] = set()
        unique_relations: list[dict[str, Any]] = []
        for rel in all_relations:
            if rel["id"] not in seen_rel_ids:
                seen_rel_ids.add(rel["id"])
                unique_relations.append(rel)

        return {
            "center": center,
            "entities": all_entities,
            "relations": unique_relations,
        }

    def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search entities by name using LIKE matching.

        Args:
            query: Search query string.
            entity_type: Optional filter by entity type.
            limit: Maximum results to return (default 10).

        Returns:
            List of matching entity dicts, ordered by mention_count descending.
        """
        query = query.strip()
        if not query:
            return []

        like_pattern = f"%{query}%"

        if entity_type is not None:
            entity_type = entity_type.lower().strip()
            rows = self._conn.execute(
                """SELECT * FROM kg_entities
                   WHERE name LIKE ? COLLATE NOCASE
                     AND entity_type = ?
                   ORDER BY mention_count DESC
                   LIMIT ?""",
                (like_pattern, entity_type, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM kg_entities
                   WHERE name LIKE ? COLLATE NOCASE
                   ORDER BY mention_count DESC
                   LIMIT ?""",
                (like_pattern, limit),
            ).fetchall()

        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Entity extraction
    # ------------------------------------------------------------------

    def extract_entities_from_text(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        """Extract entities from text using pattern matching.

        Uses simple heuristics (no ML model) to detect:
        - Names: capitalized word sequences, Vietnamese name patterns
        - Projects: words following "dự án" or "project"
        - Tools/tech: known technology keywords
        - URLs/domains

        Args:
            text: Input text to extract entities from.

        Returns:
            List of (entity_name, entity_type) tuples.
        """
        entities: list[tuple[str, str]] = []
        seen: set[str] = set()

        def _add(name: str, etype: str) -> None:
            key = name.lower()
            if key not in seen and len(name) >= 2:
                seen.add(key)
                entities.append((name, etype))

        # 1. Tech keywords (case-insensitive)
        text_lower = text.lower()
        for kw in _TECH_KEYWORDS:
            # Match whole word only
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, text_lower):
                _add(kw, "tool")

        # 2. Project mentions (Vietnamese + English)
        for match in _VN_PROJECT_TRIGGERS.finditer(text):
            project_name = match.group(1).strip()
            # Remove trailing common words
            project_name = project_name.rstrip(" .,;:!?")
            if project_name and len(project_name) >= 2:
                _add(project_name, "project")

        # 3. URLs/domains
        for match in _URL_PATTERN.finditer(text):
            domain = match.group(1)
            if domain:
                _add(domain, "concept")

        # 4. Capitalized names (potential person/org names)
        for match in _NAME_PATTERN.finditer(text):
            candidate = match.group(1).strip()
            words = candidate.split()

            # Skip single stopwords
            if len(words) == 1 and words[0] in _NAME_STOPWORDS:
                continue

            # Skip if all words are stopwords
            if all(w in _NAME_STOPWORDS for w in words):
                continue

            # Skip if it's already captured as tech
            if candidate.lower() in seen:
                continue

            # 2+ word capitalized sequences are likely names
            if len(words) >= 2:
                _add(candidate, "person")
            elif len(words) == 1 and len(candidate) >= 2:
                # Single capitalized word — could be a name or org
                # Only include if it's not a common English word
                if candidate not in _NAME_STOPWORDS:
                    _add(candidate, "person")

        return entities

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def build_context(self, entity_name: str) -> str:
        """Build a text context string from an entity and its neighbors.

        Useful for injecting knowledge graph context into LLM prompts.

        Args:
            entity_name: The entity to build context for.

        Returns:
            Human-readable text summarizing the entity and its relationships.
        """
        data = self.get_neighbors(entity_name, depth=1)

        if data["center"] is None:
            return ""

        center = data["center"]
        lines: list[str] = []

        # Entity header
        props = json.loads(center["properties"]) if center["properties"] else {}
        props_str = ""
        if props:
            props_str = " (" + ", ".join(f"{k}: {v}" for k, v in props.items()) + ")"
        lines.append(
            f"[{center['entity_type'].upper()}] {center['name']}{props_str} "
            f"— mentioned {center['mention_count']} time(s)"
        )

        # Relations
        if data["relations"]:
            lines.append("Relations:")
            for rel in data["relations"]:
                source = rel.get("source_name", "?")
                target = rel.get("target_name", "?")
                strength_bar = "█" * int(rel["strength"] * 5)
                lines.append(
                    f"  {source} --[{rel['relation_type']}]--> {target} "
                    f"(strength: {rel['strength']:.1f} {strength_bar})"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stats & maintenance
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get knowledge graph statistics.

        Returns:
            Dict with entity_count, relation_count, and top_entities
            (top 10 by mention_count).
        """
        entity_count = self._conn.execute(
            "SELECT COUNT(*) FROM kg_entities"
        ).fetchone()[0]

        relation_count = self._conn.execute(
            "SELECT COUNT(*) FROM kg_relations"
        ).fetchone()[0]

        top_entities = self._conn.execute(
            """SELECT name, entity_type, mention_count
               FROM kg_entities
               ORDER BY mention_count DESC
               LIMIT 10"""
        ).fetchall()

        return {
            "entity_count": entity_count,
            "relation_count": relation_count,
            "top_entities": [
                {
                    "name": r["name"],
                    "entity_type": r["entity_type"],
                    "mention_count": r["mention_count"],
                }
                for r in top_entities
            ],
        }

    def decay(self, days: int = 30) -> int:
        """Reduce mention_count for entities not seen recently.

        Entities not updated within the given number of days have their
        mention_count reduced by 1 (floored at 0). Entities reaching 0
        are NOT deleted — they remain discoverable but deprioritized.

        Args:
            days: Number of days of inactivity before decay applies.

        Returns:
            Number of entities affected.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        cursor = self._conn.execute(
            """UPDATE kg_entities
               SET mention_count = MAX(0, mention_count - 1)
               WHERE last_seen < ? AND mention_count > 0""",
            (cutoff,),
        )
        affected = cursor.rowcount
        self._conn.commit()

        if affected > 0:
            log.info("knowledge_graph_decay", affected=affected, days=days)

        return affected
