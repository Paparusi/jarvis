---
name: osint-investigator
description: >
  OSINT (Open Source Intelligence) investigation — gather publicly available
  information about targets (domains, IPs, people, companies). Combines
  web search, DNS lookup, and page extraction.
version: 1.0.0
metadata:
  jarvis:
    category: security
    emoji: "🕵️"
    priority: 0.85
    requires:
      bins: [dig, nmap]
    mcp_tools: []
---

# OSINT Investigator

## Khi nao kich hoat
Khi user yeu cau:
- Tim thong tin ve mot domain, IP, hoac to chuc
- Recon, reconnaissance, OSINT
- "ai so huu domain nay?", "tim thong tin ve cong ty X"
- Subdomain enumeration, email harvesting

## Workflow
1. Xac dinh target type: domain, IP, person, company
2. DNS Lookup: resolve A, MX, NS, TXT records (dung `dns_lookup`)
3. WHOIS: tim thong tin dang ky domain (dung `http_request` voi WHOIS API)
4. Web search: tim thong tin cong khai (dung `web_search`)
5. Fetch key pages: homepage, about, robots.txt (dung `fetch_url`)
6. Port scan: kiem tra services dang chay (dung `port_scan`)
7. Tong hop ket qua thanh report co cau truc

## Rules
- CHI thu thap thong tin CONG KHAI (publicly available)
- KHONG brute force, KHONG truy cap trai phep
- Luon ghi ro nguon thong tin
- Neu target la ca nhan, ton trong privacy — chi thong tin cong khai
- Bao cao ket qua theo do uu tien: Critical > High > Medium > Low

## Output format
```
## OSINT Report: [target]

### DNS Records
- A: ...
- MX: ...
- NS: ...

### WHOIS
- Registrar: ...
- Created: ...

### Open Ports
- 80/tcp: HTTP
- 443/tcp: HTTPS

### Web Presence
- Homepage: ...
- Technologies detected: ...

### Public Information
- ...
```
