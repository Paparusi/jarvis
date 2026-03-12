"""Facebook Messenger Chatbot — Tư vấn việc làm 24/7.

Trả lời tự nhiên như người thật, không phải menu bot.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import FastAPI, Request, Response

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("messenger_bot")

TZ_BANGKOK = timezone(timedelta(hours=7))

app = FastAPI(title="Nhân Lực 24h - Messenger Bot")

VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "nhanluc24h_verify_2026")
PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
APP_SECRET = os.environ.get("FB_APP_SECRET", "")
PAGE_ID = "110786530340557"

# Job database
JOBS = [
    {
        "id": "tti_cuchi",
        "title": "Công nhân sản xuất TTI",
        "company": "TTI Việt Nam",
        "location": "Củ Chi, TP.HCM",
        "salary_range": "12-18 triệu/tháng",
        "salary_base": "5.560.000đ",
        "allowances": "Chuyên cần 400K, đi lại 250K",
        "overtime": "40K-72K/giờ tùy ngày",
        "skill_bonus": "400K - 1.2 triệu/tháng",
        "onboarding_bonus": "4.3 triệu",
        "tet_bonus": "2 triệu (2 đợt)",
        "type": "Lắp ráp thiết bị điện không dây (máy mài, khoan, cắt, chà nhám, thổi lá, hút bụi)",
        "environment": "Chuyền đứng, máy lạnh mát mẻ",
        "benefits": "Ăn miễn phí, BHXH/BHYT/BHTN, lương tháng 13, quà lễ Tết, 1 ngày phép/tháng, ứng lương hàng tháng",
        "requirements": "CCCD gốc hoặc VNeID định danh mức 2",
        "interview": "Thứ 2 - Thứ 7 hàng tuần",
        "shifts": "Ca ngày 7:00-7:30, ca đêm 19:00-19:30. Ca 8/10/12 tiếng, xoay ca 1-2 tuần",
        "contact": "0363.639.128",
        "referral_bonus": "300K cho người giới thiệu",
    },
]

# Conversation history per user
user_history: dict[str, list[dict]] = {}


def get_history(sender_id: str) -> list[dict]:
    if sender_id not in user_history:
        user_history[sender_id] = []
    return user_history[sender_id]


def add_to_history(sender_id: str, role: str, text: str):
    history = get_history(sender_id)
    history.append({"role": role, "text": text, "time": time.time()})
    # Keep last 20 messages
    if len(history) > 20:
        user_history[sender_id] = history[-20:]


def generate_reply(sender_id: str, message_text: str) -> str:
    """Generate natural human-like reply."""
    text = message_text.lower().strip()
    history = get_history(sender_id)
    is_first = len(history) == 0
    
    add_to_history(sender_id, "user", message_text)
    
    # ── First message / Greeting ──
    greetings = ["hi", "hello", "xin chào", "chào", "hey", "alo", "cho hỏi", 
                 "cho em hỏi", "ad ơi", "admin", "anh ơi", "chị ơi", "ơi"]
    if is_first or any(text == g or text.startswith(g + " ") for g in greetings):
        reply = (
            "Chào bạn! 😊 Mình là tư vấn viên của Nhân Lực 24h.\n\n"
            "Bạn đang tìm việc làm phải không? Hiện tại bên mình đang tuyển "
            "công nhân sản xuất tại TTI Củ Chi, lương 12-18 triệu/tháng, "
            "làm việc trong môi trường máy lạnh luôn nè.\n\n"
            "Bạn quan tâm không? Mình tư vấn chi tiết cho nha! 😊"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Salary questions ──
    salary_kw = ["lương", "thu nhập", "bao nhiêu tiền", "salary", "income", "kiếm được", "trả bao nhiêu"]
    if any(k in text for k in salary_kw):
        reply = (
            "Lương ở TTI khá ổn nè bạn! 💰\n\n"
            "Tổng thu nhập khoảng 12-18 triệu/tháng, tùy ca và tăng ca.\n\n"
            "Cụ thể thì:\n"
            "- Lương cơ bản: 5.560K\n"
            "- Phụ cấp chuyên cần + đi lại: 650K\n"
            "- Tăng ca thường: 40K/giờ, CN: 53K/giờ, đêm CN: 72K/giờ\n"
            "- Thưởng tay nghề: 400K - 1.2 triệu\n\n"
            "Ngoài ra còn thưởng hội nhập 4.3 triệu cho người mới nữa! "
            "Bạn muốn biết thêm gì không? 😊"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Location ──
    location_kw = ["ở đâu", "địa chỉ", "chỗ nào", "khu vực", "xa không", "đường nào"]
    if any(k in text for k in location_kw):
        reply = (
            "Nhà máy TTI ở Củ Chi, TP.HCM bạn nhé! 📍\n\n"
            "Có xe đưa đón, nếu bạn ở xa thì cũng có KTX hỗ trợ luôn. "
            "Bạn đang ở khu vực nào? Mình xem có tiện đi làm không nha 😊"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Benefits ──
    benefit_kw = ["quyền lợi", "chế độ", "bhxh", "bảo hiểm", "ăn uống", "ktx", "ký túc", 
                  "phúc lợi", "được gì", "có gì hay"]
    if any(k in text for k in benefit_kw):
        reply = (
            "Chế độ ở TTI tốt lắm nè! ✨\n\n"
            "- Ăn miễn phí tại nhà máy\n"
            "- BHXH, BHYT, BHTN đầy đủ\n"
            "- Lương tháng 13\n"
            "- Quà lễ, Tết\n"
            "- Mỗi tháng được 1 ngày phép\n"
            "- Được ứng lương hàng tháng\n"
            "- Người mới còn được ứng lương hàng tuần tháng đầu luôn!\n\n"
            "Quan trọng là làm việc trong phòng máy lạnh mát mẻ, "
            "không nóng bức gì đâu 😄 Bạn muốn đi phỏng vấn không?"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Interview / How to apply ──
    interview_kw = ["phỏng vấn", "interview", "hồ sơ", "cần gì", "giấy tờ", "đi làm", 
                    "đăng ký", "ứng tuyển", "apply", "xin việc", "muốn làm", "thử"]
    if any(k in text for k in interview_kw):
        reply = (
            "Đơn giản lắm bạn! 😊\n\n"
            "Bạn chỉ cần mang theo CCCD gốc hoặc VNeID định danh mức 2 là được.\n\n"
            "Lịch phỏng vấn từ Thứ 2 đến Thứ 7 hàng tuần, "
            "bạn chọn ngày nào tiện thì đi nha.\n\n"
            "Gọi cho mình qua số 0363.639.128 để mình hẹn lịch "
            "và hướng dẫn đường đi chi tiết nhé! 📞"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Job type / What to do ──
    job_kw = ["làm gì", "công việc", "việc gì", "job", "vị trí", "mô tả"]
    if any(k in text for k in job_kw):
        reply = (
            "Công việc là lắp ráp thiết bị điện không dây bạn nhé — "
            "như máy mài, máy khoan, máy cắt, chà nhám...\n\n"
            "Làm trên chuyền đứng (chuyền tay), không cần kinh nghiệm, "
            "vào sẽ được đào tạo. Môi trường máy lạnh mát mẻ luôn! ❄️\n\n"
            "Ca làm linh hoạt 8/10/12 tiếng, xoay ca 1-2 tuần. "
            "Bạn thích ca ngày hay ca đêm? 😊"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Shift / Working hours ──
    shift_kw = ["ca", "giờ làm", "thời gian", "mấy giờ", "ca ngày", "ca đêm", "tăng ca"]
    if any(k in text for k in shift_kw):
        reply = (
            "Giờ làm việc như này nè bạn:\n\n"
            "- Ca ngày: vào ca 7:00 - 7:30 sáng\n"
            "- Ca đêm: vào ca 19:00 - 19:30\n"
            "- Ca 8, 10 hoặc 12 tiếng tùy đơn hàng\n"
            "- Xoay ca 1-2 tuần\n\n"
            "Tăng ca tự nguyện nha, không ép buộc. "
            "Mà tăng ca lương cũng ngon lắm — "
            "CN đêm được 72K/giờ luôn! 💪"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Yes / Interested ──
    yes_kw = ["có", "muốn", "quan tâm", "ok", "được", "ừ", "oke", "oki", "yes", "đi", "cho em"]
    if any(text == k or text.startswith(k + " ") for k in yes_kw) or text in ["có", "ừ", "ok", "đi"]:
        reply = (
            "Tốt quá! 🎉\n\n"
            "Bạn gọi cho mình qua số 0363.639.128 nhé, "
            "mình sẽ hẹn lịch phỏng vấn và hướng dẫn đường đi chi tiết.\n\n"
            "Nhớ mang theo CCCD gốc hoặc VNeID mức 2 là OK! "
            "Chúc bạn phỏng vấn thành công nha! 💪😊"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Thanks ──
    thanks_kw = ["cảm ơn", "thanks", "thank", "tks", "cám ơn"]
    if any(k in text for k in thanks_kw):
        reply = (
            "Không có gì bạn! 😊 Nếu có gì thắc mắc cứ nhắn mình nhé.\n"
            "Chúc bạn sớm tìm được việc ưng ý! 🍀"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Referral ──
    referral_kw = ["giới thiệu", "rủ bạn", "thưởng giới thiệu"]
    if any(k in text for k in referral_kw):
        reply = (
            "À có chương trình thưởng giới thiệu nè! 🎁\n\n"
            "Bạn giới thiệu người mới vào làm sẽ được thưởng 300K/người. "
            "Rủ bạn bè cùng đi phỏng vấn luôn cho vui! 😄\n\n"
            "Liên hệ 0363.639.128 để biết thêm chi tiết nha."
        )
        add_to_history(sender_id, "bot", reply)
        return reply

    # ── Age / Requirements ──
    age_kw = ["tuổi", "bao nhiêu tuổi", "yêu cầu", "điều kiện", "bằng cấp", "kinh nghiệm"]
    if any(k in text for k in age_kw):
        reply = (
            "Yêu cầu đơn giản lắm bạn:\n\n"
            "- Từ 18 tuổi trở lên\n"
            "- Không cần kinh nghiệm, vào sẽ được đào tạo\n"
            "- Không yêu cầu bằng cấp\n"
            "- Chỉ cần có CCCD hoặc VNeID mức 2\n\n"
            "Ai cũng làm được hết á! 😊 Bạn muốn đi phỏng vấn không?"
        )
        add_to_history(sender_id, "bot", reply)
        return reply
    
    # ── Default — conversational fallback ──
    reply = (
        "Dạ mình nhận được tin nhắn của bạn rồi! 😊\n\n"
        "Hiện tại bên mình đang tuyển công nhân sản xuất tại TTI Củ Chi, "
        "lương 12-18 triệu/tháng, môi trường máy lạnh.\n\n"
        "Bạn muốn mình tư vấn về:\n"
        "- Lương và thu nhập?\n"
        "- Quyền lợi, chế độ?\n"
        "- Cách đăng ký phỏng vấn?\n\n"
        "Cứ hỏi mình nhé! Hoặc gọi luôn 0363.639.128 cho nhanh 📞"
    )
    add_to_history(sender_id, "bot", reply)
    return reply


async def send_message(recipient_id: str, text: str):
    """Send a message via Messenger API."""
    url = "https://graph.facebook.com/v21.0/me/messages"
    
    async with httpx.AsyncClient() as client:
        # Typing indicator
        await client.post(url, json={
            "recipient": {"id": recipient_id},
            "sender_action": "typing_on",
        }, params={"access_token": PAGE_ACCESS_TOKEN})
        
        # Natural delay (longer text = longer typing)
        import asyncio
        delay = min(len(text) * 0.01, 2.0)  # Max 2 seconds
        await asyncio.sleep(max(delay, 0.8))
        
        # Send message
        resp = await client.post(url, json={
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }, params={"access_token": PAGE_ACCESS_TOKEN})
        
        if resp.status_code != 200:
            log.error(f"Send failed: {resp.text}")
        else:
            log.info(f"Replied to {recipient_id}: {text[:60]}...")


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        log.info("Webhook verified!")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)


@app.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()
    log.info(f"Webhook: {json.dumps(body, ensure_ascii=False)[:300]}")
    
    if body.get("object") != "page":
        return {"status": "ok"}
    
    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id", "")
            
            # Skip page's own messages
            if sender_id == PAGE_ID:
                continue
            
            message = event.get("message", {})
            if message and not message.get("is_echo"):
                text = message.get("text", "")
                if text:
                    reply = generate_reply(sender_id, text)
                    await send_message(sender_id, reply)
            
            postback = event.get("postback", {})
            if postback:
                payload = postback.get("payload", "")
                reply = generate_reply(sender_id, payload)
                await send_message(sender_id, reply)
    
    return {"status": "ok"}


@app.get("/")
async def root():
    return {
        "name": "Nhân Lực 24h - Messenger Bot",
        "status": "running",
        "active_users": len(user_history),
        "time": datetime.now(TZ_BANGKOK).isoformat(),
    }


# ── Static files for images ──
from fastapi.staticfiles import StaticFiles
from pathlib import Path

static_dir = Path(__file__).parent.parent.parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
