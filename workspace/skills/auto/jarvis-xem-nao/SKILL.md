---
name: jarvis-xem-nao
description: >
  Tự động phát hiện từ 4 interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến jarvis xem nao.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: 0.87
    usage_count: 4
    priority: 0.6
    mcp_tools: ["mt5_price", "market_session", "mt5_candles"]
---

# Jarvis Xem Nao

## Khi nào kích hoạt
Khi user hỏi về jarvis xem nao hoặc các chủ đề liên quan.
Skill này được tự động tạo từ 4 interactions thành công.

## Tools
Sử dụng: mt5_price, market_session, mt5_candles

## Ví dụ trigger
- "e jarvis"
- "ê jarvis"
- "jarvis"

## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user
