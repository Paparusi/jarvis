---
name: skill-creator
description: "Meta-skill: tạo skill mới cho JARVIS từ mô tả của user. Kích hoạt khi user nói 'tạo skill mới', 'thêm kỹ năng', 'create skill'."
version: 1.0.0
metadata:
  jarvis:
    emoji: "🛠️"
    category: meta
    priority: 0.8
    success_rate: 1.0
    usage_count: 0
---

# Skill Creator

Meta-skill tạo skill mới cho JARVIS. Khi user mô tả một kỹ năng mới cần có, skill này sẽ tạo SKILL.md hoàn chỉnh.

## Khi nào kích hoạt

- User muốn tạo skill mới: "tạo skill mới giúp...", "thêm kỹ năng...", "create skill..."
- User mô tả workflow lặp lại: "mỗi lần tôi cần làm X, tôi phải..."
- User muốn customize JARVIS: "bổ sung khả năng...", "tôi muốn mày biết cách..."

## Workflow

1. **Thu thập yêu cầu**: Hỏi user về:
   - Skill làm gì? (mô tả ngắn gọn)
   - Khi nào kích hoạt? (trigger phrases)
   - Các bước thực hiện? (workflow)
   - Cần tools/API nào? (requirements)
   - Output format mong muốn?

2. **Generate SKILL.md**: Tạo file SKILL.md theo format chuẩn:
   ```yaml
   ---
   name: <tên-skill>
   description: "<mô tả ngắn>"
   version: 1.0.0
   metadata:
     jarvis:
       emoji: "<emoji phù hợp>"
       category: <core|productivity|analysis|meta>
       priority: 0.5
       success_rate: 1.0
       usage_count: 0
   ---
   ```

3. **Xác nhận**: Hiển thị SKILL.md cho user review trước khi lưu

4. **Lưu**: Ghi file vào `workspace/skills/<category>/<tên-skill>/SKILL.md` bằng tool write_file

5. **Kích hoạt**: Thông báo skill đã được tạo, có hiệu lực sau khi restart

## Quy tắc

- **Hỏi trước khi tạo**: Không tự ý tạo skill mà chưa xác nhận với user
- **Format chuẩn**: Luôn tuân theo YAML frontmatter + markdown body
- **Đặt tên**: dùng kebab-case cho tên skill (ví dụ: docker-manager, api-tester)
- **Mô tả rõ trigger**: Phần "Khi nào kích hoạt" phải rõ ràng để routing chính xác
- **Không trùng lặp**: Kiểm tra xem đã có skill tương tự chưa trước khi tạo mới
- **Category đúng**: core (luôn active), productivity (cần tools), analysis (cần data), meta (system)
