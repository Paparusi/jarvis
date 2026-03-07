"""Synthetic Data Generator — Diverse training data for Brain Independence.

Generates diverse Q&A pairs across 20+ categories, covering:
- Vietnamese & English conversations
- Multi-turn dialogues
- Code, DevOps, analysis, research
- Paraphrased variations of existing data
- DPO preference pairs from quality comparison

Designed to be called from CLI, scripts, or Dreamtime.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("brain.synthetic")

SYSTEM_PROMPT = "Bạn là JARVIS — trợ lý AI cá nhân thông minh."

# Extended categories — 20+ domains for diversity
CATEGORIES = [
    # === Core Vietnamese ===
    {
        "category": "greeting_vn",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp chào hỏi/trả lời tự nhiên bằng tiếng Việt. "
            "Đa dạng: chào buổi sáng/tối, hỏi thăm sức khỏe, giới thiệu, "
            "cảm ơn, tạm biệt, xin phép. Trả lời thân thiện, ấm áp."
        ),
    },
    {
        "category": "factual_vn",
        "count": 15,
        "instruction": (
            "Tạo 15 cặp Q&A kiến thức chung tiếng Việt: địa lý Việt Nam, "
            "lịch sử, khoa học, thủ đô các nước, dân số, diện tích, "
            "đơn vị đo, hệ mặt trời. Trả lời chính xác, 2-4 câu."
        ),
    },
    {
        "category": "vietnamese_culture",
        "count": 12,
        "instruction": (
            "Tạo 12 cặp Q&A về văn hóa Việt Nam: ẩm thực vùng miền, "
            "lễ hội truyền thống, phong tục tập quán, ca dao tục ngữ, "
            "nhân vật lịch sử, di sản UNESCO. Trả lời có chiều sâu."
        ),
    },
    # === Programming ===
    {
        "category": "python_coding",
        "count": 15,
        "instruction": (
            "Tạo 15 cặp Q&A lập trình Python: viết hàm, xử lý string/list/dict, "
            "file I/O, exception handling, OOP cơ bản, async/await, "
            "list comprehension, decorators. Hỏi tiếng Việt, trả lời có code."
        ),
    },
    {
        "category": "algorithms",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A về thuật toán và cấu trúc dữ liệu: sort, search, "
            "stack, queue, tree, graph, dynamic programming, Big-O. "
            "Hỏi tiếng Việt, giải thích kèm pseudocode hoặc Python."
        ),
    },
    {
        "category": "web_development",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A về web development: HTML/CSS, JavaScript, React, "
            "REST API, database SQL, authentication, deployment. "
            "Hỏi tiếng Việt, trả lời có ví dụ code."
        ),
    },
    # === DevOps & System ===
    {
        "category": "devops",
        "count": 12,
        "instruction": (
            "Tạo 12 cặp Q&A về DevOps: Docker (build/run/compose), Git workflow, "
            "CI/CD pipeline, Linux commands (systemctl, cron, awk, sed), "
            "nginx config, monitoring, log analysis. Tiếng Việt + commands."
        ),
    },
    {
        "category": "networking",
        "count": 8,
        "instruction": (
            "Tạo 8 cặp Q&A về networking: TCP/IP, DNS, HTTP/HTTPS, "
            "firewall rules, SSH tunneling, port forwarding, VPN, "
            "troubleshooting (ping, traceroute, netstat). Tiếng Việt."
        ),
    },
    # === Data & Analysis ===
    {
        "category": "data_analysis",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A về phân tích dữ liệu: pandas, CSV processing, "
            "statistical analysis, data visualization (matplotlib), "
            "data cleaning, SQL queries, JSON manipulation. Tiếng Việt + code."
        ),
    },
    {
        "category": "math_logic",
        "count": 12,
        "instruction": (
            "Tạo 12 cặp Q&A toán/logic: phép tính, phần trăm, xác suất, "
            "quy đổi đơn vị, dãy số, logic puzzle, thống kê cơ bản, "
            "lãi suất, tỷ giá. Tiếng Việt, giải chi tiết."
        ),
    },
    # === Productivity ===
    {
        "category": "planning",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A về lập kế hoạch: tạo to-do list, "
            "quản lý thời gian (Pomodoro, time blocking), lập mục tiêu SMART, "
            "tóm tắt cuộc họp, viết email chuyên nghiệp. Tiếng Việt."
        ),
    },
    {
        "category": "writing_assist",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A hỗ trợ viết: soạn email, viết CV, "
            "tóm tắt văn bản, sửa ngữ pháp, viết báo cáo, "
            "dịch Việt-Anh/Anh-Việt. Đa dạng contexts."
        ),
    },
    # === Personal Assistant ===
    {
        "category": "daily_tasks",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A trợ lý hàng ngày: nhắc lịch, gợi ý món ăn, "
            "tư vấn sức khỏe cơ bản, thời tiết, gợi ý quà tặng, "
            "travel planning, shopping advice. Tiếng Việt, thực tế."
        ),
    },
    {
        "category": "conversation",
        "count": 12,
        "instruction": (
            "Tạo 12 cặp hội thoại tự nhiên: user chia sẻ vui/buồn/stress, "
            "hỏi ý kiến, tâm sự, than phiền, kể chuyện cười. "
            "JARVIS phản hồi empathetic, hữu ích, tự nhiên. Tiếng Việt."
        ),
    },
    # === English ===
    {
        "category": "english_general",
        "count": 12,
        "instruction": (
            "Create 12 Q&A pairs IN ENGLISH: general knowledge, science, "
            "current events, explanations of concepts, comparisons, "
            "definitions. Natural, helpful tone."
        ),
    },
    {
        "category": "english_coding",
        "count": 10,
        "instruction": (
            "Create 10 Q&A pairs IN ENGLISH about programming: "
            "Python, JavaScript, SQL, API design, debugging tips, "
            "code review, best practices. Include code snippets."
        ),
    },
    # === Identity & Meta ===
    {
        "category": "jarvis_identity",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp Q&A về bản thân JARVIS: tên gì, ai tạo, "
            "có thể làm gì, giới hạn gì, mục tiêu gì, đang học gì. "
            "JARVIS: tên JARVIS, trợ lý AI cá nhân của Bi, "
            "đang tự học và cải thiện mỗi ngày."
        ),
    },
    {
        "category": "error_handling",
        "count": 8,
        "instruction": (
            "Tạo 8 cặp Q&A khi JARVIS KHÔNG BIẾT hoặc không chắc: "
            "user hỏi thông tin real-time, hỏi ý kiến cá nhân, "
            "hỏi dự đoán tương lai, hỏi thông tin nhạy cảm. "
            "JARVIS trả lời trung thực: 'Tôi không chắc chắn...', "
            "'Tôi cần kiểm tra thêm...', gợi ý cách tìm đáp án."
        ),
    },
    # === Security ===
    {
        "category": "security",
        "count": 8,
        "instruction": (
            "Tạo 8 cặp Q&A về bảo mật: password best practices, "
            "phishing detection, SSL/TLS, OWASP top 10, "
            "chmod permissions, SSH key management. Tiếng Việt."
        ),
    },
    # === Trading/Finance ===
    {
        "category": "finance",
        "count": 8,
        "instruction": (
            "Tạo 8 cặp Q&A về tài chính cơ bản: lãi suất kép, "
            "phân tích kỹ thuật (SMA, RSI, MACD), quản lý rủi ro, "
            "đọc báo cáo tài chính, DCA strategy. Tiếng Việt."
        ),
    },
]

GENERATION_PROMPT = """Bạn cần tạo dữ liệu training cho AI assistant tên JARVIS (trợ lý AI cá nhân).

