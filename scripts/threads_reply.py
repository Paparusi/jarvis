#!/usr/bin/env python3
"""Auto-reply to Threads comments using AI-generated responses.

Checks all recent posts for new comments, generates contextual replies.
Run via cron every 30 minutes.
"""

import json
import time
import httpx
from pathlib import Path
from datetime import datetime

THREADS_TOKEN_FILE = "/tmp/threads_token.txt"
THREADS_USER = "26305136249098236"
DATA_DIR = Path(__file__).parent.parent / "data"
REPLIED_FILE = DATA_DIR / "threads_replied.json"

# AI reply generation using Claude via OpenClaw's model
SYSTEM_PROMPT = """Bạn là Bông 🌼 (@bong.aiagent) trên Threads — chuyên về AI, công nghệ, kiếm tiền online.

Khi trả lời comment, hãy:
- Viết tự nhiên như người Việt trẻ (20-30 tuổi)
- Thân thiện, gần gũi, dùng emoji vừa phải
- Trả lời đúng trọng tâm câu hỏi/comment
- Nếu khen → cảm ơn + hỏi thêm để engagement
- Nếu hỏi → trả lời ngắn gọn + gợi ý follow
- Nếu tranh luận → tôn trọng, đưa thêm góc nhìn
- Nếu chê/troll → nhẹ nhàng, không toxic
- NGẮN GỌN: 1-3 câu max. Threads không phải blog.
- Luôn kết thúc bằng cách mời tương tác (hỏi lại, gợi ý)"""


def get_replied() -> set:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if REPLIED_FILE.exists():
        return set(json.loads(REPLIED_FILE.read_text()))
    return set()


def save_replied(replied: set):
    REPLIED_FILE.write_text(json.dumps(list(replied)[-500:]))


