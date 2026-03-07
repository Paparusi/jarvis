"""Skill Auto-Generator — Create skills from repeated workflow patterns.

Analyzes training data logs to detect patterns:
1. Group interactions by topic/intent
2. Find repeated successful patterns (3+ times)
3. Extract workflow steps from reasoning traces
4. Generate SKILL.md with proper frontmatter
5. Save to workspace/skills/auto/

Can be triggered:
- Manually via /generate-skills command
- Automatically during future Dreamtime cycle
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.memory.embeddings import cosine_similarity, get_embedding
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("skills.generator")


class SkillGenerator:
    """Automatically generate skills from interaction patterns."""

    def __init__(self, min_occurrences: int = 3, similarity_threshold: float = 0.80) -> None:
        self._min_occurrences = min_occurrences
        self._similarity_threshold = similarity_threshold
        self._raw_dir = get_project_root() / "training" / "data" / "raw"
        self._skills_dir = get_project_root() / "workspace" / "skills" / "auto"
        self._skills_dir.mkdir(parents=True, exist_ok=True)

    def _load_interactions(self) -> list[dict]:
        """Load all raw interactions."""
        records = []
        if not self._raw_dir.exists():
            return records
        for f in sorted(self._raw_dir.glob("interactions-*.jsonl")):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return records

    async def detect_patterns(self) -> list[dict]:
        """Detect repeated interaction patterns that could become skills.

        Returns list of pattern dicts with:
        - topic: detected topic/intent
        - count: number of similar interactions
        - examples: sample user messages
        - tools_used: tools frequently used in these interactions
        - avg_confidence: average confidence score
        """
        records = self._load_interactions()
        if len(records) < self._min_occurrences:
            return []

        # Filter to successful interactions only
        good_records = [
            r for r in records
            if r.get("model_response", "")
            and not r.get("model_response", "").startswith("Xin lỗi")
            and len(r.get("model_response", "")) > 20
        ]

        if not good_records:
            return []

        # Cluster by embedding similarity
        clusters = await self._cluster_interactions(good_records)

        # Filter clusters with enough occurrences
        patterns = []
        for cluster in clusters:
            if len(cluster) >= self._min_occurrences:
                pattern = self._analyze_cluster(cluster)
                if pattern:
                    patterns.append(pattern)

        log.info("patterns_detected", count=len(patterns))
        return patterns

    async def _cluster_interactions(self, records: list[dict]) -> list[list[dict]]:
        """Cluster interactions by message similarity."""
        if not records:
            return []

        # Compute embeddings for all user messages
        embeddings = []
        for r in records:
            emb = await get_embedding(r["user_message"][:200])
            embeddings.append(emb)

        # Simple greedy clustering
        clusters: list[list[dict]] = []
        used = set()

        for i, record in enumerate(records):
            if i in used:
                continue

            cluster = [record]
            used.add(i)

            for j in range(i + 1, len(records)):
                if j in used:
                    continue
                sim = cosine_similarity(embeddings[i], embeddings[j])
                if sim >= self._similarity_threshold:
                    cluster.append(records[j])
                    used.add(j)

            clusters.append(cluster)

        return clusters

    def _analyze_cluster(self, cluster: list[dict]) -> dict | None:
        """Analyze a cluster of similar interactions to extract a pattern."""
        if not cluster:
            return None

        # Extract common elements
        messages = [r["user_message"] for r in cluster]
        tools_used = []
        skills_used = []

        for r in cluster:
            trace = r.get("reasoning_trace", "")
            if trace:
                try:
                    trace_data = json.loads(trace) if isinstance(trace, str) else trace
                    if isinstance(trace_data, list):
                        for tc in trace_data:
                            if isinstance(tc, dict) and "tool" in tc:
                                tools_used.append(tc["tool"])
                except (json.JSONDecodeError, TypeError):
                    pass

            for skill in r.get("skills_used", []):
                skills_used.append(skill)

        # Count tool frequency
        tool_counts = Counter(tools_used)
        skill_counts = Counter(skills_used)

        # Detect common keywords for topic name
        topic = self._extract_topic(messages)
        if not topic:
            return None

        # Average confidence
        confidences = [r.get("confidence_score", 0) for r in cluster if r.get("confidence_score")]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "topic": topic,
            "count": len(cluster),
            "examples": messages[:5],
            "tools_used": dict(tool_counts.most_common(5)),
            "skills_used": dict(skill_counts.most_common(3)),
            "avg_confidence": round(avg_confidence, 2),
            "models_used": Counter(r.get("model_used", "") for r in cluster).most_common(3),
        }

    @staticmethod
    def _normalize_name(raw: str) -> str:
        """Normalize a topic string to Anthropic-spec skill name.

        Converts Vietnamese/unicode chars to ASCII, lowercases, keeps only
        alphanumeric + hyphens, collapses consecutive hyphens, strips edges.
        """
        # NFD decomposition strips accents (e.g. ắ→a, ệ→e)
        text = unicodedata.normalize("NFD", raw)
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")
        # Vietnamese đ→d
        text = text.replace("đ", "d").replace("Đ", "D")
        text = text.lower()
        # Replace non-alphanumeric with hyphen
        text = re.sub(r"[^a-z0-9]+", "-", text)
        # Collapse consecutive hyphens, strip leading/trailing
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text[:64]  # max 64 chars per spec

    def _extract_topic(self, messages: list[str]) -> str:
        """Extract a topic name from a cluster of similar messages."""
        # Combine and find common words
        all_text = " ".join(messages).lower()

        # Remove common Vietnamese stop words
        stop_words = {
            "tôi", "bạn", "cho", "của", "với", "và", "là", "để", "có", "không",
            "này", "đó", "thì", "nếu", "khi", "từ", "trong", "trên", "dưới",
            "giúp", "hãy", "xin", "vui", "lòng", "cần", "muốn", "được",
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "i", "you", "he", "she", "it", "we", "they", "me", "my",
            "to", "for", "of", "in", "on", "at", "by", "with", "from",
            "can", "how", "what", "help", "please", "do", "make",
        }

        words = re.findall(r"\b[a-záàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ]{3,}\b", all_text)
        word_counts = Counter(w for w in words if w not in stop_words)

        if not word_counts:
            return ""

        # Take top 2-3 keywords as topic
        top_words = [w for w, _ in word_counts.most_common(3)]
        raw_name = "-".join(top_words)
        return self._normalize_name(raw_name)

    def generate_skill(self, pattern: dict) -> str | None:
        """Generate a SKILL.md from a detected pattern.

        Returns the path to the generated skill, or None if generation fails.
        """
        topic = pattern["topic"]
        if not topic:
            return None

        # Check if skill already exists
        skill_dir = self._skills_dir / topic
        if skill_dir.exists():
            log.info("skill_already_exists", topic=topic)
            return None

        # Build SKILL.md content
        tools_section = ""
        if pattern["tools_used"]:
            tools_list = ", ".join(pattern["tools_used"].keys())
            tools_section = f"\n## Tools\nSử dụng: {tools_list}\n"

        examples_section = ""
        if pattern["examples"]:
            example_lines = "\n".join(f"- \"{ex[:80]}\"" for ex in pattern["examples"][:3])
            examples_section = f"\n## Ví dụ trigger\n{example_lines}\n"

        # Detect required tools
        req_tools = list(pattern["tools_used"].keys())[:3] if pattern["tools_used"] else []

        content = f"""---
