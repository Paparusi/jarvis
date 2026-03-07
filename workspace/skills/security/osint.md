---
name: osint
description: "Thu thập thông tin tình báo nguồn mở (OSINT) — tìm kiếm thông tin công khai về domain, tổ chức, cá nhân từ nhiều nguồn: Google dork, username search, email harvest, Wayback Machine, GitHub leaks."
version: "1.0.0"
author: "JARVIS CTO"
tags: [security, osint, recon, intelligence, information-gathering]
allowed-tools: [google_dork, username_search, email_harvest, wayback_lookup, github_leaks]
metadata:
  jarvis:
    emoji: "🕵️"
    category: security
    priority: 0.85
    success_rate: 1.0
    usage_count: 0
---

# OSINT Intelligence Gathering

Thu thập thông tin tình báo nguồn mở (Open Source Intelligence) từ nhiều nguồn công khai. Kết hợp Google dorking, username enumeration, email harvesting, Wayback Machine, và GitHub leak scanning để xây dựng hồ sơ toàn diện về target.

## Khi nào kích hoạt

- Thu thập OSINT: "thu thập thông tin về domain này", "OSINT target này"
- Google dork: "dork tìm file nhạy cảm", "tìm thông tin lộ trên Google"
- Username search: "tìm username này trên các platform", "check xem account này ở đâu"
- Email harvest: "tìm email liên quan đến domain này"
- Wayback: "xem phiên bản cũ của website này", "wayback lookup"
- GitHub leaks: "tìm secrets bị lộ trên GitHub", "scan GitHub leaks"
- Recon tổng quát: "recon mục tiêu này", "thu thập thông tin tình báo"

## Workflow

1. **Xác định target**: Domain, tổ chức, cá nhân, hoặc username
2. **Google Dorking** (`google_dork`): Tìm thông tin nhạy cảm bị lộ
   - `site:target.com filetype:pdf|doc|xls`
   - `site:target.com inurl:admin|login|config`
   - `"target.com" password|secret|credential`
3. **Username Search** (`username_search`): Tìm username trên các platform
   - Social media, forums, code repos, gaming sites
   - Cross-reference kết quả để xác nhận identity
4. **Email Harvest** (`email_harvest`): Thu thập email liên quan
   - Từ website, social media, WHOIS records
   - Verify email format patterns (first.last@domain.com)
5. **Wayback Lookup** (`wayback_lookup`): Kiểm tra lịch sử website
   - Tìm trang đã bị xóa, thông tin cũ
   - So sánh versions để phát hiện thay đổi
6. **GitHub Leaks** (`github_leaks`): Scan mã nguồn công khai
   - API keys, credentials, private configs
   - Commit history chứa thông tin nhạy cảm
7. **Tổng hợp**: Xây dựng báo cáo OSINT có cấu trúc

## Quy tắc

- CHỈ thu thập thông tin CÔNG KHAI (publicly available)
- KHÔNG truy cập trái phép, KHÔNG brute force
- Luôn ghi rõ nguồn của mỗi thông tin
- Nếu target là cá nhân, tôn trọng quyền riêng tư
- Phân loại thông tin theo mức độ quan trọng: Critical > High > Medium > Low
- Tuân thủ pháp luật và đạo đức nghề nghiệp

## Output format

```
## OSINT Report: [target]
Date: [timestamp]

### Google Dorking Results
- Sensitive files found: ...
- Exposed pages: ...

### Username Presence
| Platform     | URL              | Status   |
|-------------|------------------|----------|
| GitHub      | github.com/user  | Found    |
| Twitter     | twitter.com/user | Not found|

### Email Addresses
- admin@target.com (verified)
- info@target.com (unverified)

### Wayback Machine
- Snapshots: X total
- Notable changes: ...

### GitHub Leaks
- [CRITICAL] API key found in commit abc123
- [HIGH] Database credentials in config.yml

### Summary
- Total findings: X
- Critical: X | High: X | Medium: X | Low: X
```

## Ví dụ truy vấn

- "Thu thập OSINT về domain example.com"
- "Tìm username 'johndoe' trên tất cả platform"
- "Dork tìm file nhạy cảm trên target.com"
- "Check xem GitHub có leak gì từ công ty ABC không"
- "Wayback xem phiên bản cũ của trang login"