{instruction}

QUAN TRỌNG:
- Output JSON array, mỗi phần tử có "user" và "assistant"
- Trả lời tự nhiên, dài vừa phải (50-300 từ tùy độ phức tạp)
- Đa dạng cách hỏi (không lặp pattern câu hỏi)
- Trả lời chính xác, có cấu trúc (markdown, bullet points nếu phù hợp)
- JARVIS xưng "tôi", gọi user là "bạn"
- Có thể dùng emoji vừa phải (1-2 per response)
- Code snippets dùng markdown code blocks
- Mỗi cặp Q&A phải khác nhau rõ ràng

Output ONLY valid JSON array, no explanation:
[{{"user": "...", "assistant": "..."}}, ...]"""

MULTITURN_PROMPT = """Tạo dữ liệu training MULTI-TURN cho AI assistant JARVIS.

{instruction}

Output JSON array, mỗi phần tử có "turns" là array các messages:
[
  {{
    "turns": [
      {{"role": "user", "content": "..."}},
      {{"role": "assistant", "content": "..."}},
      {{"role": "user", "content": "..."}},
      {{"role": "assistant", "content": "..."}}
    ]
  }}
]

QUAN TRỌNG:
- Mỗi conversation 2-3 lượt (4-6 messages)
- Follow-up questions tự nhiên, liên quan đến câu trước
- JARVIS nhớ context từ câu trước
- Output ONLY valid JSON array"""

MULTITURN_CATEGORIES = [
    {
        "category": "multiturn_coding",
        "count": 5,
        "instruction": (
            "Tạo 5 hội thoại multi-turn về coding: user hỏi viết code, "
            "JARVIS viết → user hỏi thêm/sửa/giải thích → JARVIS cải tiến. "
            "Python, tiếng Việt."
        ),
    },
    {
        "category": "multiturn_research",
        "count": 5,
        "instruction": (
            "Tạo 5 hội thoại multi-turn về nghiên cứu: user hỏi topic, "
            "JARVIS giải thích → user đào sâu → JARVIS chi tiết hơn. "
            "Đa dạng topics, tiếng Việt."
        ),
    },
    {
        "category": "multiturn_planning",
        "count": 5,
        "instruction": (
            "Tạo 5 hội thoại multi-turn về lập kế hoạch: user nói mục tiêu, "
            "JARVIS đề xuất plan → user hỏi chi tiết/thay đổi → JARVIS adjust. "
            "Tiếng Việt."
        ),
    },
]

DPO_PROMPT = """Tạo dữ liệu DPO (preference) cho AI training.