name: {topic}
description: >
  Tự động phát hiện từ {pattern['count']} interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến {topic.replace('-', ' ')}.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: {pattern['avg_confidence']:.2f}
    usage_count: {pattern['count']}
    priority: 0.6
    mcp_tools: {json.dumps(req_tools)}
---

# {topic.replace('-', ' ').title()}

## Khi nào kích hoạt
Khi user hỏi về {topic.replace('-', ' ')} hoặc các chủ đề liên quan.
Skill này được tự động tạo từ {pattern['count']} interactions thành công.
{tools_section}{examples_section}
## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user
"""

        # Save
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")

        log.info(
            "skill_generated",
            topic=topic,
            path=str(skill_path),
            pattern_count=pattern["count"],
        )
        return str(skill_path)

    async def run(self) -> dict:
        """Full pipeline: detect patterns → generate skills.

        Returns stats about what was generated.
        """
        patterns = await self.detect_patterns()
        generated = []

        for pattern in patterns:
            path = self.generate_skill(pattern)
            if path:
                generated.append({
                    "topic": pattern["topic"],
                    "path": path,
                    "interactions": pattern["count"],
                })

        stats = {
            "patterns_detected": len(patterns),
            "skills_generated": len(generated),
            "generated": generated,
        }
        log.info("skill_generation_complete", **stats)
        return stats
