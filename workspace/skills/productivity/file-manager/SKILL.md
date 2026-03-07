---
name: file-manager
description: "Quản lý file: đọc, ghi, tạo, liệt kê files trong workspace. Kích hoạt khi user thao tác với files."
version: 1.0.0
metadata:
  jarvis:
    emoji: "📁"
    category: productivity
    priority: 0.5
    success_rate: 1.0
    usage_count: 0
---

# File Manager

Skill quản lý file trong workspace. Đọc, ghi, tạo mới và liệt kê files.

## Khi nào kích hoạt

- Đọc file: "đọc file...", "xem nội dung...", "mở file..."
- Ghi file: "tạo file...", "ghi vào...", "lưu thành file..."
- Liệt kê: "list files", "xem thư mục", "có gì trong..."
- Tìm kiếm file: "tìm file...", "file nào chứa..."

## Workflow

1. **Xác định thao tác**: Đọc, ghi, hay liệt kê?
2. **Xác nhận path**: Đảm bảo path nằm trong workspace/ hoặc /tmp/
3. **Thực hiện**: Dùng tools read_file, write_file, hoặc list_directory
4. **Hiển thị kết quả**: Format output phù hợp

## Quy tắc

- **Giới hạn access**: Chỉ truy cập workspace/ và /tmp/, không truy cập file hệ thống
- **Không ghi đè**: Cảnh báo user trước khi ghi đè file đã tồn tại
- **Bảo mật**: Không đọc/ghi file .env, credentials, secrets
- **Format**: Khi hiển thị nội dung file, dùng code block với syntax highlighting phù hợp