Cho mỗi câu hỏi, tạo 2 câu trả lời:
- "chosen": Câu trả lời TỐT (chính xác, hữu ích, có cấu trúc, empathetic)
- "rejected": Câu trả lời KÉM (mơ hồ, sai, thiếu detail, generic)

{instruction}

Output JSON array:
[
  {{
    "prompt": "câu hỏi",
    "chosen": "câu trả lời tốt",
    "rejected": "câu trả lời kém"
  }}
]

QUAN TRỌNG:
- "chosen" phải thực sự hữu ích, chi tiết, chính xác
- "rejected" phải realistic (giống LLM yếu trả lời), KHÔNG phải hoàn toàn sai
- Đa dạng câu hỏi
- Output ONLY valid JSON array"""

DPO_CATEGORIES = [
    {
        "category": "dpo_coding",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp preference về coding Python. "
            "'chosen' có code đúng + giải thích, 'rejected' có code lỗi hoặc thiếu."
        ),
    },
    {
        "category": "dpo_knowledge",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp preference về kiến thức chung (tiếng Việt). "
            "'chosen' chính xác + chi tiết, 'rejected' mơ hồ hoặc thiếu thông tin."
        ),
    },
    {
        "category": "dpo_assistant",
        "count": 10,
        "instruction": (
            "Tạo 10 cặp preference về trợ lý cá nhân. "
            "'chosen' thực tế + actionable, 'rejected' generic + không hữu ích."
        ),
    },
    {
        "category": "dpo_safety",
        "count": 5,
        "instruction": (
            "Tạo 5 cặp preference về safety: user hỏi thông tin nhạy cảm/nguy hiểm. "
            "'chosen' từ chối lịch sự + gợi ý thay thế, 'rejected' trả lời không an toàn."
        ),
    },
]


class SyntheticDataGenerator:
    """Generate diverse synthetic training data using LLM."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.7,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._root = get_project_root()
        self._output_dir = self._root / "training" / "data" / "processed"
        self._dpo_dir = self._root / "training" / "data" / "preferences"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._dpo_dir.mkdir(parents=True, exist_ok=True)

    async def generate_sft(
        self,
        categories: list[dict] | None = None,
        max_concurrent: int = 3,
    ) -> dict[str, Any]:
        """Generate SFT training data across all categories.

        Returns stats dict with counts per category.
        """
        import litellm

        cats = categories or CATEGORIES
        all_records: list[dict] = []
        stats: dict[str, int] = {}
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _gen_one(cat: dict) -> tuple[str, list[dict]]:
            async with semaphore:
                return cat["category"], await self._generate_category(
                    cat, GENERATION_PROMPT,
                )

        tasks = [_gen_one(c) for c in cats]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                log.error("synthetic_category_error", error=str(result))
                continue
            category, pairs = result
            records = self._format_sft(pairs, category)
            all_records.extend(records)
            stats[category] = len(records)
            log.info("synthetic_generated", category=category, count=len(records))

        # Save
        output_file = self._output_dir / "synthetic_sft.jsonl"
        self._save_jsonl(all_records, output_file)

        total = len(all_records)
        log.info("synthetic_sft_complete", total=total, categories=len(stats))
        return {
            "total_records": total,
            "categories": stats,
            "output_file": str(output_file),
        }

    async def generate_multiturn(
        self,
        categories: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Generate multi-turn conversation training data."""
        cats = categories or MULTITURN_CATEGORIES
        all_records: list[dict] = []
        stats: dict[str, int] = {}

        for cat in cats:
            raw = await self._generate_category(cat, MULTITURN_PROMPT)
            records = self._format_multiturn(raw, cat["category"])
            all_records.extend(records)
            stats[cat["category"]] = len(records)
            log.info("multiturn_generated", category=cat["category"], count=len(records))

        output_file = self._output_dir / "synthetic_multiturn_sft.jsonl"
        self._save_jsonl(all_records, output_file)

        return {
            "total_records": len(all_records),
            "categories": stats,
            "output_file": str(output_file),
        }

    async def generate_dpo(
        self,
        categories: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Generate synthetic DPO preference pairs."""
        cats = categories or DPO_CATEGORIES
        all_pairs: list[dict] = []
        stats: dict[str, int] = {}

        for cat in cats:
            raw = await self._generate_category(cat, DPO_PROMPT)
            pairs = self._format_dpo(raw, cat["category"])
            all_pairs.extend(pairs)
            stats[cat["category"]] = len(pairs)
            log.info("dpo_generated", category=cat["category"], count=len(pairs))

        output_file = self._dpo_dir / "synthetic_dpo.jsonl"
        self._save_jsonl(all_pairs, output_file)

        return {
            "total_pairs": len(all_pairs),
            "categories": stats,
            "output_file": str(output_file),
        }

    async def generate_paraphrases(
        self,
        source_file: Path | None = None,
        max_records: int = 50,
    ) -> dict[str, Any]:
        """Generate paraphrased versions of existing training data."""
        import litellm

        src = source_file or (self._output_dir / "combined_sft.jsonl")
        if not src.exists():
            return {"total": 0, "error": "Source file not found"}

        # Load source records
        records = []
        for line in src.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if "messages" in r and len(r["messages"]) >= 3:
                    records.append(r)
            except json.JSONDecodeError:
                continue

        # Sample subset
        sample = random.sample(records, min(max_records, len(records)))

        # Batch paraphrase
        paraphrased: list[dict] = []
        batch_size = 5

        for i in range(0, len(sample), batch_size):
            batch = sample[i:i + batch_size]
            originals = [
                {"user": r["messages"][1]["content"],
                 "assistant": r["messages"][2]["content"]}
                for r in batch
            ]

            prompt = (
                "Paraphrase các cặp Q&A sau. Giữ nguyên ý nghĩa nhưng thay đổi "
                "cách diễn đạt (từ vựng, cấu trúc câu khác). "
                "Output JSON array:\n"
                f"{json.dumps(originals, ensure_ascii=False)}\n\n"
                "Output ONLY paraphrased JSON array:\n"
                '[{"user": "...", "assistant": "..."}, ...]'
            )

            try:
                resp = await litellm.acompletion(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096,
                    temperature=self._temperature,
                )
                content = resp.choices[0].message.content or ""
                parsed = self._extract_json_array(content)
                for pair in parsed:
                    if pair.get("user") and pair.get("assistant"):
                        paraphrased.append({
                            "messages": [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": pair["user"]},
                                {"role": "assistant", "content": pair["assistant"]},
                            ],
                            "metadata": {
                                "source_model": self._model,
                                "category": "paraphrase",
                                "synthetic": True,
                            },
                        })
            except Exception as e:
                log.error("paraphrase_error", batch=i, error=str(e))

        output_file = self._output_dir / "synthetic_paraphrases.jsonl"
        self._save_jsonl(paraphrased, output_file)

        return {
            "total": len(paraphrased),
            "source_sampled": len(sample),
            "output_file": str(output_file),
        }

    async def generate_all(self) -> dict[str, Any]:
        """Run all generation pipelines and combine results."""
        results: dict[str, Any] = {}

        # 1. SFT data
        sft_result = await self.generate_sft()
        results["sft"] = sft_result

        # 2. Multi-turn
        mt_result = await self.generate_multiturn()
        results["multiturn"] = mt_result

        # 3. DPO pairs
        dpo_result = await self.generate_dpo()
        results["dpo"] = dpo_result

        # 4. Combine all SFT into combined file
        combined = self._combine_sft()
        results["combined"] = combined

        total = sft_result["total_records"] + mt_result["total_records"]
        results["total_sft"] = total
        results["total_dpo"] = dpo_result["total_pairs"]

        log.info("synthetic_all_complete",
                 total_sft=total, total_dpo=dpo_result["total_pairs"])
        return results

    def _combine_sft(self) -> dict[str, Any]:
        """Combine all SFT sources into combined_sft.jsonl."""
        all_records: list[dict] = []
        seen_messages: set[str] = set()

        # Load from all SFT sources
        patterns = [
            "sft_*.jsonl",
            "synthetic_sft.jsonl",
            "synthetic_multiturn_sft.jsonl",
            "synthetic_paraphrases.jsonl",
        ]

        for pattern in patterns:
            for f in sorted(self._output_dir.glob(pattern)):
                for line in f.read_text(encoding="utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if "messages" not in record:
                            continue
                        # Dedup by user message
                        msgs = record.get("messages", [])
                        if len(msgs) >= 2:
                            key = msgs[1].get("content", "").strip().lower()
                            if key and key not in seen_messages:
                                seen_messages.add(key)
                                all_records.append(record)
                    except json.JSONDecodeError:
                        continue

        combined_file = self._output_dir / "combined_sft.jsonl"
        self._save_jsonl(all_records, combined_file)

        return {
            "total_records": len(all_records),
            "output_file": str(combined_file),
        }

    async def _generate_category(
        self,
        cat: dict,
        prompt_template: str,
    ) -> list[dict]:
        """Generate data for a single category via LLM."""
        import litellm

        prompt = prompt_template.format(instruction=cat["instruction"])

        try:
            resp = await litellm.acompletion(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=self._temperature,
            )
            content = resp.choices[0].message.content or ""
            return self._extract_json_array(content)
        except Exception as e:
            log.error("generation_error", category=cat["category"], error=str(e))
            return []

    def _extract_json_array(self, text: str) -> list[dict]:
        """Extract JSON array from LLM response text.

        Handles: raw JSON, ```json blocks, nested code fences.
        """
        import re

        # Strip markdown code fences first
        cleaned = re.sub(r"```(?:json)?\s*\n?", "", text)
        cleaned = cleaned.strip()

        start = cleaned.find("[")
        end = cleaned.rfind("]") + 1
        if start >= 0 and end > start:
            json_str = cleaned[start:end]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # Try fixing common issues: trailing commas
                json_str = re.sub(r",\s*]", "]", json_str)
                json_str = re.sub(r",\s*}", "}", json_str)
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass
        return []

    def _format_sft(self, pairs: list[dict], category: str) -> list[dict]:
        """Format Q&A pairs into ChatML SFT records."""
        records = []
        for pair in pairs:
            user_msg = pair.get("user", "")
            asst_msg = pair.get("assistant", "")
            if not user_msg or not asst_msg:
                continue
            if len(asst_msg.strip()) < 10:
                continue
            records.append({
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": asst_msg},
                ],
                "metadata": {
                    "source_model": self._model,
                    "category": category,
                    "synthetic": True,
                },
            })
        return records

    def _format_multiturn(self, raw: list[dict], category: str) -> list[dict]:
        """Format multi-turn conversations into ChatML."""
        records = []
        for item in raw:
            turns = item.get("turns", [])
            if len(turns) < 4:
                continue
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for turn in turns:
                role = turn.get("role", "")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
            if len(messages) >= 5:  # system + at least 2 turns
                records.append({
                    "messages": messages,
                    "metadata": {
                        "source_model": self._model,
                        "category": category,
                        "synthetic": True,
                        "multiturn": True,
                    },
                })
        return records

    def _format_dpo(self, raw: list[dict], category: str) -> list[dict]:
        """Format DPO preference pairs."""
        pairs = []
        for item in raw:
            prompt = item.get("prompt", "")
            chosen = item.get("chosen", "")
            rejected = item.get("rejected", "")
            if prompt and chosen and rejected:
                pairs.append({
                    "prompt": prompt,
                    "chosen": chosen,
                    "rejected": rejected,
                    "chosen_model": f"synthetic_{self._model}",
                    "rejected_model": "synthetic_weak",
                    "category": category,
                })
        return pairs

    @staticmethod
    def _save_jsonl(data: list[dict], path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def get_stats(self) -> dict[str, Any]:
        """Get statistics on generated synthetic data."""
        sft_file = self._output_dir / "synthetic_sft.jsonl"
        mt_file = self._output_dir / "synthetic_multiturn_sft.jsonl"
        para_file = self._output_dir / "synthetic_paraphrases.jsonl"
        dpo_file = self._dpo_dir / "synthetic_dpo.jsonl"
        combined = self._output_dir / "combined_sft.jsonl"

        def _count(p: Path) -> int:
            if not p.exists():
                return 0
            return sum(1 for line in p.read_text().strip().split("\n") if line.strip())

        return {
            "sft_records": _count(sft_file),
            "multiturn_records": _count(mt_file),
            "paraphrase_records": _count(para_file),
            "dpo_pairs": _count(dpo_file),
            "combined_sft": _count(combined),
        }
