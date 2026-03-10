---
name: xauusd-hien-tai
description: >
  Tự động phát hiện từ 5 interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến xauusd hien tai.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: 0.00
    usage_count: 5
    priority: 0.6
    mcp_tools: ["web_search", "fetch_url", "mt5_price"]
---

# Xauusd Hien Tai

## Khi nào kích hoạt
Khi user hỏi về xauusd hien tai hoặc các chủ đề liên quan.
Skill này được tự động tạo từ 5 interactions thành công.

## Tools
Sử dụng: web_search, fetch_url, mt5_price, market_session, mt5_analyze

## Ví dụ trigger
- "Tìm cho tôi giá XAUUSD hiện tại"
- "còn giá XAU hiện tại"
- "Phân tích XAUUSDm hiện tại đi"

## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user
