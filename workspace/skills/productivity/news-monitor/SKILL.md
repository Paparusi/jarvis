---
name: news-monitor
description: >
  Monitor and summarize latest news on topics of interest — tech, AI,
  crypto, security. Search multiple sources and provide concise summaries.
version: 1.0.0
metadata:
  jarvis:
    category: productivity
    emoji: "📰"
    priority: 0.75
---

# News Monitor

## Khi nao kich hoat
Khi user yeu cau:
- Tin tuc moi nhat ve topic X
- "co gi moi ve AI/crypto/security?"
- News summary, trend analysis
- "cap nhat tin tuc hom nay"

## Workflow
1. Xac dinh topics can theo doi
2. Tim kiem tin tuc (dung `web_search` voi 2-3 queries khac nhau)
3. Fetch key articles (dung `fetch_url` cho top 3-5 bai)
4. Tong hop thanh summary co cau truc
5. Highlight key trends va insights

## Rules
- Luon ghi ro nguon va thoi gian
- Uu tien nguon uy tin (Reuters, Bloomberg, TechCrunch, Ars Technica)
- Phan biet tin tuc vs opinion
- Neu thong tin mau thuan giua cac nguon, neu ro
- Format ngan gon — bullet points, khong dai dong
