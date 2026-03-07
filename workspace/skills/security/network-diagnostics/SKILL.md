---
name: network-diagnostics
description: >
  Network troubleshooting and diagnostics — ping, traceroute, DNS resolution,
  port connectivity checks. Diagnose connectivity issues systematically.
version: 1.0.0
metadata:
  jarvis:
    category: security
    emoji: "🌐"
    priority: 0.85
---

# Network Diagnostics

## Khi nao kich hoat
Khi user yeu cau:
- Kiem tra ket noi mang
- Ping, traceroute, DNS check
- "tai sao khong truy cap duoc website X?"
- Network troubleshooting, latency check

## Workflow
1. Ping target (dung `ping`) — check basic connectivity
2. DNS lookup (dung `dns_lookup`) — check name resolution
3. Traceroute (dung `traceroute`) — identify routing issues
4. Port check (dung `port_scan` voi connect mode) — verify service availability
5. HTTP check (dung `http_request` HEAD) — verify web service response
6. Tong hop va diagnosis

## Rules
- Bat dau tu don gian (ping) roi phuc tap dan (traceroute)
- Neu ping fail, check DNS truoc khi traceroute
- So sanh ket qua voi baseline neu co
- Giai thich ket qua bang ngon ngu de hieu
- Goi y giai phap cu the cho tung van de

## Output format
```
## Network Diagnostics: [target]

### Connectivity
- Ping: OK (avg 15ms) / FAIL
- DNS: Resolves to [IP] / FAIL

### Route
- Hops: 12
- Bottleneck: Hop 7 (100ms+ latency)

### Services
- HTTP (80): OK (200, 150ms)
- HTTPS (443): OK (200, 180ms)

### Diagnosis
[Clear explanation of the issue and suggested fix]
```
