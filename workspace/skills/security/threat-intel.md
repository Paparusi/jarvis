---
name: threat-intel
description: "Tình báo mối đe dọa — tra cứu VirusTotal, kiểm tra AbuseIPDB, check malware hash, và tìm kiếm Shodan cho exposed services."
version: "1.0.0"
author: "JARVIS CTO"
tags: [security, threat-intelligence, virustotal, shodan, malware, ioc]
allowed-tools: [virustotal_lookup, abuseipdb_check, malware_hash_check, shodan_search]
metadata:
  jarvis:
    emoji: "🛡️"
    category: security
    priority: 0.85
    success_rate: 1.0
    usage_count: 0
---

# Threat Intelligence

Tình báo mối đe dọa: tra cứu file/URL/IP trên VirusTotal, kiểm tra IP độc hại trên AbuseIPDB, xác minh malware hash qua các threat feeds, và tìm kiếm Shodan để phát hiện services bị lộ trên internet.

## Khi nào kích hoạt

- VirusTotal: "check file này trên VT", "URL này có malware không?", "scan IP trên VirusTotal"
- AbuseIPDB: "IP này có bị report không?", "check IP độc hại", "abuse check"
- Malware hash: "hash này có phải malware không?", "check hash trên threat intel"
- Shodan: "tìm services lộ trên internet", "shodan search target", "xem server này expose gì"
- Tổng quát: "kiểm tra mối đe dọa", "threat intelligence", "IP/domain này có an toàn không?"

## Workflow

### VirusTotal Lookup
1. **Tra cứu** (`virustotal_lookup`):
   - File scan: upload hoặc search by hash (MD5/SHA-1/SHA-256)
   - URL scan: check reputation và detection results
   - IP lookup: associated domains, communications, detections
   - Domain lookup: DNS, subdomains, WHOIS, detection history
   - Phân tích: detection ratio, AV engine results, behavioral analysis

### AbuseIPDB Check
2. **Kiểm tra IP** (`abuseipdb_check`):
   - Abuse confidence score (0-100%)
   - Report history: số lượng, categories, timeline
   - ISP/ASN information
   - Country, usage type (hosting, residential, VPN)
   - Whitelist status

### Malware Hash Check
3. **Xác minh hash** (`malware_hash_check`):
   - Cross-reference với multiple threat feeds
   - Malware family identification
   - MITRE ATT&CK mapping
   - First/last seen dates
   - Associated campaigns và threat actors

### Shodan Search
4. **Tìm kiếm Shodan** (`shodan_search`):
   - Discover exposed services (ports, banners)
   - Technology fingerprinting
   - Vulnerability detection (CVEs)
   - SSL/TLS certificate analysis
   - Geographic distribution
   - Historical data comparison

## Quy tắc

- Sử dụng API keys hợp lệ (cấu hình trong .env)
- Rate limit theo API quota (VirusTotal: 4 req/min free tier)
- KHÔNG upload file nhạy cảm lên public services
- Cross-reference nhiều nguồn để tăng confidence
- Phân loại threats: Malicious, Suspicious, Clean, Unknown
- Cache kết quả để tránh duplicate lookups
- Bảo mật API keys, KHÔNG log vào plaintext

## Output format

```
## Threat Intelligence Report: [target]
Date: [timestamp]

### VirusTotal Results
- Target: 185.220.101.42
- Detection: 15/87 engines flagged as malicious
- Categories: Malware, C2, Botnet
- First seen: 2024-01-10
- Associated domains:
  - evil.com (malicious)
  - c2.bad.org (malicious)

### AbuseIPDB
- Confidence of Abuse: 92%
- Total Reports: 347 (last 30 days)
- Categories: SSH brute force, Web attack, Port scan
- ISP: Bulletproof Hosting Ltd
- Country: RU

### Malware Hash Analysis
- Hash: d41d8cd98f00b204e9800998ecf8427e
- Status: MALICIOUS
- Family: Emotet
- MITRE ATT&CK: T1566 (Phishing), T1059 (Command Execution)
- Threat Actor: TA542

### Shodan — Exposed Services
| Port  | Service    | Version         | Vulns   |
|-------|-----------|-----------------|---------|
| 22    | OpenSSH   | 7.4             | CVE-... |
| 80    | nginx     | 1.14.0          | 2 CVEs  |
| 3306  | MySQL     | 5.7.33          | exposed |
| 6379  | Redis     | 6.0.1           | no auth |

### Risk Assessment
- Overall: HIGH RISK
- Confidence: 95%
- Recommendation: Block IP, update firewall rules, investigate connections
```

## Ví dụ truy vấn

- "Check IP 185.220.101.42 trên VirusTotal và AbuseIPDB"
- "Hash này có phải malware không: d41d8cd98f..."
- "Shodan tìm services lộ của target.com"
- "URL này có an toàn không? Scan VirusTotal"
- "Kiểm tra domain suspicious.com có liên quan đến threat nào"
- "IP này bị report abuse bao nhiêu lần?"
