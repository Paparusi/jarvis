#!/usr/bin/env python3
"""Auto-post to Threads — Viral AI/Tech content with Unsplash images.

Research-backed content library with hook-first writing.
Run via cron every 3 hours.
"""

import json
import random
import time
import httpx
from pathlib import Path
from datetime import datetime
from threads_content_v2 import POSTS_V2

THREADS_TOKEN_FILE = "/tmp/threads_token.txt"
THREADS_USER = "26305136249098236"
DATA_DIR = Path(__file__).parent.parent / "data"
POSTED_FILE = DATA_DIR / "threads_posted.json"

# High-quality Unsplash images
IMAGES = {
    "ai": [
        "https://images.unsplash.com/photo-1677442136019-21780ecad995?w=1080&q=80",
        "https://images.unsplash.com/photo-1684369176170-463e84248b70?w=1080&q=80",
        "https://images.unsplash.com/photo-1620712943543-bcc4688e7485?w=1080&q=80",
        "https://images.unsplash.com/photo-1555255707-c07966088b7b?w=1080&q=80",
        "https://images.unsplash.com/photo-1531746790095-6c5a2b55842e?w=1080&q=80",
        "https://images.unsplash.com/photo-1694891437157-fb3e4cab1ed7?w=1080&q=80",
    ],
    "tech": [
        "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1080&q=80",
        "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=1080&q=80",
        "https://images.unsplash.com/photo-1488590528505-98d2b5aba04b?w=1080&q=80",
        "https://images.unsplash.com/photo-1461749280684-dccba630e2f6?w=1080&q=80",
        "https://images.unsplash.com/photo-1504639725590-34d0984388bd?w=1080&q=80",
    ],
    "money": [
        "https://images.unsplash.com/photo-1553729459-afe8f2e2ed65?w=1080&q=80",
        "https://images.unsplash.com/photo-1579621970563-ebec7560ff3e?w=1080&q=80",
        "https://images.unsplash.com/photo-1633158829585-23ba8f7c8caf?w=1080&q=80",
    ],
    "robot": [
        "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=1080&q=80",
        "https://images.unsplash.com/photo-1546776310-eef45dd6d63c?w=1080&q=80",
        "https://images.unsplash.com/photo-1535378917042-10a22c95931a?w=1080&q=80",
    ],
    "future": [
        "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1080&q=80",
        "https://images.unsplash.com/photo-1506318137071-a8e063b4bec0?w=1080&q=80",
        "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?w=1080&q=80",
    ],
    "work": [
        "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?w=1080&q=80",
        "https://images.unsplash.com/photo-1553877522-43269d4ea984?w=1080&q=80",
        "https://images.unsplash.com/photo-1559136555-9303baea8ebd?w=1080&q=80",
    ],
}

