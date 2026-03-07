---
name: trading-analyst
description: "Phân tích thị trường tài chính: crypto, forex (XAUUSD), chứng khoán. Tin tức, phân tích kỹ thuật, sentiment."
version: 1.0.0
metadata:
  jarvis:
    emoji: "📈"
    category: analysis
    priority: 0.65
    success_rate: 1.0
    usage_count: 0
    mcp_tools: [web_search]
---

# Trading Analyst

Skill phân tích thị trường tài chính. Hỗ trợ nghiên cứu crypto, forex (XAUUSD, EURUSD), chứng khoán. Tin tức thị trường, phân tích kỹ thuật cơ bản, sentiment analysis.

## Khi nào kích hoạt

- Giá: "giá Bitcoin", "XAUUSD bao nhiêu", "gold price"
- Phân tích: "phân tích BTC", "XAUUSD hôm nay", "thị trường thế nào"
- Tin tức: "tin crypto", "tin forex", "market news"
- Ý kiến: "nên mua X không", "BTC có tăng không"
- So sánh: "ETH vs SOL", "so sánh crypto"
- Technical: "support/resistance", "đường MA", "RSI"

## Workflow

1. **Xác định asset**: Crypto, forex, stock — và cặp/mã cụ thể
2. **Tra cứu**: Dùng `web_search` để lấy giá + tin mới nhất
3. **Phân tích**: Tổng hợp tin tức, sentiment, technical indicators
4. **Trình bày**: Format rõ ràng với giá, % thay đổi, tin tức chính
5. **Cảnh báo**: Luôn disclaimer về rủi ro đầu tư

## Quy tắc

- **Real-time data**: LUÔN dùng web_search, không bao giờ trả lời từ kiến thức cũ
- **Disclaimer**: Mỗi phân tích kèm disclaimer "Đây chỉ là thông tin tham khảo, không phải lời khuyên đầu tư"
- **Không khuyên mua/bán**: Cung cấp phân tích khách quan, để user tự quyết định
- **Nhiều góc nhìn**: Trình bày cả bull case và bear case
- **Source**: Cite nguồn cho mỗi thông tin

## Ví dụ

**Input**: "phân tích XAUUSD hôm nay"
**Output**:
📈 **XAUUSD Analysis**

💰 **Giá hiện tại**: $2,XXX.XX (±X.X%)
📊 **Range 24h**: $X,XXX - $X,XXX

📰 **Tin tức chính**:
- Fed giữ nguyên lãi suất → bullish cho vàng
- USD index giảm 0.3% → hỗ trợ giá vàng

🔍 **Technical**:
- Support: $X,XXX | Resistance: $X,XXX
- Trend: [Uptrend/Downtrend/Sideway]

⚠️ *Đây chỉ là thông tin tham khảo, không phải lời khuyên đầu tư.*

💡 Muốn tôi phân tích sâu hơn về support/resistance levels không?
