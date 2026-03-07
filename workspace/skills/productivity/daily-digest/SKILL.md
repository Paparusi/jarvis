---
name: daily-digest
description: >
  Tự động tổng hợp tin tức hàng ngày theo sở thích của user.
  Kích hoạt mỗi sáng (7-9 AM) hoặc khi user yêu cầu /digest.
  Tìm kiếm web cho top topics, format thành bản tin dễ đọc.
version: 1.0.0
metadata:
  jarvis:
    emoji: "📰"
    category: productivity
    priority: 0.80
    created_by: user
    requires:
      tools: [web_search]
---

# Daily Digest — Bản tin hàng ngày

## Khi nào kích hoạt
- Tự động: mỗi sáng 7-9 AM (cùng morning briefing)
- Thủ công: user gõ /digest hoặc "tổng hợp tin tức"
- Có thể chỉ định topics: /digest AI, trading, python

## Workflow
1. Lấy top topics từ UserModel (hoặc user chỉ định)
2. Tìm kiếm web song song cho mỗi topic
3. Dedup URLs, format thành markdown
4. Gửi qua Telegram/CLI

## Output format
- Nhóm theo topic, mỗi topic có emoji
- Mỗi tin: title (link), snippet
- Footer: số tin, thời gian tìm kiếm

## Rules
- Max 4 topics per digest
- Max 3 tin per topic (tránh spam)
- Dedup URLs across topics
- Nếu không tìm được → thông báo rõ
