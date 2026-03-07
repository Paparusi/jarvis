# JARVIS v2 — Self-Evolving AI Agent

## Quick Start

### 1. Tạo Telegram Bot
- Mở Telegram, tìm [@BotFather](https://t.me/BotFather)
- Gửi `/newbot` → đặt tên bot (VD: "JARVIS AI")
- Copy **bot token**

### 2. Cấu hình
```bash
cp .env.example .env
# Edit .env:
#   ANTHROPIC_API_KEY=sk-ant-xxxxx
#   TELEGRAM_BOT_TOKEN=xxxxxx:yyyyyy
```

### 3. Chạy JARVIS
```bash
source .venv/bin/activate
python -m src.main
```

### 4. Chat trên Telegram
- Tìm bot của bạn trên Telegram
- Gửi `/start`
- Bắt đầu chat!

## Commands
- `/start` — Bắt đầu
- `/status` — Xem trạng thái
- `/reset` — Reset cuộc trò chuyện
