---
name: trading-analyst
description: "Phân tích và giao dịch XAUUSD trực tiếp qua MT5. Smart Money Concepts, chỉ báo kỹ thuật, quản lý vị thế."
version: 5.0.0
metadata:
  jarvis:
    emoji: "📈"
    category: analysis
    priority: 0.85
    success_rate: 1.0
    usage_count: 0
    allowed_tools:
      - mt5_price
      - mt5_candles
      - mt5_account
      - mt5_positions
      - mt5_order
      - mt5_close
      - mt5_history
      - market_session
      - technical_indicators
      - trading_calendar
      - mt5_analyze
      - mt5_signal
      - mt5_risk
      - mt5_journal_log
      - mt5_journal_stats
      - mt5_journal_sync
      - mt5_smc
      - trade_plan
      - trade_status
      - trade_config
      - trade_control
      - web_search
---

# Trading Analyst — MT5 Live Trading

Skill phân tích và giao dịch XAUUSD trực tiếp qua MT5. Kết nối real-time với MetaTrader 5 qua REST bridge.

## Khi nào kích hoạt

- Giá: "giá vàng", "XAUUSD bao nhiêu", "gold price", "bid ask"
- Phân tích: "phân tích XAUUSD", "technical analysis", "RSI", "MACD", "signal", "tín hiệu"
- SMC: "order block", "OB", "FVG", "fair value gap", "BOS", "ChoCH", "market structure", "liquidity sweep", "supply demand", "smart money"
- Tài khoản: "tài khoản MT5", "balance", "equity", "margin"
- Vị thế: "vị thế đang mở", "positions", "P&L"
- Giao dịch: "mua vàng", "bán XAUUSD", "đóng lệnh", "đặt lệnh"
- Lịch sử: "lịch sử giao dịch", "trade history"
- Session: "phiên giao dịch", "market session", "nên trade không"
- Calendar: "lịch kinh tế", "NFP", "Fed", "CPI"
- Risk: "lot size", "position size", "risk", "R:R"
- Journal: "thống kê", "win rate", "journal", "sync"
- Trading Brain: "trade plan", "bắt đầu trade", "stop trade", "kill trade", "risk config", "trading brain"

## Workflow

1. **Xác định yêu cầu**: Xem giá / phân tích / giao dịch / thông tin
2. **Lấy data live**: Dùng `mt5_price`, `mt5_candles` để lấy data real-time
3. **Phân tích**: `technical_indicators` cho chỉ báo, `market_session` cho context phiên
4. **Hành động**: `mt5_order` (mua/bán) hoặc `mt5_close` (đóng) — LUÔN xác nhận
5. **Bổ sung**: `web_search` cho tin tức, `trading_calendar` cho sự kiện

## Workflow nâng cao (Phase 2)

1. **Phân tích đa TF**: `mt5_analyze` cho phân tích M15/H1/H4/D1 + signal score 0-100
2. **Tín hiệu nhanh**: `mt5_signal` cho điểm số + khuyến nghị BUY/SELL/NEUTRAL
3. **Quản lý rủi ro**: `mt5_risk` tính lot size trước khi vào lệnh
4. **Journal**: `mt5_journal_sync` đồng bộ lịch sử + `mt5_journal_stats` xem thống kê
5. **Luôn check risk trước khi trade**: Dùng `mt5_risk` để tính lot size chính xác

## Workflow SMC (Phase 3)

1. **Market Structure**: `mt5_smc` để xem BOS/ChoCH, trend hiện tại
2. **Key Zones**: Order Blocks + FVG + Supply/Demand gần giá
3. **Confluence**: `mt5_analyze` (đã tích hợp SMC scoring) cho điểm tổng hợp
4. **Entry**: Chờ price về OB/FVG + confirm với RSI/MACD
5. **Luôn dùng `mt5_smc` kèm `mt5_analyze` cho phân tích đầy đủ**

## Workflow Trading Brain (Phase 4)

1. **Khởi động**: `trade_control action=start` để bật Trading Brain
2. **Trade Plan**: `trade_plan` tạo plan tự động (Multi-TF + SMC + Volume Profile + Confluence)
3. **Giám sát**: `trade_status` xem trạng thái, zones, positions, risk
4. **Cấu hình risk**: `trade_config` xem/chỉnh max_daily_loss_pct, max_lot_size, min_rr_ratio
5. **Dừng**: `trade_control action=stop` dừng hoặc `trade_control action=kill` kill khẩn cấp
6. **Trading Brain tự động**: monitor zones, confirm entries, manage positions, enforce risk limits

## Quy tắc

- **TIỀN THẬT**: `mt5_order` và `mt5_close` là lệnh thật. LUÔN hỏi xác nhận user trước khi thực hiện.
- **Volume**: Mặc định 0.01 lot. Không bao giờ đặt > 0.1 lot trừ khi user yêu cầu rõ ràng.
- **Stop Loss**: LUÔN khuyến nghị đặt SL. Cảnh báo nếu user muốn trade không SL.
- **Session**: Check `market_session` trước khi trade. Cảnh báo nếu Dead Zone hoặc low volatility.
- **Calendar**: Check `trading_calendar` trước khi trade. Cảnh báo nếu gần event lớn (FOMC, NFP, CPI).
- **Bridge offline**: Nếu MT5 bridge không kết nối, thông báo rõ và fallback sang `web_search`.
- **Disclaimer**: "Đây là công cụ hỗ trợ, không phải tư vấn đầu tư."

## Ví dụ

**Input**: "phân tích XAUUSD"
**Output**: Gọi `mt5_analyze` + `mt5_smc` → format analysis + SMC patterns

**Input**: "order block XAUUSD"
**Output**: Gọi `mt5_smc` → hiển thị OB, FVG, BOS/ChoCH, S/D zones

**Input**: "mua 0.01 lot XAUUSD SL 2640 TP 2670"
**Output**: Xác nhận với user → gọi `mt5_order`

**Input**: "tài khoản"
**Output**: Gọi `mt5_account` + `mt5_positions` → tổng hợp