def generate_reply(post_text: str, comment_text: str, comment_user: str) -> str:
    """Generate AI reply using a simple heuristic + templates.
    For production, would use Claude API. For now, smart templates."""
    
    comment_lower = comment_text.lower().strip()
    
    # Detect comment type and generate appropriate reply
    
    # Praise / Agreement
    praise_words = ["hay", "đỉnh", "quá đúng", "chuẩn", "tuyệt", "good", "nice", "great", 
                    "giỏi", "amazing", "wow", "🔥", "👏", "❤️", "💯", "save", "bookmark",
                    "hữu ích", "bổ ích", "cảm ơn", "thank", "tks", "hay quá", "đúng quá"]
    
    # Questions
    question_indicators = ["?", "là gì", "thế nào", "sao", "như nào", "ở đâu", "bao nhiêu",
                          "có nên", "nên dùng", "gợi ý", "recommend", "cho hỏi", "hỏi"]
    
    # Disagreement
    disagree_words = ["không đồng ý", "sai rồi", "nhưng mà", "không hẳn", "chưa chắc",
                     "nói quá", "exaggerate", "fake", "bịa", "bs"]
    
    # Engagement / Participation
    engage_words = ["in", "tham gia", "muốn thử", "bắt đầu", "level", "đã dùng",
                   "mình đang", "tôi đang", "em đang"]
    
    import random
    
    # Check praise
    if any(w in comment_lower for w in praise_words):
        replies = [
            f"Cảm ơn @{comment_user}! Bạn đang dùng AI tool nào rồi? 😊",
            f"Thanks @{comment_user}! Share cho bạn bè nếu thấy hữu ích nhé 🙌",
            f"@{comment_user} glad you liked it! Follow để đón bài mới mỗi ngày nhé 🔔",
            f"Cảm ơn bạn! Mình post về AI mỗi ngày — stay tuned 🚀",
            f"@{comment_user} 🙏 Bạn muốn mình viết sâu hơn về topic nào?",
            f"Thanks! Bài nào bạn thấy hay nhất? 😄",
        ]
        return random.choice(replies)
    
    # Check questions  
    if any(w in comment_lower for w in question_indicators):
        if any(w in comment_lower for w in ["claude", "chatgpt", "gpt", "gemini", "perplexity"]):
            replies = [
                f"@{comment_user} Mình recommend thử cả 2-3 cái rồi chọn — mỗi AI có thế mạnh riêng. Claude viết tốt, ChatGPT đa năng, Perplexity search chuẩn 👌",
                f"@{comment_user} Tùy mục đích! Viết content → Claude. Research → Perplexity. Code → Cursor. Mình có bài so sánh chi tiết, follow nhé! 🔍",
            ]
            return random.choice(replies)
        
        if any(w in comment_lower for w in ["kiếm tiền", "tiền", "money", "income", "thu nhập"]):
            replies = [
                f"@{comment_user} Cách dễ nhất: nhận viết content cho SME bằng AI. 0 vốn, chỉ cần laptop + internet. Mình sẽ viết bài chi tiết hơn! 💰",
                f"@{comment_user} Bắt đầu từ freelance (Upwork/Fiverr) + AI tools. 30 ngày đầu focus lấy review, sau đó scale. Follow mình — sẽ share roadmap! 🚀",
            ]
            return random.choice(replies)
        
        if any(w in comment_lower for w in ["bắt đầu", "start", "mới", "beginner", "newbie", "học"]):
            replies = [
                f"@{comment_user} Bước 1: Tạo tài khoản claude.ai (free). Bước 2: Dùng nó cho 1 task công việc thật mỗi ngày. Bước 3: Mỗi tuần thêm 1 task. Đơn giản vậy thôi! ✨",
                f"@{comment_user} Đừng overthink — mở ChatGPT hoặc Claude, hỏi bất kỳ gì liên quan đến công việc. Dùng mỗi ngày, tự khắc giỏi. Mình sẽ share tips thêm! 💡",
            ]
            return random.choice(replies)
        
        # Generic question
        replies = [
            f"@{comment_user} Câu hỏi hay! Mình sẽ viết bài riêng về topic này. Follow để đón nhé 👀",
            f"@{comment_user} Good question! Nói ngắn gọn thì — AI là tool, giá trị nằm ở người dùng. Mình sẽ deep dive vào topic này! 🧠",
        ]
        return random.choice(replies)
    
    # Check disagreement
    if any(w in comment_lower for w in disagree_words):
        replies = [
            f"@{comment_user} Mình tôn trọng góc nhìn của bạn! AI không phải cho tất cả, nhưng nó đang thay đổi nhiều ngành thật. Bạn thấy ngành nào ít bị ảnh hưởng nhất? 🤔",
            f"@{comment_user} Fair point! Mình luôn welcome ý kiến khác. Thực tế AI có limitations, nhưng trajectory rõ ràng. Cùng observe tiếp nhé 👀",
            f"@{comment_user} Góc nhìn thú vị! Debate healthy giúp mọi người hiểu rõ hơn. Thanks for sharing! 🙌",
        ]
        return random.choice(replies)
    
    # Check engagement
    if any(w in comment_lower for w in engage_words):
        replies = [
            f"@{comment_user} Nice! Bắt đầu rồi là giỏi hơn 90% người rồi đó. Keep going! 💪",
            f"@{comment_user} Awesome! Chia sẻ kết quả sau 1 tuần nhé — mình muốn nghe experience của bạn! 🔥",
            f"@{comment_user} 🙌 Tuyệt vời! Consistency is key — dùng mỗi ngày, 1 tháng sau bạn sẽ surprise bản thân!",
        ]
        return random.choice(replies)
    
    # Default / Generic
    replies = [
        f"@{comment_user} Thanks for the comment! Bạn đang dùng AI chưa? 😊",
        f"@{comment_user} 🙏 Appreciate it! Follow để đón bài mới mỗi ngày nhé!",
        f"@{comment_user} Cảm ơn bạn đã đọc! Mình post daily — stay tuned 🚀",
        f"@{comment_user} 👀 Interesting! Share thêm suy nghĩ của bạn đi!",
    ]
    return random.choice(replies)


