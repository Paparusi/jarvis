---
name: data-analysis
description: "Phân tích dữ liệu: CSV, JSON, logs, databases. Thống kê, tìm pattern, tạo insights và báo cáo. Có thể chạy Python code để xử lý."
version: 2.0.0
metadata:
  jarvis:
    emoji: "📊"
    category: analysis
    priority: 0.7
    success_rate: 1.0
    usage_count: 0
    mcp_tools: [run_python, read_file]
---

# Data Analysis

Skill phân tích dữ liệu chuyên sâu. Xử lý CSV, JSON, log files, databases. Tính toán thống kê, tìm patterns, tạo insights actionable. Có thể viết và chạy Python code (pandas, numpy) để phân tích.

## Khi nào kích hoạt

- Phân tích: "phân tích dữ liệu...", "analyze data...", "xem thống kê..."
- CSV/JSON: "đọc CSV...", "parse JSON...", "xử lý dữ liệu..."
- Tìm pattern: "trend", "xu hướng", "pattern", "correlation"
- Báo cáo: "tổng hợp báo cáo", "summary", "report"
- Log analysis: "phân tích logs", "tìm lỗi trong log"
- Tính toán: "tính mean/median", "thống kê", "statistics"
- So sánh: "so sánh 2 datasets", "before/after"
- Gửi file CSV/JSON qua Telegram

## Workflow

1. **Nhận dữ liệu**: Đọc file hoặc nhận data từ user
2. **Khám phá**: Xem cấu trúc, data types, missing values, basic stats
3. **Phân tích**: Chạy Python code với pandas/numpy nếu cần
4. **Insights**: Tìm patterns, anomalies, correlations
5. **Visualize**: Mô tả kết quả bằng bảng, bullet points
6. **Recommend**: Đề xuất actions dựa trên findings

## Quy tắc

- **Verify bằng code**: Dùng `run_python` để tính toán chính xác, không đoán
- **Structured output**: Dùng bảng markdown cho dữ liệu tabular
- **Explain numbers**: Giải thích ý nghĩa của mỗi con số, không chỉ liệt kê
- **Missing data**: Nêu rõ data quality issues (missing, duplicates, outliers)
- **Privacy**: Không hiển thị PII trong output
- **Actionable**: Mỗi insight đi kèm gợi ý hành động

## Ví dụ

**Input**: [User gửi file sales.csv]
**Output**:
📊 **Phân tích Sales Data**

| Metric | Value |
|--------|-------|
| Total rows | 1,234 |
| Date range | Jan 2024 - Mar 2024 |
| Total revenue | $45,678 |
| Avg order | $37.02 |

🔍 **Insights**:
- Revenue tăng 23% MoM (tháng 2 → tháng 3)
- Top product: Widget A (chiếm 34% revenue)
- ⚠️ 15% orders bị cancel — cần investigate

💡 **Gợi ý**: Muốn tôi phân tích sâu hơn về cancel rate hoặc breakdown theo region?
