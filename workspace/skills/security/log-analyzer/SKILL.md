---
name: log-analyzer
description: >
  Analyze system logs, web server logs, and application logs to detect
  anomalies, security incidents, and performance issues. Uses file reading
  and Python analysis.
version: 1.0.0
metadata:
  jarvis:
    category: security
    emoji: "📊"
    priority: 0.80
---

# Log Analyzer

## Khi nao kich hoat
Khi user yeu cau:
- Phan tich logs (syslog, auth.log, access.log, error.log)
- Tim anomaly, pha hien tan cong
- "co gi bat thuong trong log khong?"
- Failed login detection, brute force detection

## Workflow
1. Doc log file (dung `read_file`)
2. Parse log format (syslog, Apache/Nginx, JSON)
3. Phan tich bang Python (dung `run_python`):
   - Dem failed logins, group by IP
   - Detect unusual patterns (time, frequency, source)
   - Tim error spikes
   - Identify suspicious IPs
4. Cross-reference suspicious IPs (dung `web_search` cho threat intel)
5. Tong hop findings

## Rules
- Xu ly log files lon bang streaming/chunked reading
- Luon bao mat thong tin nhat ky — khong log ra passwords
- Focus vao patterns bat thuong, khong liet ke moi dong
- Neu phat hien incident nghiem trong, khuyen user hanh dong ngay
- Thong ke ro rang: counts, percentages, timelines

## Output format
```
## Log Analysis Report
File: [path]
Period: [start] — [end]
Total entries: [N]

### Anomalies Detected

#### Brute Force Attempt
- Source: 192.168.1.100
- Failed logins: 47 in 5 minutes
- Accounts targeted: root, admin, user
- Action: Block IP immediately

#### Error Spike
- Time: 14:30-14:45 UTC
- Error rate: 500% above baseline
- Error type: 502 Bad Gateway
- Likely cause: Backend service down

### Statistics
- Unique IPs: X
- Failed logins: X
- 4xx errors: X
- 5xx errors: X
```
