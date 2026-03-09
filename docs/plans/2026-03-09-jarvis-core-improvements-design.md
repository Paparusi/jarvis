# JARVIS Core Improvements — Performance + Telegram UX

> Design doc for comprehensive JARVIS improvement sprint.
> Date: 2026-03-09

## Problem Statement

JARVIS hiện tại có 2 vấn đề chính:
1. **Response chậm** — 4-25s cho tool-using queries, user phải chờ lâu
2. **Telegram UI nghèo nàn** — Chỉ có text + 2 button types (feedback/retry), phải gõ lệnh thủ công

## Goals

- Giảm perceived response time xuống <2s cho simple queries
- Telegram UX professional: inline menus, quick actions, rich formatting
- Không break existing functionality (1987 tests passing)

---

## Part A: Performance Improvements

### Current State
| Metric | Value |
|--------|-------|
| System prompt | 10,186 tokens (37% of 30K budget) |
| Tool schemas | 6,100 tokens (116 tools) |
| Cache hit | 6ms (vs 3-4s miss) |
| Simple query (no tools) | ~1.4s |
| 1-tool query | ~4.1s |
| 2+ tool query | ~5.1s |
| Bottleneck | External tool I/O: 68% of time |

### A1: Tool Result Caching (Short-TTL)

**Problem**: Same tool calls repeat across similar queries (e.g., `mt5_price XAUUSD` called 3 times in 5 minutes).

**Solution**: Cache tool results with short TTL (30-60s) based on `(tool_name, args_hash)`.

**Where**: `src/intelligence/agent_loop.py` — wrap `_execute_single_tool()`.

```python
# In agent_loop.py
_tool_result_cache: dict[str, tuple[float, ToolResult]] = {}
_TOOL_CACHE_TTL = 30  # seconds

async def _execute_single_tool(self, name, args):
    cache_key = f"{name}:{hash(json.dumps(args, sort_keys=True))}"
    now = time.time()

    if cache_key in self._tool_result_cache:
        cached_time, cached_result = self._tool_result_cache[cache_key]
        if now - cached_time < _TOOL_CACHE_TTL:
            return cached_result

    result = await self._tools.execute(name, args)
    self._tool_result_cache[cache_key] = (now, result)
    return result
```

**Impact**: Saves 2-4s on repeated tool calls within 30s window.

### A2: Smart Tool Selection

**Problem**: 116 tool schemas sent to Claude every request (6,100 tokens). Claude phải scan hết để chọn tool.

**Solution**: Categorize tools, only send relevant schemas based on query intent.

**Categories**:
| Category | Tools | When to include |
|----------|-------|-----------------|
| `always` | web_search, fetch_url, run_python, remember, recall | Every request |
| `trading` | mt5_* (22 tools), trade_* (5 tools) | Query matches trading patterns |
| `security` | pentest_*, recon_*, exploit_* (30+ tools) | /pentest, /bounty, /hunt commands |
| `code` | ast_analyze, complexity_check, code_search, diff_summary | Code-related queries |
| `data` | csv_analyze, json_query, sqlite_query | Data analysis queries |
| `mcp` | mcp_filesystem_*, mcp_github_* | File/GitHub operations |

**Where**: `src/tools/base.py` — add `category` field to tool registration. `src/intelligence/agent_loop.py` — filter schemas before sending.

**Impact**: Typical chat query sends 15-20 tool schemas instead of 116. Saves ~4,500 tokens per request. Faster Claude response (less to parse).

### A3: Streaming Everywhere

**Problem**: Non-tool queries use direct `route()` (no streaming). User sees nothing for 1.4s then full response.

**Solution**: Use `route_stream` for ALL queries in Telegram adapter, not just tool queries.

**Where**: `src/gateway/channels/telegram.py` — unify `_handle_message()` to always use streaming path.

**Impact**: Time-to-first-byte drops from 1.4s to ~300ms. User sees text appearing immediately.

---

## Part B: Telegram UX Overhaul

### Current State
- 31 commands (text-only)
- 2 button types: feedback (thumbs up/down), retry
- No command palette, no menus, no quick actions
- User must memorize and type commands manually

### B1: Main Menu Keyboard

**Persistent ReplyKeyboardMarkup** shown after /start and available always:

```
┌──────────────────────────────┐
│  📊 Status  │  🧠 Memory    │
│  📈 Trading │  🔍 Search    │
│  ⚙️ Settings│  ❓ Help      │
└──────────────────────────────┘
```

**Implementation**: `ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)`

Each button sends text that triggers corresponding handler:
- "📊 Status" → calls `_handle_status()`
- "📈 Trading" → shows trading sub-menu (inline keyboard)
- "🔍 Search" → prompts for search query
- etc.

### B2: Inline Sub-Menus

After pressing main menu button, show inline keyboard with options:

**Trading Menu** (triggered by "📈 Trading"):
```
┌────────────────────────────────┐
│ 💰 Giá XAUUSD │ 📊 Phân tích │
│ 📋 Positions  │ 📜 History   │
│ 🧠 Trade Plan │ ⚙️ Config    │
│ ▶️ Start Brain│ ⏹ Stop Brain │
└────────────────────────────────┘
```

**Memory Menu**:
```
┌──────────────────────────┐
│ 🔍 Search  │ 📝 Remember │
│ 📊 Stats   │ 🗑 Clear    │
└──────────────────────────┘
```

**Settings Menu**:
```
┌──────────────────────────────┐
│ 🌐 Language │ 🤖 Model     │
│ 🔔 Alerts   │ 📊 Diagnostics│
└──────────────────────────────┘
```

### B3: Context-Aware Quick Actions

After each JARVIS response, show relevant follow-up buttons:

