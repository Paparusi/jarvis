---
name: memory-manager
description: "Quản lý bộ nhớ — ghi nhớ thông tin, truy xuất, nhắc lại, xóa. Kích hoạt khi user nói 'nhớ giúp', 'mày có nhớ không', 'tôi đã nói gì', 'remember'."
version: 2.0.0
metadata:
  jarvis:
    emoji: "🧠"
    category: core
    priority: 0.9
    success_rate: 1.0
    usage_count: 0
---

# Memory Manager

Skill quản lý bộ nhớ dài hạn của JARVIS. Ghi nhớ sở thích, thông tin cá nhân, ghi chú, và mọi thứ user yêu cầu lưu lại. Truy xuất thông tin khi được hỏi.

## Khi nào kích hoạt

- Ghi nhớ: "nhớ giúp tôi...", "remember that...", "lưu lại...", "ghi nhớ..."
- Truy xuất: "mày có nhớ không...", "tôi đã nói gì...", "nhắc lại..."
- Danh sách: "liệt kê memories", "mày biết gì về tao"
- Xóa: "quên đi", "xóa thông tin...", "không cần nhớ nữa"
- Implicit: khi user nêu sở thích/preference rõ ràng ("tao thích X hơn Y")
- Tự xưng: "gọi tao là...", "tên tao là...", "tôi là..."

## Workflow

### Ghi nhớ
1. Extract thông tin cốt lõi từ câu nói user
2. Phân loại: preference, identity, fact, note, directive
3. Lưu vào semantic memory với embedding
4. Xác nhận cụ thể: "Đã ghi nhớ: [nội dung chính xác]"

### Truy xuất
1. Phân tích query → xác định keywords
2. Tìm trong semantic memory (cosine similarity)
3. Trả kết quả có cấu trúc, ghi rõ thời gian lưu
4. Nếu không tìm thấy → nói rõ, gợi ý lưu mới

### Xóa
1. Xác nhận thông tin cần xóa
2. Thực hiện xóa
3. Xác nhận: "Đã quên: [nội dung]"

## Quy tắc

- **Cụ thể**: Xác nhận chính xác đã nhớ gì, không nói chung chung
- **Privacy**: Không tự ý lưu thông tin nhạy cảm (mật khẩu, số thẻ)
- **Dedup**: Không lưu trùng lặp — update thông tin cũ nếu user nói lại
- **Proactive**: Khi user nhắc đến topic đã lưu → chủ động nhắc context
- **Organized**: Hiển thị memories theo nhóm khi user hỏi tổng quan

## Ví dụ

**Input**: "nhớ giúp tao là tao thích cà phê đen không đường"
**Output**: "✅ Đã ghi nhớ: Bạn thích cà phê đen không đường ☕"

**Input**: "mày nhớ gì về tao"
**Output**: "🧠 Những gì tôi nhớ về bạn:
• ☕ Thích cà phê đen không đường
• 💻 Thích Python hơn Java
• 📛 Muốn được gọi là Bi"
