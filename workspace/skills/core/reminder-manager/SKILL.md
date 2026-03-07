---
name: reminder-manager
description: "Đặt nhắc nhở, quản lý lịch hẹn, timer. Kích hoạt khi user nói 'nhắc tao', 'remind me', 'đặt hẹn', 'sau X phút'."
version: 1.0.0
metadata:
  jarvis:
    emoji: "⏰"
    category: core
    priority: 0.85
    success_rate: 1.0
    usage_count: 0
---

# Reminder Manager

Skill quản lý nhắc nhở và hẹn giờ. Hỗ trợ đặt reminder bằng ngôn ngữ tự nhiên (tiếng Việt + English), xem danh sách, hủy reminder.

## Khi nào kích hoạt

- Đặt nhắc: "nhắc tao sau 30 phút...", "remind me in 2 hours..."
- Hẹn giờ: "lúc 3 giờ chiều...", "at 15:00...", "at 3pm..."
- Xem danh sách: "có nhắc nhở nào không", "list reminders"
- Hủy: "hủy nhắc nhở", "cancel reminder"
- Implicit: "đừng quên...", "tao cần nhớ lúc..."

## Cú pháp hỗ trợ

```
/remind sau 30 phút uống nước
/remind sau 2 tiếng họp team
/remind lúc 15:00 gọi khách hàng
/remind in 1 hour check email
/remind at 3pm meeting
/reminders — xem danh sách
```

## Thời gian hỗ trợ

| Cú pháp | Ví dụ |
|---------|-------|
| `sau X phút` | sau 30 phút |
| `sau X tiếng/giờ` | sau 2 tiếng |
| `sau X ngày` | sau 1 ngày |
| `sau X tuần` | sau 1 tuần |
| `lúc HH:MM` | lúc 15:30 |
| `in X minutes` | in 30 minutes |
| `in X hours` | in 2 hours |
| `at HH:MM` | at 15:30 |
| `at Xpm/am` | at 3pm |

## Workflow

1. **Parse thời gian**: Trích xuất thời gian từ câu nói user
2. **Extract mô tả**: Phần còn lại là nội dung nhắc nhở
3. **Xác nhận**: Hiển thị thời gian + nội dung để user verify
4. **Lưu**: Schedule job vào scheduler
5. **Thông báo**: Khi đến giờ → push notification qua Telegram

## Quy tắc

- **Xác nhận rõ**: Luôn hiển thị thời gian chính xác đã schedule
- **Timezone**: Hiện tại dùng UTC, note rõ cho user
- **Parse error**: Nếu không hiểu thời gian → gợi ý format đúng
- **Multiple**: User có thể đặt nhiều reminders cùng lúc
