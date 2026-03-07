---
name: forensics
description: "Phân tích pháp y số — trích xuất metadata file, phát hiện steganography, trích xuất IOC (Indicators of Compromise), và phân tích log hệ thống."
version: "1.0.0"
author: "JARVIS CTO"
tags: [security, forensics, dfir, malware, incident-response, stego]
allowed-tools: [file_metadata, stego_detect, ioc_extract, log_analyze]
metadata:
  jarvis:
    emoji: "🔬"
    category: security
    priority: 0.80
    success_rate: 1.0
    usage_count: 0
---

# Digital Forensics Analysis

Phân tích pháp y số toàn diện: trích xuất và phân tích metadata file, phát hiện dữ liệu ẩn qua steganography, trích xuất Indicators of Compromise (IOC) từ samples, và phân tích log hệ thống để tìm dấu vết tấn công.

## Khi nào kích hoạt

- Metadata: "xem metadata file này", "trích xuất thông tin ẩn trong file"
- Steganography: "có dữ liệu ẩn trong ảnh này không?", "detect stego"
- IOC: "trích xuất IOC từ malware sample", "tìm indicators of compromise"
- Log analysis: "phân tích log này tìm dấu vết tấn công", "xem log có gì bất thường"
- Incident response: "điều tra sự cố bảo mật", "forensics phân tích"
- CTF forensics: "giải challenge forensics", "tìm flag trong file này"

## Workflow

### File Metadata Analysis
1. **Trích xuất metadata** (`file_metadata`):
   - File type identification (magic bytes)
   - EXIF data (ảnh): GPS, camera, timestamps, software
   - PDF metadata: author, creator, modification dates
   - Office documents: author, revision history, macros
   - Embedded objects và hidden streams
   - Timestamps: created, modified, accessed (timeline analysis)

### Steganography Detection
2. **Phát hiện stego** (`stego_detect`):
   - LSB (Least Significant Bit) analysis
   - Statistical analysis: chi-square, entropy
   - Known stego tools detection: steghide, OpenStego, F5
   - Image comparison (nếu có original)
   - Audio steganography (spectogram analysis)
   - File appended data (check sau EOF marker)

### IOC Extraction
3. **Trích xuất IOC** (`ioc_extract`):
   - IP addresses (C2 servers)
   - Domain names và URLs
   - File hashes (MD5, SHA-1, SHA-256)
   - Email addresses
   - Registry keys (Windows)
   - Mutex names
   - YARA rule matching
   - String extraction từ binaries

### Log Analysis
4. **Phân tích log** (`log_analyze`):
   - Parse nhiều format: syslog, Apache, Nginx, auth.log, Windows Event Log
   - Detect brute force patterns
   - Identify privilege escalation attempts
   - Find anomalous behavior: unusual times, IPs, commands
   - Timeline reconstruction
   - Correlation across multiple log sources

## Quy tắc

- Bảo toàn chain of custody: KHÔNG modify evidence gốc
- Document mọi bước phân tích cho reproducibility
- Sử dụng hashes để verify integrity của evidence
- Phân tích trong môi trường isolated (sandbox)
- Report findings theo timeline chronological
- Phân loại IOC theo confidence level: Confirmed, Likely, Possible
- Tuân thủ quy trình DFIR tiêu chuẩn

## Output format

```
## Forensics Report: [case/file]
Date: [timestamp]
Analyst: JARVIS

### Evidence Summary
- File: suspicious.pdf
- SHA-256: abc123...
- Size: 1.2 MB
- Type: PDF document

### Metadata Analysis
- Author: admin (modified)
- Creator: Microsoft Word 2019
- Created: 2024-01-15 03:22:14 UTC
- Modified: 2024-01-16 14:05:33 UTC
- [SUSPICIOUS] Creation time is outside business hours

### IOC Extracted
| Type       | Value                | Confidence |
|-----------|---------------------|------------|
| IP        | 185.220.101.42      | Confirmed  |
| Domain    | c2.malware.evil     | Confirmed  |
| Hash (MD5)| d41d8cd98f00b204... | Confirmed  |
| URL       | http://evil.com/dl  | Likely     |

### Log Analysis (auth.log)
- [ALERT] 847 failed SSH attempts from 185.220.101.42 (03:00-03:15)
- [ALERT] Successful login from same IP at 03:16
- [ALERT] sudo commands executed: wget, chmod, crontab

### Timeline
1. 03:00 — Brute force SSH starts
2. 03:16 — Successful authentication
3. 03:17 — Malware downloaded
4. 03:18 — Persistence established via crontab

### Conclusions
- Attack vector: SSH brute force
- Impact: Full system compromise
- Remediation: Isolate host, rotate credentials, block C2 IPs
```

## Ví dụ truy vấn

- "Phân tích metadata file ảnh này, có GPS không?"
- "Kiểm tra xem ảnh PNG này có ẩn dữ liệu stego không"
- "Trích xuất IOC từ malware sample này"
- "Phân tích auth.log tìm dấu vết brute force"
- "Điều tra sự cố: server bị compromise, xem log"
- "Giải challenge forensics CTF: tìm flag trong file"
