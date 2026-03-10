---
name: plan-trade-alert
description: >
  Tự động phát hiện từ 3 interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến plan trade alert.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: 0.00
    usage_count: 3
    priority: 0.6
    mcp_tools: ["trade_plan", "trade_config", "trade_control"]
---

# Plan Trade Alert

## Khi nào kích hoạt
Khi user hỏi về plan trade alert hoặc các chủ đề liên quan.
Skill này được tự động tạo từ 3 interactions thành công.

## Tools
Sử dụng: trade_plan, trade_config, trade_control, trade_status

## Ví dụ trigger
- "Có chứ setup trade plan và đặt alert theo dõi bám sát plan"
- "alert khi tới phiên london và phân tích lại toàn bộ để có hướng trade tốt hơn"
- "lệnh 0.02 đã SL từ lâu rồi update và lên plan"

## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user