**After price query**:
```
[📊 Phân tích kỹ thuật] [📈 Trade Plan] [👍] [👎]
```

**After analysis response**:
```
[💡 Gợi ý entry] [⚠️ Risk check] [👍] [👎]
```

**After general response**:
```
[🔍 Tìm thêm] [📌 Nhớ giúp] [👍] [👎]
```

**Logic**: Pattern match on response content/tools used to determine which buttons to show.

```python
def _get_quick_actions(self, user_message: str, response: str, tools_used: list[str]) -> list[list[InlineKeyboardButton]]:
    buttons = []

    if any(t.startswith("mt5_") for t in tools_used):
        buttons.append([
            InlineKeyboardButton("📊 Phân tích", callback_data="quick:analyze"),
            InlineKeyboardButton("📈 Trade Plan", callback_data="quick:trade_plan"),
        ])
    elif any(t in ("web_search", "fetch_url") for t in tools_used):
        buttons.append([
            InlineKeyboardButton("🔍 Tìm thêm", callback_data="quick:search_more"),
            InlineKeyboardButton("📌 Nhớ giúp", callback_data="quick:remember"),
        ])

    # Always add feedback row
    buttons.append([
        InlineKeyboardButton("👍", callback_data="feedback:positive"),
        InlineKeyboardButton("👎", callback_data="feedback:negative"),
    ])
    return buttons
```

### B4: Trading Notifications with Actions

When Trading Brain sends alerts, include action buttons:

```
🔔 XAUUSD đạt zone 2920 (Confluence: 85%)

Entry: SELL @ 2920.50
SL: 2925.00 (4.5 pips)
TP1: 2915.00 (5.5 pips, RR 1.2)

[✅ Approve] [❌ Skip] [📊 Detail] [⚙️ Modify]
```

### B5: Progress Indicators

Better visual feedback during processing:

```python
# Phase 1: Immediate typing action
await chat.send_action("typing")

# Phase 2: Status message with estimated time
status = await message.reply_text("🔄 Đang xử lý... (~3s)")

# Phase 3: Update with tool progress
await status.edit_text("🔍 web_search... (1/2 tools)")
await status.edit_text("✅ web_search\n📊 mt5_price... (2/2 tools)")
await status.edit_text("💭 Tổng hợp kết quả...")

# Phase 4: Final response replaces status
```

### B6: Telegram Bot Commands Menu

Register commands with BotFather for native command palette:

```python
commands = [
    BotCommand("status", "Xem trạng thái hệ thống"),
    BotCommand("memory", "Quản lý bộ nhớ"),
    BotCommand("trade", "Trading Brain"),
    BotCommand("mt5", "Giá & tài khoản MT5"),
    BotCommand("skills", "Danh sách kỹ năng"),
    BotCommand("health", "Kiểm tra sức khỏe"),
    BotCommand("digest", "Tin tức hàng ngày"),
    BotCommand("help", "Trợ giúp"),
]
await app.bot.set_my_commands(commands)
```

---

## Architecture Decisions

### Button State Management

**Problem**: Telegram callback buttons lose context after message edit.

**Solution**: Simple in-memory cache with TTL:

```python
class ButtonStateCache:
    """Track button context for callback handlers."""

    def __init__(self, max_size: int = 200, ttl: int = 300):
        self._cache: dict[str, tuple[float, dict]] = {}
        self._max_size = max_size
        self._ttl = ttl

    def store(self, callback_id: str, context: dict) -> None:
        self._cache[callback_id] = (time.time(), context)
        self._trim()

    def get(self, callback_id: str) -> dict | None:
        if callback_id in self._cache:
            ts, ctx = self._cache[callback_id]
            if time.time() - ts < self._ttl:
                return ctx
        return None
```

### Tool Category System

Add `category` to tool registration (backward compatible):

```python
# In src/tools/base.py
@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Callable
    category: str = "general"  # New field

# Categories: "always", "trading", "security", "code", "data", "mcp", "general"
```

### Backward Compatibility

- All existing commands continue to work unchanged
- Keyboard/button additions are purely additive
- Tool category defaults to "general" (included always until categorized)
- Tests don't need changes for UX additions (UI-only changes)

---

## Implementation Priority

| # | Feature | Impact | Effort | Priority |
|---|---------|--------|--------|----------|
| 1 | B6: Bot commands menu | High (discoverable) | Low (10 min) | P0 |
| 2 | B1: Main menu keyboard | High (navigation) | Low (30 min) | P0 |
| 3 | A3: Streaming everywhere | High (perceived speed) | Medium (1h) | P0 |
| 4 | B5: Progress indicators | Medium (UX polish) | Low (30 min) | P1 |
| 5 | B3: Quick action buttons | High (engagement) | Medium (1h) | P1 |
| 6 | A1: Tool result caching | Medium (repeat queries) | Low (30 min) | P1 |
| 7 | B2: Inline sub-menus | High (power users) | Medium (2h) | P1 |
| 8 | A2: Smart tool selection | Medium (token savings) | High (3h) | P2 |
| 9 | B4: Trading notifications | Medium (trading UX) | Medium (1h) | P2 |

---

## Non-Goals

- Web UI improvements (separate effort)
- CLI improvements (low priority)
- New tools/skills (separate effort)
- Model switching via buttons (deferred — only 1 model currently)

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Simple query perceived time | 1.4s | <0.5s (streaming) |
| Tool query perceived time | 4-5s | <1s to first feedback |
| User interaction method | Type commands | Tap buttons |
| Telegram commands discoverable | 0 (must know) | 8 (bot menu) |
| Quick actions per response | 2 (feedback only) | 4-6 (context-aware) |
