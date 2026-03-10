---
name: dang-model-nao
description: >
  Tự động phát hiện từ 4 interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến dang model nao.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: 0.84
    usage_count: 4
    priority: 0.6
    mcp_tools: []
---

# Dang Model Nao

## Khi nào kích hoạt
Khi user hỏi về dang model nao hoặc các chủ đề liên quan.
Skill này được tự động tạo từ 4 interactions thành công.

## Ví dụ trigger
- "ê giờ mày đang chạy model nào vậy?"
- "bạn đang dùng model nào"
- "m đang dùng model nào vậy"

## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user
