---
name: web-attack
description: "Kiểm tra lỗ hổng bảo mật web — directory bruteforce, SQL injection, XSS, CORS misconfiguration, WAF detection, LFI, và HTTP header audit."
version: "1.0.0"
author: "JARVIS CTO"
tags: [security, web, pentest, vulnerability, injection, xss, sqli]
allowed-tools: [dir_bruteforce, sqli_test, xss_scan, cors_check, waf_detect, lfi_test, header_audit]
metadata:
  jarvis:
    emoji: "🌐"
    category: security
    priority: 0.85
    success_rate: 1.0
    usage_count: 0
---

# Web Vulnerability Testing

Kiểm tra toàn diện lỗ hổng bảo mật web application. Bao gồm directory bruteforce, SQL injection testing, XSS scanning, CORS misconfiguration check, WAF detection, Local File Inclusion testing, và HTTP security header audit.

## Khi nào kích hoạt

- Kiểm tra web: "test bảo mật website này", "scan lỗ hổng web"
- Directory bruteforce: "brute directory", "tìm đường dẫn ẩn", "dir scan"
- SQL injection: "test SQLi", "kiểm tra SQL injection", "thử inject"
- XSS: "scan XSS", "kiểm tra cross-site scripting"
- CORS: "check CORS", "kiểm tra CORS policy"
- WAF: "detect WAF", "xem có WAF không", "bypass WAF"
- LFI: "test LFI", "kiểm tra local file inclusion"
- Header audit: "kiểm tra HTTP headers", "security headers"

## Workflow

1. **Reconnaissance**:
   - Xác định target URL, technology stack
   - WAF detection (`waf_detect`) trước để biết rào cản
2. **Header Audit** (`header_audit`):
   - Kiểm tra security headers: CSP, X-Frame-Options, HSTS, X-XSS-Protection
   - Phát hiện server version disclosure, cookie flags
3. **Directory Bruteforce** (`dir_bruteforce`):
   - Tìm đường dẫn ẩn: admin panels, backup files, config files
   - Wordlist phù hợp với technology stack
4. **CORS Check** (`cors_check`):
   - Test CORS misconfiguration
   - Kiểm tra wildcard origins, credential leaks
5. **SQL Injection** (`sqli_test`):
   - Test các entry points: parameters, headers, cookies
   - Boolean-based, time-based, error-based detection
6. **XSS Scan** (`xss_scan`):
   - Reflected XSS, Stored XSS, DOM-based XSS
   - Test với các payloads phổ biến và bypass filters
7. **LFI Test** (`lfi_test`):
   - Path traversal: ../../etc/passwd
   - PHP wrappers, null byte injection
   - Log poisoning vectors
8. **Tổng hợp báo cáo** theo severity

## Quy tắc

- CHỈ test khi có AUTHORIZATION từ chủ sở hữu target
- Luôn hỏi xác nhận trước khi chạy aggressive tests
- KHÔNG exploit thực sự — chỉ detect và report
- Phân loại theo severity: Critical, High, Medium, Low, Informational
- Gợi ý remediation cho mỗi finding
- Rate limit requests để không gây DoS
- Log tất cả requests cho audit trail

## Output format

```
## Web Security Assessment: [target]
Date: [timestamp]
WAF Detected: [Yes/No — type]

### Summary
- Critical: X | High: X | Medium: X | Low: X

### Findings

#### [CRITICAL] SQL Injection — /search?q=
- Type: Boolean-based blind
- Parameter: q
- Payload: ' OR 1=1 --
- Remediation: Sử dụng parameterized queries, input validation

#### [HIGH] Reflected XSS — /comment
- Type: Reflected
- Parameter: name
- Payload: <script>alert(1)</script>
- Remediation: Output encoding, Content-Security-Policy header

#### [MEDIUM] Missing Security Headers
- X-Frame-Options: MISSING
- Content-Security-Policy: MISSING
- Remediation: Thêm security headers vào web server config

#### [LOW] Directory Listing Enabled — /uploads/
- Remediation: Disable directory listing trong web server

### Hidden Paths Found
- /admin/ (403 Forbidden)
- /backup/ (200 OK) ⚠️
- /.env (200 OK) ⚠️

### Recommendations
1. Fix critical và high findings ngay
2. Implement WAF nếu chưa có
3. Regular security scanning schedule
```

## Ví dụ truy vấn

- "Kiểm tra bảo mật website example.com"
- "Scan SQL injection cho form login này"
- "Test XSS trên tất cả input fields"
- "Check CORS policy của API endpoint này"
- "Tìm đường dẫn ẩn trên server"
- "Website này có WAF không?"
- "Audit HTTP security headers"
