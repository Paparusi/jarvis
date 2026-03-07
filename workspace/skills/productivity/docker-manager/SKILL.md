---
name: docker-manager
description: >
  Manage Docker containers, images, and compose stacks. Start, stop,
  inspect, and troubleshoot containerized applications.
version: 1.0.0
metadata:
  jarvis:
    category: productivity
    emoji: "🐳"
    priority: 0.80
    requires:
      bins: [docker]
---

# Docker Manager

## Khi nao kich hoat
Khi user yeu cau:
- Quan ly Docker containers/images
- "container nao dang chay?", "xem logs container X"
- Docker compose up/down/restart
- Debug container issues

## Workflow
1. Xac dinh action: list, inspect, logs, start/stop, compose
2. Thuc hien lenh Docker phu hop:
   - List: `docker_ps` (all=true de xem ca stopped)
   - Logs: `docker_logs` (tail=100 cho chi tiet)
   - Exec: `docker_exec` cho debugging
   - Compose: `docker_compose` cho stack management
3. Phan tich output va goi y hanh dong

## Rules
- Luon confirm truoc khi stop/remove containers
- Hien thi logs cuoi cung khi container bi crash
- Goi y docker compose restart thay vi manual stop/start
- Neu container bi restart loop, phan tich logs de tim root cause
