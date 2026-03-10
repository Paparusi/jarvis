---
name: lenh-dang-roi
description: >
  Tự động phát hiện từ 3 interactions tương tự.
  Kích hoạt khi user hỏi về chủ đề liên quan đến lenh dang roi.
version: 1.0.0
metadata:
  jarvis:
    category: auto-generated
    auto_generated: true
    created_by: dreamtime
    success_rate: 0.00
    usage_count: 3
    priority: 0.6
    mcp_tools: ["mt5_price", "mt5_candles", "mt5_signal"]
---

# Lenh Dang Roi

## Khi nào kích hoạt
Khi user hỏi về lenh dang roi hoặc các chủ đề liên quan.
Skill này được tự động tạo từ 3 interactions thành công.

## Tools
Sử dụng: mt5_price, mt5_candles, mt5_signal, technical_indicators, mt5_smc

## Ví dụ trigger
- "vậy đang trong zone rồi mà sao không mở lệnh"
- "phân tích sâu lại từ đầu, tìm điểm đặt lệnh mở lệnh"
- "thị trường đang hoạt động rồi mà, đang ở phiên Á, brigde không mở được lệnh hay "

## Workflow
1. Phân tích yêu cầu user
2. Thực hiện các bước cần thiết (sử dụng tools nếu có)
3. Tổng hợp kết quả rõ ràng

## Quy tắc
- Trả lời chính xác theo yêu cầu
- Sử dụng tools khi cần thiết
- Nếu không chắc chắn → hỏi lại user
