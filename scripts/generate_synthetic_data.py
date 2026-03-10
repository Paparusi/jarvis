"""Generate synthetic training data for JARVIS using Claude API.

Creates diverse Vietnamese Q&A pairs covering:
- General chat / greetings
- Factual questions (geography, science, history)
- Vietnamese culture & language
- Math & logic
- Code assistance
- Productivity & planning
- Personal assistant tasks

Output: JSONL in ChatML format compatible with SFT training.
"""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.intelligence.claude_client import get_claude_client

SYSTEM_PROMPT = "Bạn là JARVIS — trợ lý AI cá nhân thông minh."

# Categories with example prompts for Claude to generate more
CATEGORIES = [
    {
        "category": "greeting",
        "instruction": "Tạo 8 cặp chào hỏi/trả lời tự nhiên bằng tiếng Việt. Đa dạng: chào buổi sáng, hỏi thăm, giới thiệu, cảm ơn, tạm biệt. Trả lời thân thiện, ngắn gọn.",
        "count": 8,
    },
    {
        "category": "factual_vn",
        "instruction": "Tạo 15 cặp Q&A về kiến thức chung bằng tiếng Việt: địa lý Việt Nam, lịch sử, khoa học cơ bản, toán đơn giản, thủ đô các nước, đơn vị đo. Trả lời chính xác, ngắn gọn (1-3 câu).",
        "count": 15,
    },
    {
        "category": "vietnamese_culture",
        "instruction": "Tạo 10 cặp Q&A về văn hóa Việt Nam: ẩm thực, lễ hội, phong tục, ca dao tục ngữ, nhân vật lịch sử. Trả lời bằng tiếng Việt, có chiều sâu nhưng ngắn gọn.",
        "count": 10,
    },
    {
        "category": "code_assist",
        "instruction": "Tạo 12 cặp Q&A về lập trình: viết hàm Python đơn giản, giải thích concept, debug, thuật toán cơ bản. User hỏi bằng tiếng Việt, trả lời bằng tiếng Việt có code snippet.",
        "count": 12,
    },
    {
        "category": "productivity",
        "instruction": "Tạo 10 cặp Q&A về productivity: lập kế hoạch, quản lý thời gian, ghi chú, nhắc nhở, tóm tắt. User hỏi bằng tiếng Việt, trả lời thực tế có cấu trúc.",
        "count": 10,
    },
    {
        "category": "math_logic",
        "instruction": "Tạo 10 cặp Q&A về toán/logic: phép tính, phần trăm, quy đổi đơn vị, logic đơn giản, thống kê cơ bản. Hỏi + trả lời bằng tiếng Việt.",
        "count": 10,
    },
    {
        "category": "assistant_tasks",
        "instruction": "Tạo 10 cặp Q&A cho trợ lý cá nhân: dịch ngắn, giải thích từ, so sánh, gợi ý, tư vấn đơn giản. Tiếng Việt, thân thiện, hữu ích.",
        "count": 10,
    },
    {
        "category": "tech_devops",
        "instruction": "Tạo 10 cặp Q&A về DevOps/hệ thống: Docker, Git, Linux commands, networking cơ bản, cloud concepts. User hỏi tiếng Việt, trả lời tiếng Việt có technical detail.",
        "count": 10,
    },
    {
        "category": "conversation",
        "instruction": "Tạo 10 cặp hội thoại tự nhiên: user kể chuyện/chia sẻ/than phiền/vui, JARVIS phản hồi empathetic và hữu ích. Tiếng Việt, tự nhiên.",
        "count": 10,
    },
    {
        "category": "jarvis_identity",
        "instruction": "Tạo 8 cặp Q&A về bản thân JARVIS: tên gì, ai tạo, có thể làm gì, giới hạn gì, mục tiêu gì. JARVIS trả lời: tên JARVIS, trợ lý AI cá nhân, chủ là Bi, đang học hỏi mỗi ngày.",
        "count": 8,
    },
]

GENERATION_PROMPT = """Bạn cần tạo dữ liệu training cho AI assistant tên JARVIS (trợ lý AI cá nhân).

{instruction}

QUAN TRỌNG:
- Output JSON array, mỗi phần tử có "user" và "assistant"
- Trả lời phải tự nhiên, không quá dài (50-200 từ)
- Đa dạng cách hỏi (không lặp pattern)
- Trả lời phải chính xác về mặt nội dung
- JARVIS xưng "tôi", gọi user là "bạn"
- Có thể dùng emoji vừa phải (1-2 per response)
- Code snippets dùng markdown code blocks

Output ONLY valid JSON array, no explanation:
[{{"user": "...", "assistant": "..."}}, ...]"""


async def generate_category(category: dict) -> list[dict]:
    """Generate Q&A pairs for a category using Claude."""
    prompt = GENERATION_PROMPT.format(instruction=category["instruction"])

    try:
        client = get_claude_client()
        response = await client.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.8,
        )
        content = response.choices[0].message.content or ""

        # Extract JSON from response
        # Try to find JSON array in response
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            json_str = content[start:end]
            pairs = json.loads(json_str)
            return pairs
        return []
    except Exception as e:
        print(f"  Error generating {category['category']}: {e}", file=sys.stderr)
        return []


def format_sft(pairs: list[dict], category: str) -> list[dict]:
    """Format pairs into ChatML SFT format."""
    records = []
    for pair in pairs:
        user_msg = pair.get("user", "")
        asst_msg = pair.get("assistant", "")
        if not user_msg or not asst_msg:
            continue
        records.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": asst_msg},
            ],
            "metadata": {
                "source_model": "claude-sonnet-4-20250514",
                "category": category,
                "synthetic": True,
            },
        })
    return records


async def main():
    output_dir = Path(__file__).parent.parent / "training" / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "synthetic_sft.jsonl"

    all_records = []
    total_pairs = 0

    for cat in CATEGORIES:
        print(f"Generating {cat['category']} ({cat['count']} pairs)...")
        pairs = await generate_category(cat)
        records = format_sft(pairs, cat["category"])
        all_records.extend(records)
        total_pairs += len(records)
        print(f"  → {len(records)} pairs generated")

    # Save
    with open(output_file, "w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nTotal: {total_pairs} synthetic SFT records")
    print(f"Saved to: {output_file}")

    # Also load real data and combine
    real_files = sorted((output_dir).glob("sft_*.jsonl"))
    if real_files:
        real_data = []
        # Use only the latest real SFT file
        latest = real_files[-1]
        with open(latest) as f:
            for line in f:
                real_data.append(json.loads(line))

        combined_file = output_dir / "combined_sft.jsonl"
        with open(combined_file, "w", encoding="utf-8") as f:
            for r in real_data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            for r in all_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        print(f"Combined: {len(real_data)} real + {total_pairs} synthetic = {len(real_data) + total_pairs} total")
        print(f"Saved to: {combined_file}")


if __name__ == "__main__":
    asyncio.run(main())