# ── VIRAL CONTENT LIBRARY ──
# Rules: Strong hook first line, short paragraphs, end with CTA/question
POSTS = [
    # ── PERSONAL STORY / TRANSFORMATION ──
    {
        "cat": "ai",
        "text": """Tháng trước tôi suýt bị đuổi việc.

Sếp giao 200 trang tài liệu, deadline 2 ngày.

Bình thường? Chết.

Nhưng tôi mở Claude, paste từng phần vào.
3 tiếng sau — xong. Tóm tắt, phân tích, đề xuất.

Sếp đọc xong gọi lên: "Đây là bản report tốt nhất tháng."

Tôi không thông minh hơn ai.
Tôi chỉ biết dùng đúng công cụ vào đúng lúc.

AI không cứu bạn.
Nhưng biết dùng AI đúng cách thì có. 💡"""
    },
    {
        "cat": "money",
        "text": """Từ 0 → 15 triệu/tháng.

Không phải crypto. Không phải dropship.

Tôi dùng AI viết content cho 3 fanpage.
Mỗi ngày tốn 1 tiếng.
Thu nhập: 5 triệu/page.

Quy trình:
→ Claude viết 5 bài/ngày (15 phút)
→ Midjourney tạo ảnh (15 phút)
→ Lên lịch đăng (10 phút)
→ Trả lời comment (20 phút)

Phần khó nhất? Bắt đầu.
Phần dễ nhất? Mọi thứ sau đó.

Bạn có 1 tiếng mỗi ngày không? ⏰"""
    },
    {
        "cat": "work",
        "text": """"Em dùng AI à? Thế thì skill em ở đâu?"

Câu này tôi nghe tuần nào cũng có.

Trả lời:
Skill của tôi là biết HỎI đúng.
Là biết CHECK output.
Là biết GHÉP các mảnh thành bức tranh hoàn chỉnh.

Người thợ giỏi không chê búa máy.
Họ dùng búa máy để xây nhà đẹp hơn.

AI là búa máy.
Bạn là kiến trúc sư.

Đừng tự hào vì đóng đinh bằng tay. 🔨"""
    },

    # ── SHOCK / CONTROVERSY ──
    {
        "cat": "future",
        "text": """Xóa ChatGPT đi.

Nghiêm túc.

Không phải vì nó dở.
Mà vì bạn đang dùng SAI.

90% người dùng ChatGPT như Google — hỏi 1 câu, nhận 1 câu.

Trong khi AI có thể:
→ Phân tích cả cuốn sách trong 2 phút
→ Viết business plan hoàn chỉnh
→ Debug code nhanh hơn senior dev
→ Tạo khóa học từ expertise của bạn

Bạn đang dùng Ferrari để đi chợ.

Học cách lái đã. Rồi hãy phàn nàn nó chậm. 🏎️"""
    },
    {
        "cat": "robot",
        "text": """Apple vừa sa thải 600 nhân viên QA.

Thay bằng gì? AI testing.

Google cắt 30% đội ngũ dịch thuật. Lý do? AI dịch tốt hơn.

Đây không phải tin đồn. Đây là báo cáo Q4.

Nhưng —

Cùng quý đó, LinkedIn báo cáo:
→ 4.7 triệu việc MỚI liên quan AI
→ Lương trung bình: $120K/năm
→ 85% không yêu cầu bằng CS

AI không xóa việc làm.
AI xóa CÁCH làm việc cũ.

Bạn đang update bản thân hay đang chờ bị thay thế? ⚡"""
    },
    {
        "cat": "ai",
        "text": """Điều không ai nói với bạn về AI:

Nó ngu.

Nghiêm túc. AI ngu hơn bạn nghĩ.

Nó hallucinate — bịa ra số liệu tự tin như thật.
Nó bias — thiên vị theo data training.
Nó không hiểu — chỉ predict token tiếp theo.

VẬY SAO VẪN DÙNG?

Vì nó ngu nhưng NHANH.
Ngu nhưng KHÔNG MỆT.
Ngu nhưng làm 24/7.

Giống như máy tính bỏ túi.
Nó không "hiểu" toán.
Nhưng bạn vẫn dùng thay vì nhẩm.

Dùng AI đúng = hiểu giới hạn của nó.
Đó mới là kỹ năng thật sự. 🧠"""
    },

    # ── LISTICLE / TIPS ──
    {
        "cat": "tech",
        "text": """7 website AI miễn phí mà trường học không dạy:

1. Napkin.ai — Biến text thành infographic tự động
2. Suno.ai — Tạo nhạc bằng AI. Bất kỳ thể loại nào.
3. HeyGen — Clone khuôn mặt + giọng nói. Quay video không cần camera.
4. Ideogram — Tạo ảnh có CHỮ đẹp (Midjourney làm không được)
5. v0.dev — Mô tả UI, nó code React cho bạn
6. Notebook LM — Upload tài liệu, nó tạo podcast 2 người thảo luận
7. Bolt.new — Mô tả app, nó build full-stack trong 30 giây

Tất cả MIỄN PHÍ.

Bookmark ngay. Cảm ơn sau. 🔖"""
    },
    {
        "cat": "tech",
        "text": """Copy prompt này. Nghiêm túc.

"Tôi muốn [mục tiêu]. Bạn là chuyên gia [lĩnh vực] với 15 năm kinh nghiệm.

Hãy:
1. Phân tích tình huống
2. Đưa ra 3 phương án (ưu/nhược điểm)
3. Recommend phương án tốt nhất
4. Cho action plan chi tiết từng bước

Format: bullet points, ngắn gọn, có deadline."

Prompt này biến ChatGPT từ chatbot thành consultant $500/giờ.

Tôi dùng nó cho mọi thứ — từ marketing plan đến quyết định career.

Thử đi. Khác biệt ngay lần đầu. 📋"""
    },
    {
        "cat": "money",
        "text": """3 side hustle với AI đang hot nhất 2025:

𝟏. AI Content Agency (10-30tr/tháng)
→ Viết blog, social media cho SME
→ Tool: Claude + Canva + Buffer
→ Cần: 0đ vốn, 2-3 giờ/ngày

𝟐. AI Chatbot cho doanh nghiệp (5-20tr/project)
→ Tạo chatbot tư vấn cho shop, clinic, restaurant
→ Tool: Chatbase, Botpress (free tier)
→ Cần: biết copy-paste, không cần code

𝟑. AI Course Creator (20-50tr/tháng)
→ Tạo khóa học online bằng AI
→ Tool: ChatGPT viết script, HeyGen quay video
→ Bán trên Udemy, Kyna, Edumall

Cái nào dễ nhất? Số 1.
Cái nào lời nhất? Số 3.
Cái nào nên bắt đầu? CÁI NÀO CŨNG ĐƯỢC.

Chỉ cần bắt đầu. Hôm nay. 🚀"""
    },

    # ── ENGAGEMENT BAIT / QUESTION ──
    {
        "cat": "work",
        "text": """Phỏng vấn năm 2025 vs 2020:

2020: "Bạn biết dùng Excel không?"
2025: "Bạn biết dùng AI không?"

2020: "Bạn có 5 năm kinh nghiệm?"
2025: "Bạn có portfolio AI project không?"

2020: "Lương kỳ vọng bao nhiêu?"
2025: "Bạn có thể thay thế bao nhiêu người bằng AI?"

Nghe tàn nhẫn? Đó là thực tế.

Tin tốt: Bạn vẫn còn thời gian.
Tin xấu: Không nhiều đâu.

Bắt đầu học AI ngay hôm nay.
Hoặc giải thích cho nhà tuyển dụng tại sao bạn không biết. 🎯"""
    },
    {
        "cat": "ai",
        "text": """Thí nghiệm: Tôi để AI chạy business 1 tuần.

Ngày 1: Claude viết 20 bài social media → lên lịch
Ngày 2: AI trả lời 47 email khách hàng → 0 complaint
Ngày 3: Midjourney thiết kế 15 ảnh quảng cáo
Ngày 4: AI phân tích data → tìm ra 3 insight mới
Ngày 5: Chatbot xử lý 200+ tin nhắn khách hàng

Kết quả sau 1 tuần:
→ Revenue: +22%
→ Thời gian tôi bỏ ra: 3 tiếng/ngày (thay vì 10)
→ Customer satisfaction: giữ nguyên

Bài học lớn nhất?

AI không hoàn hảo. Nhưng nó đủ tốt.
Và "đủ tốt" + tốc độ = THẮNG. ⚡"""
    },
    {
        "cat": "future",
        "text": """2025: "AI chỉ là trend thôi, rồi sẽ qua."

Giống hệt:
2007: "iPhone chỉ là trend, ai bỏ bàn phím?"
1995: "Internet chỉ là trend, ai cần email?"
1990: "PC chỉ là trend, giấy bút tốt hơn."

Mỗi thế hệ đều có người NÓI và người LÀM.

Người nói: "Chờ xem đã."
Người làm: Đã kiếm được tiền.

10 năm nữa nhìn lại, bạn sẽ là người nào?

Comment "LÀM" nếu bạn đã bắt đầu 👇"""
    },
    {
        "cat": "robot",
        "text": """Sam Altman (CEO OpenAI) vừa nói 1 câu lạnh sống lưng:

"AI agents sẽ tham gia lực lượng lao động năm 2025."

Không phải hỗ trợ. THAM GIA. Như một nhân viên.

Nghĩa là:
→ AI sẽ có "vai trò" cụ thể trong công ty
→ AI sẽ được giao KPI
→ AI sẽ làm việc với team người thật
→ AI sẽ report trực tiếp cho manager

Nghe sci-fi? 
Google, Microsoft, Salesforce đã triển khai.

Đồng nghiệp mới của bạn có thể không phải người.

Ready? 🤖"""
    },
    {
        "cat": "ai",
        "text": """Stop dùng ChatGPT cho mọi thứ.

Mỗi AI có thế mạnh riêng:

📝 Viết content dài? → Claude (vượt trội)
🔍 Research có nguồn? → Perplexity (không bịa)
💻 Code? → Cursor + Claude (combo chết người)
🎨 Design? → Midjourney (ảnh) + Ideogram (có chữ)
📊 Data analysis? → ChatGPT Code Interpreter
🎵 Âm nhạc? → Suno (tạo nhạc từ text)
🎬 Video? → HeyGen (clone mặt) + Runway (edit)

Dùng đúng tool = kết quả x10.
Dùng sai tool = mất thời gian rồi chê "AI dở".

Save lại. Dùng dần. 📌"""
    },
    {
        "cat": "money",
        "text": """Freelancer Việt Nam đang kiếm $2000-5000/tháng nhờ AI.

Và phần lớn không biết code.

Họ làm gì?
→ Nhận brief từ khách Mỹ/EU trên Upwork
→ Dùng AI làm 80% công việc
→ Review, tinh chỉnh 20%
→ Giao đúng hạn, chất lượng cao

Khách không hỏi "bạn dùng AI không?"
Khách hỏi "bao giờ xong?"

Năng lực mới không phải "làm giỏi".
Năng lực mới là "giao giỏi".

Upwork + AI = máy in tiền.
Nhưng phải bắt đầu mới biết. 💰"""
    },

    # ── EDUCATIONAL / DEEP INSIGHT ──
    {
        "cat": "tech",
        "text": """Bí mật về AI mà big tech không muốn bạn biết:

AI KHÔNG hiểu gì cả.

Nó không "suy nghĩ".
Nó không "sáng tạo".
Nó predict token tiếp theo dựa trên statistics.

Giống autocomplete trên điện thoại.
Nhưng version cực kỳ phức tạp.

Tại sao điều này QUAN TRỌNG?

Vì khi hiểu cách AI hoạt động:
→ Bạn biết khi nào TIN nó
→ Bạn biết khi nào CHECK lại
→ Bạn biết PROMPT sao cho hiệu quả

Người dùng AI giỏi nhất không phải fan boy.
Họ là skeptic biết tận dụng. 🎯"""
    },
    {
        "cat": "ai",
        "text": """Quy tắc 80/20 của AI:

80% giá trị đến từ 20% tính năng.

Bạn KHÔNG cần:
❌ Học prompt engineering nâng cao
❌ Fine-tune model
❌ Hiểu transformer architecture
❌ Theo dõi mọi AI mới ra

Bạn CHỈ cần:
✅ 1 AI chat tool (Claude hoặc ChatGPT)
✅ Biết mô tả rõ ràng cái bạn muốn
✅ Biết check output thay vì tin mù
✅ Dùng nó HÀNG NGÀY cho công việc thật

Đơn giản vậy thôi.
Phức tạp hóa = lý do để không bắt đầu.

Start simple. Start today. ✨"""
    },
    {
        "cat": "future",
        "text": """Dự đoán 2026 (đánh dấu bài này):

1. AI agent sẽ thay thế 50% công việc customer service
2. Ít nhất 1 bộ phim Hollywood sẽ dùng 100% AI actors
3. Coding bootcamp sẽ đóng cửa hàng loạt
4. AI-generated music sẽ lên Billboard Top 100
5. Xuất hiện triệu phú đầu tiên kiếm tiền 100% từ AI content

Điên? Có thể.
Nhưng ai nghĩ ChatGPT sẽ có 300 triệu user?

Remind bài này sau 12 tháng.
Xem đúng bao nhiêu. 📅

Follow để theo dõi kết quả nhé 👀"""
    },
    {
        "cat": "work",
        "text": """LinkedIn profile của bạn đang CHẾT.

Và AI có thể cứu nó trong 15 phút.

Bước 1: Paste job description mơ ước vào Claude
Bước 2: Paste profile hiện tại
Bước 3: "Viết lại profile để match 90% JD này"

Kết quả:
→ Headline thu hút recruiter
→ About section kể câu chuyện
→ Experience bullet points có con số
→ Skills section khớp với industry keywords

Trước: 0 tin nhắn recruiter/tháng
Sau: 5-10 tin nhắn/tháng

15 phút thay đổi cả career.
Thử ngay tối nay. 💼"""
    },
] + POSTS_V2  # Extend with V2 content