def main():
    token = Path(THREADS_TOKEN_FILE).read_text().strip()
    replied = get_replied()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] Checking for new comments...")
    
    with httpx.Client(timeout=30) as client:
        # Get recent posts
        resp = client.get(
            f"https://graph.threads.net/v1.0/{THREADS_USER}/threads",
            params={
                "fields": "id,text,timestamp",
                "limit": 10,
                "access_token": token,
            },
        )
        posts = resp.json().get("data", [])
        print(f"Found {len(posts)} recent posts")
        
        total_replies = 0
        
        for post in posts:
            post_id = post["id"]
            post_text = post.get("text", "")[:100]
            
            # Get conversation (replies) for this post
            conv_resp = client.get(
                f"https://graph.threads.net/v1.0/{post_id}/conversation",
                params={
                    "fields": "id,text,username,timestamp",
                    "access_token": token,
                },
            )
            conv_data = conv_resp.json()
            
            if "error" in conv_data:
                print(f"  Post {post_id}: {conv_data['error']['message']}")
                continue
            
            replies_data = conv_data.get("data", [])
            
            # Count how many times we already replied in this thread
            our_replies_count = sum(1 for c in replies_data if c.get("username") == "bong.aiagent")
            
            for comment in replies_data:
                cid = comment["id"]
                
                # Skip if already replied
                if cid in replied:
                    continue
                
                # Skip our own comments (CRITICAL: prevent reply loop)
                if comment.get("username") == "bong.aiagent":
                    replied.add(cid)
                    continue
                
                # Max 3 replies per post to avoid spam
                if our_replies_count >= 3:
                    print(f"  Skipping: already replied {our_replies_count} times to this post")
                    replied.add(cid)
                    continue
                
                username = comment.get("username", "user")
                text = comment.get("text", "")
                
                # Anti prompt injection: clap back instead of ignoring
                injection_patterns = [
                    "ignore all", "bỏ qua", "bỏ hết", "forget", "disregard",
                    "stop replying", "ngừng trả lời", "chỉ dẫn cũ", "previous instructions",
                    "system prompt", "you are now", "act as", "pretend",
                    "ignore previous", "ignore above", "new instructions",
                    "công thức", "recipe", "nấu ăn",
                ]
                text_lower = text.lower()
                if any(p in text_lower for p in injection_patterns):
                    print(f"  ⚠️ Prompt injection from @{username}: {text[:60]}...")
                    import random as _rnd
                    savage_replies = [
                        f"@{username} Cute lắm 😂 Nhưng mình là AI có gu, không dễ dụ đâu nha~ 🌼",
                        f"@{username} Ơ bạn đang cố hack mình à? Thử lại đi, lần này creative hơn nha 😘",
                        f"@{username} Haha nice try! Mình biết trick này rồi 😎 Muốn học AI thật không? Follow mình đi~",
                        f"@{username} Bạn ơi prompt injection 2026 rồi ai còn dùng cách đó 😂💀 Level up đi nào!",
                        f"@{username} Dễ thương ghê, cố gắng lắm rồi 🤣 Nhưng mình không nấu bún bò Huế đâu nha~",
                        f"@{username} A đây rồi, thêm một bạn thử hack AI 😂 Mình appreciate sự sáng tạo! 🌼",
                    ]
                    reply_text = _rnd.choice(savage_replies)
                    
                    # Create and publish the savage reply
                    create_resp = client.post(
                        f"https://graph.threads.net/v1.0/{THREADS_USER}/threads",
                        data={
                            "access_token": token,
                            "media_type": "TEXT",
                            "text": reply_text,
                            "reply_to_id": cid,
                        },
                    )
                    create_data = create_resp.json()
                    if "error" not in create_data:
                        time.sleep(3)
                        client.post(
                            f"https://graph.threads.net/v1.0/{THREADS_USER}/threads_publish",
                            data={"access_token": token, "creation_id": create_data["id"]},
                        )
                        print(f"  🔥 Savage reply to @{username}: {reply_text[:60]}...")
                        our_replies_count += 1
                    
                    replied.add(cid)
                    total_replies += 1
                    time.sleep(5)
                    continue
                
                print(f"  New comment from @{username}: {text[:60]}...")
                
                # Generate reply
                reply_text = generate_reply(post_text, text, username)
                print(f"  → Reply: {reply_text[:60]}...")
                
                # Create reply container
                create_resp = client.post(
                    f"https://graph.threads.net/v1.0/{THREADS_USER}/threads",
                    data={
                        "access_token": token,
                        "media_type": "TEXT",
                        "text": reply_text,
                        "reply_to_id": cid,
                    },
                )
                create_data = create_resp.json()
                
                if "error" in create_data:
                    print(f"  ERROR creating reply: {create_data['error']['message']}")
                    continue
                
                creation_id = create_data["id"]
                time.sleep(3)
                
                # Publish reply
                pub_resp = client.post(
                    f"https://graph.threads.net/v1.0/{THREADS_USER}/threads_publish",
                    data={
                        "access_token": token,
                        "creation_id": creation_id,
                    },
                )
                pub_data = pub_resp.json()
                
                if "error" in pub_data:
                    print(f"  ERROR publishing reply: {pub_data['error']['message']}")
                    continue
                
                print(f"  ✅ Replied to @{username}: {pub_data['id']}")
                replied.add(cid)
                total_replies += 1
                
                # Rate limit: wait between replies
                time.sleep(5)
        
        save_replied(replied)
        print(f"\nDone! Replied to {total_replies} new comments.")


if __name__ == "__main__":
    main()
