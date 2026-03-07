---
name: bang-may
description: >
  Tự động phát hiện từ 5 interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến bang may.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: 0.89
    usage_count: 5
    priority: 0.6
    mcp_tools: ["run_python"]
---

# Bằng Mấy

## Khi nào kích hoạt
Khi user hỏi về bang may hoặc các chủ đề liên quan.
Skill này được tự động tạo từ 5 interactions thành công.

## Tools
Sử dụng: run_python

## Ví dụ trigger
- "1+1 bang may?"
- "1+1 bang may?"
- "1+1 bang may?"

## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user


## Merged From: bang-may
- Original description: Tự động phát hiện từ 5 interactions tương tự. Kích hoạt khi user hỏi về chủ đề liên quan đến bang may.
- Merged on: 2026-03-06