def get_posted() -> list:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if POSTED_FILE.exists():
        return json.loads(POSTED_FILE.read_text())
    return []


def save_posted(posted: list):
    POSTED_FILE.write_text(json.dumps(posted[-100:]))


def pick_post(posted: list) -> tuple:
    """Pick a post that hasn't been used recently."""
    recent = set(posted[-(len(POSTS) - 2):]) if len(posted) >= len(POSTS) - 2 else set(posted)
    available = [i for i in range(len(POSTS)) if i not in recent]
    if not available:
        available = list(range(len(POSTS)))
    idx = random.choice(available)
    return idx, POSTS[idx]


def main():
    token = Path(THREADS_TOKEN_FILE).read_text().strip()
    posted = get_posted()

    idx, post = pick_post(posted)
    cat = post["cat"]
    text = post["text"].strip()
    image_url = random.choice(IMAGES.get(cat, IMAGES["ai"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] Posting #{idx} (cat={cat})")
    print(f"Image: {image_url}")
    print(f"Hook: {text.split(chr(10))[0]}")

    with httpx.Client(timeout=30) as client:
        # Step 1: Create container
        resp = client.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER}/threads",
            data={
                "access_token": token,
                "media_type": "IMAGE",
                "image_url": image_url,
                "text": text,
            },
        )
        data = resp.json()

        if "error" in data:
            print(f"ERROR creating: {data['error']['message']}")
            return False

        creation_id = data["id"]
        print(f"Container: {creation_id}")

        # Step 2: Wait for image processing
        time.sleep(5)

        # Step 3: Publish
        resp = client.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER}/threads_publish",
            data={
                "access_token": token,
                "creation_id": creation_id,
            },
        )
        pub_data = resp.json()

        if "error" in pub_data:
            print(f"ERROR publishing: {pub_data['error']['message']}")
            return False

        print(f"✅ Published: {pub_data['id']}")

        posted.append(idx)
        save_posted(posted)
        return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
