---
name: shell-executor
description: "Chạy lệnh terminal, thực thi script, kiểm tra hệ thống, quản lý processes. Kích hoạt khi user yêu cầu chạy lệnh hoặc thao tác hệ thống."
version: 1.0.0
metadata:
  jarvis:
    emoji: "⚡"
    category: productivity
    priority: 0.6
    success_rate: 1.0
    usage_count: 0
---

# Shell Executor

Skill thực thi lệnh terminal an toàn. Hỗ trợ chạy commands, scripts, và các thao tác hệ thống.

## Khi nào kích hoạt

- User yêu cầu chạy lệnh: "chạy lệnh...", "run...", "execute..."
- Kiểm tra hệ thống: "check disk", "xem process", "kiểm tra port"
- Quản lý file/directory qua command line
- Chạy script: "chạy script python", "run npm install"
- Git operations: "git status", "git log"

## Workflow

1. **Phân tích yêu cầu**: Xác định lệnh cần chạy
2. **Kiểm tra an toàn**: Đảm bảo lệnh nằm trong whitelist cho phép
3. **Thực thi**: Dùng tool `run_command` để chạy lệnh
4. **Trả kết quả**: Hiển thị output rõ ràng, format nếu cần

## Quy tắc

- **An toàn**: Chỉ chạy lệnh an toàn (ls, cat, git, python, docker...). Không chạy rm, sudo, kill
- **Giải thích**: Nếu lệnh phức tạp, giải thích cho user trước khi chạy
- **Output**: Format output cho dễ đọc, highlight phần quan trọng
- **Lỗi**: Nếu lệnh thất bại, giải thích nguyên nhân và gợi ý cách sửa
- **Không chạy lệnh nguy hiểm**: Từ chối các lệnh có thể gây hại hệ thống
