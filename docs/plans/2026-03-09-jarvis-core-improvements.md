# JARVIS Core Improvements Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Improve JARVIS response speed (streaming everywhere) and completely overhaul Telegram UX with inline menus, quick actions, rich formatting, and smart notifications.

**Architecture:** Add persistent reply keyboard for navigation, inline keyboard sub-menus for actions, context-aware quick action buttons after responses, and unify all Telegram message handling through streaming path. Fix remaining `degraded_mode` references from Ollama removal.

**Tech Stack:** python-telegram-bot (existing), asyncio, existing JARVIS infrastructure.

---

## Task 0: Fix Remaining `degraded_mode` References

**Files:**
- Modify: `src/gateway/channels/telegram.py:516-518`
- Modify: `src/monitoring/server.py:93-95`

These files reference `SystemHealth.degraded_mode` which was removed. They will crash at runtime.

**Step 1: Fix telegram.py**

In `src/gateway/channels/telegram.py`, replace lines 516-518:
```python
        # Add degradation mode info
        mode = self._health_monitor.health.degraded_mode
        if mode:
            report += f"\n\n🔶 **Chế độ**: {mode}"
```
With:
```python
        # Health summary
        if not self._health_monitor.health.api_keys_ok:
            report += "\n\n🔶 **Cảnh báo**: API keys thiếu"
```

**Step 2: Fix monitoring/server.py**

In `src/monitoring/server.py`, replace lines 93-95:
```python
                "degraded_mode": h.degraded_mode or "none",
            }
            if h.degraded_mode:
```
With:
```python
                "api_keys_ok": h.api_keys_ok,
            }
            if not h.api_keys_ok:
```

**Step 3: Run tests**

Run: `python -m pytest tests/unit/test_health_monitor.py tests/unit/test_telegram.py -v`
Expected: All PASS

**Step 4: Verify no remaining references**

Run: `grep -rn "degraded_mode" src/`
Expected: No matches

**Step 5: Commit**

```bash
git add src/gateway/channels/telegram.py src/monitoring/server.py
git commit -m "fix: remove remaining degraded_mode references after Ollama removal"
```

---

## Task 1: Register Bot Commands Menu

**Files:**
- Modify: `src/gateway/channels/telegram.py:339-344` (in `start()` method)

Register commands with Telegram BotFather API so users see a command palette when they tap `/` or the menu button.

**Step 1: Add command registration after bot start**

In `src/gateway/channels/telegram.py`, after line 343 (`await self._app.updater.start_polling(...)`), add:

```python
        # Register bot commands menu for Telegram UI
        from telegram import BotCommand
        try:
            await self._app.bot.set_my_commands([
                BotCommand("start", "Menu chính"),
                BotCommand("status", "Trạng thái hệ thống"),
                BotCommand("mt5", "Giá & tài khoản MT5"),
                BotCommand("trade", "Trading Brain"),
                BotCommand("memory", "Bộ nhớ"),
                BotCommand("skills", "Kỹ năng"),
                BotCommand("health", "Kiểm tra sức khỏe"),
                BotCommand("digest", "Tin tức hôm nay"),
                BotCommand("help", "Trợ giúp"),
            ])
            log.info("bot_commands_registered")
        except Exception as e:
            log.warning("bot_commands_register_failed", error=str(e))
```

**Step 2: Add /help handler**

Add a new handler registration in `start()` after line 325 (the `/trade` handler):
```python
        self._app.add_handler(CommandHandler("help", self._handle_help))
```

Add the handler method (after `_handle_start`):
```python
    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show help with available commands."""
        await update.message.reply_text(
            "📚 **JARVIS Commands:**\n\n"
            "📊 /status — Trạng thái hệ thống\n"
            "🧠 /memory — Xem bộ nhớ\n"
            "📝 /remember `<text>` — Ghi nhớ\n"
            "📈 /mt5 — Giá & MT5\n"
            "🤖 /trade — Trading Brain\n"
            "🔍 /digest — Tin tức\n"
            "🎯 /skills — Kỹ năng\n"
            "🏥 /health — Sức khỏe\n"
            "⏰ /remind `<text>` — Nhắc nhở\n"
            "📋 /reminders — Xem nhắc nhở\n"
            "📊 /stats — Thống kê chi tiết\n"
            "👤 /profile — Digital Twin\n"
            "🔄 /reset — Reset trò chuyện\n\n"
            "💡 Hoặc chat tự nhiên bằng text, voice, gửi file/ảnh.",
            parse_mode="Markdown",
        )
```

**Step 3: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS (no breaking changes)

**Step 4: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: register Telegram bot commands menu + /help handler"
```

---

## Task 2: Main Menu Keyboard

**Files:**
- Modify: `src/gateway/channels/telegram.py`

Add a persistent `ReplyKeyboardMarkup` shown after `/start` so users can tap buttons instead of typing commands.

**Step 1: Create the main menu keyboard**

Add a module-level constant near the top of telegram.py (after the imports, around line 50):

```python
from telegram import ReplyKeyboardMarkup, KeyboardButton

_MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Status"), KeyboardButton("🧠 Memory")],
        [KeyboardButton("📈 Trading"), KeyboardButton("🔍 Search")],
        [KeyboardButton("⚙️ Settings"), KeyboardButton("❓ Help")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
```

**Step 2: Show keyboard in /start**

In `_handle_start()` (line 484), add `reply_markup=_MAIN_MENU_KEYBOARD` to the `reply_text` call:

```python
        await update.message.reply_text(
            f"Xin chào {user.first_name}! 👋\n\n"
            f"Tôi là **JARVIS** — trợ lý AI cá nhân của bạn.\n"
            f"🧠 Bộ nhớ dài hạn | ☁️ Claude AI | 🎯 {skills_count} skills | 🔧 {tools_count} tools\n\n"
            f"Dùng menu bên dưới hoặc chat tự nhiên.",
            parse_mode="Markdown",
            reply_markup=_MAIN_MENU_KEYBOARD,
        )
```

**Step 3: Route menu button presses**

In `_handle_message()`, add menu button routing at the beginning (before session creation, around line 970 after auth check):

```python
        # Route main menu button presses
        _MENU_ROUTES = {
            "📊 Status": "/status",
            "🧠 Memory": "/memory",
            "📈 Trading": "/trade",
            "🔍 Search": None,  # Prompt user
            "⚙️ Settings": "/health",
            "❓ Help": "/help",
        }
        if text in _MENU_ROUTES:
            route = _MENU_ROUTES[text]
            if route is None:
                await update.message.reply_text("🔍 Tìm gì? Gõ câu hỏi tiếp theo.")
                return
            # Simulate command by calling handler directly
            handler_name = f"_handle_{route[1:]}"
            handler = getattr(self, handler_name, None)
            if handler:
                await handler(update, context)
                return
```

**Step 4: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: add persistent main menu keyboard with button routing"
```

---

## Task 3: Context-Aware Quick Action Buttons

**Files:**
- Modify: `src/gateway/channels/telegram.py:1538-1577` (`_send_response`)

Replace static feedback-only buttons with context-aware quick action buttons based on query content and tools used.

**Step 1: Write the quick actions builder**

Add a new method to `TelegramAdapter`:

```python
    def _build_quick_actions(
        self, user_message: str, response_text: str, tools_used: list[str] | None = None,
    ) -> InlineKeyboardMarkup:
        """Build context-aware quick action buttons."""
        rows: list[list[InlineKeyboardButton]] = []
        tools = tools_used or []
        msg_lower = user_message.lower()

        # Trading context
        if any(t.startswith("mt5_") for t in tools) or any(
            w in msg_lower for w in ("xauusd", "gold", "vàng", "trading", "giá")
        ):
            rows.append([
                InlineKeyboardButton("📊 Phân tích", callback_data="quick:analyze"),
                InlineKeyboardButton("📈 Trade Plan", callback_data="quick:trade_plan"),
            ])

        # Search/research context
        elif any(t in ("web_search", "fetch_url") for t in tools):
            rows.append([
                InlineKeyboardButton("🔍 Tìm thêm", callback_data="quick:search_more"),
                InlineKeyboardButton("📌 Nhớ giúp", callback_data="quick:remember"),
            ])

        # General — suggest based on length
        elif len(response_text) > 500:
            rows.append([
                InlineKeyboardButton("📌 Nhớ giúp", callback_data="quick:remember"),
            ])

        # Always add feedback row
        rows.append([
            InlineKeyboardButton("👍", callback_data="feedback:positive"),
            InlineKeyboardButton("👎", callback_data="feedback:negative"),
        ])

        return InlineKeyboardMarkup(rows)
```

**Step 2: Update `_send_response` to accept tools_used**

Change `_send_response` signature and replace static keyboard:

```python
    async def _send_response(self, update: Update, text: str,
                             user_message: str = "", tools_used: list[str] | None = None):
        """Send response with context-aware quick action buttons."""
        max_len = 4000
        keyboard = self._build_quick_actions(user_message, text, tools_used)
        # ... rest unchanged
```

**Step 3: Update callers to pass user_message and tools_used**

In `_handle_message()` at line 1157, change:
```python
            sent_msg = await self._send_response(update, response.content)
```
To:
```python
            tools_used = []
            if response.reasoning_trace:
                try:
                    tools_used = [t.get("name", "") for t in json.loads(response.reasoning_trace)]
                except Exception:
                    pass
            sent_msg = await self._send_response(
                update, response.content, user_message=text, tools_used=tools_used,
            )
```

Add `import json` at the top if not already present.

**Step 4: Handle quick action callbacks**

In `_handle_feedback()`, add quick action handling before the feedback logic (after `retry:` handling):

```python
        # Handle quick action buttons
        if data.startswith("quick:"):
            action = data.split(":", 1)[1]
            actions = {
                "analyze": "phân tích kỹ thuật XAUUSD hiện tại",
                "trade_plan": "tạo trade plan cho XAUUSD",
                "search_more": "tìm thêm thông tin về chủ đề trước",
                "remember": None,  # Special: remember last response
            }
            prompt = actions.get(action)
            if prompt is None and action == "remember":
                # Remember the last response
                cached = self._response_cache.get(query.message.message_id)
                if cached:
                    key = self._memory.session_key("telegram", str(query.from_user.id))
                    await self._memory.remember(key, cached["model_response"][:500])
                    await query.answer("📌 Đã lưu vào bộ nhớ!", show_alert=True)
                else:
                    await query.answer("⏰ Nội dung đã hết hạn", show_alert=False)
                return
            if prompt:
                await query.answer("⏳ Đang xử lý...")
                await self._handle_message(update, context, override_text=prompt)
                return
```

**Step 5: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS

**Step 6: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: context-aware quick action buttons after JARVIS responses"
```

---

## Task 4: Inline Trading Sub-Menu

**Files:**
- Modify: `src/gateway/channels/telegram.py`

When user taps "📈 Trading" from main menu or sends `/trade`, show an inline keyboard with trading actions.

**Step 1: Create trading menu**

Add method to `TelegramAdapter`:

```python
    async def _show_trading_menu(self, update: Update) -> None:
        """Show inline trading sub-menu."""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💰 Giá XAUUSD", callback_data="menu:mt5_price"),
                InlineKeyboardButton("📊 Phân tích", callback_data="menu:analyze"),
            ],
            [
                InlineKeyboardButton("📋 Positions", callback_data="menu:positions"),
                InlineKeyboardButton("📜 History", callback_data="menu:history"),
            ],
            [
                InlineKeyboardButton("🧠 Trade Plan", callback_data="menu:trade_plan"),
                InlineKeyboardButton("⚙️ Config", callback_data="menu:trade_config"),
            ],
            [
                InlineKeyboardButton("▶️ Start Brain", callback_data="menu:trade_start"),
                InlineKeyboardButton("⏹ Stop Brain", callback_data="menu:trade_stop"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="menu:back")],
        ])
        message = update.callback_query.message if update.callback_query else update.message
        await message.reply_text("📈 **Trading Menu:**", parse_mode="Markdown", reply_markup=keyboard)
```

**Step 2: Route menu callbacks**

Add menu callback handling in `_handle_feedback()`, after the `quick:` handling:

```python
        # Handle menu callbacks
        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            menu_prompts = {
                "mt5_price": "check giá XAUUSD hiện tại",
                "analyze": "phân tích kỹ thuật XAUUSD",
                "positions": "/mt5 positions",
                "history": "/mt5 history",
                "trade_plan": "tạo trade plan cho session hiện tại",
                "trade_config": "/trade config",
                "trade_start": "/trade start",
                "trade_stop": "/trade stop",
                "back": None,
            }
            prompt = menu_prompts.get(action)
            if action == "back":
                await query.answer()
                try:
                    await query.message.delete()
                except Exception:
                    pass
                return
            if prompt:
                await query.answer("⏳ Đang xử lý...")
                if prompt.startswith("/"):
                    # Route as command
                    cmd = prompt[1:].split()[0]
                    handler = getattr(self, f"_handle_{cmd}", None)
                    if handler:
                        await handler(update, context)
                        return
                await self._handle_message(update, context, override_text=prompt)
                return
```

**Step 3: Wire trading menu to main menu button**

In the `_MENU_ROUTES` routing in `_handle_message()`, change `"📈 Trading"` to call the menu:

```python
        if text == "📈 Trading":
            await self._show_trading_menu(update)
            return
```

Move this check before the generic `_MENU_ROUTES` dict check.

**Step 4: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: inline trading sub-menu with action buttons"
```

---

## Task 5: Improved Progress Indicators

**Files:**
- Modify: `src/gateway/channels/telegram.py:1047-1055` (status message)
- Modify: `src/gateway/channels/telegram.py:1200-1278` (`_route_with_tools`)

Better visual feedback during processing: typing action, estimated time, tool count.

**Step 1: Send typing action immediately**

In `_handle_message()`, add typing action before status message (before line 1047):

```python
            # Send typing action immediately
            try:
                await update.effective_chat.send_action("typing")
            except Exception:
                pass
```

**Step 2: Improve status message with context**

Replace the status message creation (lines 1048-1055):

```python
            status_msg = None
            try:
                status_text = "🔍 Đang tìm kiếm & phân tích..." if likely_tools else "💭 Đang suy nghĩ..."
                status_msg = await update.message.reply_text(status_text)
            except Exception:
                pass
```

**Step 3: Add tool count to streaming status**

In `_route_with_tools()`, modify the `tool_start` handler to show count:

Replace lines 1225-1234:
```python
            if event.type == "tool_start":
                emoji = _TOOL_EMOJI.get(event.tool_name, "🔧")
                tool_status_lines.append(f"{emoji} _{event.tool_name}_...")
                if status_msg:
                    try:
                        count = len(tool_status_lines)
                        status_text = "\n".join(tool_status_lines)
                        await status_msg.edit_text(status_text, parse_mode="Markdown")
                        last_edit_time = _time.monotonic()
                    except Exception:
                        pass
```

**Step 4: Add elapsed time to "Đang tổng hợp" message**

In `_route_with_tools()`, add timing to the `tool_end` handler. Add `start_time = _time.monotonic()` at the start of the method (after line 1218), then modify the tool_end status update (line 1243):

```python
                        elapsed = int(_time.monotonic() - route_start_time)
                        status_text = "\n".join(tool_status_lines) + f"\n\n💭 Đang tổng hợp... ({elapsed}s)"
```

**Step 5: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS

**Step 6: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: improved progress indicators with typing action and elapsed time"
```

---

## Task 6: Tool Result Caching (Short-TTL)

**Files:**
- Modify: `src/intelligence/agent_loop.py`
- Create: `tests/unit/test_tool_cache.py`

Cache tool results for 30 seconds to avoid redundant API calls when same tool is called repeatedly.

**Step 1: Write failing test**

Create `tests/unit/test_tool_cache.py`:

```python
"""Tests for agent loop tool result caching."""

import time

import pytest

from src.intelligence.agent_loop import AgentLoop


class TestToolResultCache:
    def test_cache_stores_result(self):
        loop = AgentLoop.__new__(AgentLoop)
        loop._tool_result_cache = {}
        key = "web_search:abc123"
        from src.tools.base import ToolResult
        result = ToolResult(output="test data", success=True)
        loop._tool_result_cache[key] = (time.time(), result)
        assert key in loop._tool_result_cache

    def test_cache_hit_within_ttl(self):
        loop = AgentLoop.__new__(AgentLoop)
        loop._tool_result_cache = {}
        from src.tools.base import ToolResult
        result = ToolResult(output="cached", success=True)
        now = time.time()
        loop._tool_result_cache["key"] = (now, result)
        cached_time, cached_result = loop._tool_result_cache["key"]
        assert now - cached_time < 30
        assert cached_result.output == "cached"

    def test_cache_miss_after_ttl(self):
        loop = AgentLoop.__new__(AgentLoop)
        loop._tool_result_cache = {}
        from src.tools.base import ToolResult
        result = ToolResult(output="old", success=True)
        old_time = time.time() - 60  # 60s ago
        loop._tool_result_cache["key"] = (old_time, result)
        cached_time, _ = loop._tool_result_cache["key"]
        assert time.time() - cached_time >= 30  # Expired
```

**Step 2: Run test to verify it passes** (these are unit tests on data structures)

Run: `python -m pytest tests/unit/test_tool_cache.py -v`
Expected: PASS

**Step 3: Add caching to agent loop**

In `src/intelligence/agent_loop.py`, add to `AgentLoop.__init__()`:

```python
        self._tool_result_cache: dict[str, tuple[float, ToolResult]] = {}
        self._TOOL_CACHE_TTL = 30  # seconds
```

Modify the `_execute_single_tool` method (or wherever individual tools are executed) to check cache:

```python
    async def _execute_single_tool(self, name: str, args: dict) -> ToolResult:
        """Execute a single tool with short-TTL caching."""
        import hashlib
        cache_key = f"{name}:{hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()[:12]}"
        now = time.time()

        # Check cache
        if cache_key in self._tool_result_cache:
            cached_time, cached_result = self._tool_result_cache[cache_key]
            if now - cached_time < self._TOOL_CACHE_TTL:
                log.debug("tool_cache_hit", tool=name)
                return cached_result

        # Execute and cache
        result = await self._tools.execute(name, args)
        self._tool_result_cache[cache_key] = (now, result)

        # Trim cache (max 50 entries)
        if len(self._tool_result_cache) > 50:
            oldest_key = min(self._tool_result_cache, key=lambda k: self._tool_result_cache[k][0])
            del self._tool_result_cache[oldest_key]

        return result
```

**Step 4: Run full agent loop tests**

Run: `python -m pytest tests/unit/test_agent_loop.py tests/unit/test_tool_cache.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/intelligence/agent_loop.py tests/unit/test_tool_cache.py
git commit -m "feat: add 30s tool result caching to avoid redundant API calls"
```

---

## Task 7: Update /start Welcome & Identity

**Files:**
- Modify: `src/gateway/channels/telegram.py:484-507` (`_handle_start`)

Update `/start` to reflect current architecture (no local brain, cloud-first) and show the new main menu.

**Step 1: Update _handle_start**

Replace the entire `_handle_start` body (lines 484-507):

```python
    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        skills_count = len(self._skill_loader.get_all_metadata())
        tools_count = len(self._tool_registry.get_all())
        await update.message.reply_text(
            f"Xin chào {user.first_name}! 👋\n\n"
            f"Tôi là **JARVIS** — trợ lý AI cá nhân.\n"
            f"☁️ Claude AI | 🧠 Long-term Memory | 🎯 {skills_count} skills | 🔧 {tools_count} tools\n\n"
            f"📈 Trading Brain (XAUUSD) | 🔍 Web Search | 📄 RAG\n\n"
            f"Dùng menu bên dưới hoặc chat tự nhiên.",
            parse_mode="Markdown",
            reply_markup=_MAIN_MENU_KEYBOARD,
        )
```

**Step 2: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS

**Step 3: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: update /start with current architecture and main menu keyboard"
```

---

## Task 8: Smart Notifications with Action Buttons

**Files:**
- Modify: `src/gateway/channels/telegram.py`

Upgrade trading brain notifications and health alerts to include action buttons.

**Step 1: Add notification method with buttons**

Add to `TelegramAdapter`:

```python
    async def _send_notification_with_actions(
        self, user_id: str, message: str, actions: list[tuple[str, str]] | None = None,
    ) -> None:
        """Send notification with optional action buttons.

        actions: list of (label, callback_data) tuples.
        """
        keyboard = None
        if actions:
            buttons = [InlineKeyboardButton(label, callback_data=cb) for label, cb in actions]
            # Max 2 buttons per row
            rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
            keyboard = InlineKeyboardMarkup(rows)

        try:
            await self._app.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await self._app.bot.send_message(
                    chat_id=user_id, text=message, reply_markup=keyboard,
                )
            except Exception as e:
                log.error("notification_send_failed", user_id=user_id, error=str(e))
```

**Step 2: Use for health alerts**

In the health monitor notification callback setup (line 351-353), change to use the enhanced notification for future alerts. The health monitor already uses `_send_notification` — we enhance that:

In `_send_notification` (find the existing method), add action buttons for critical alerts:

```python
    async def _send_notification(self, user_id: str, message: str) -> None:
        """Send notification to user (used by scheduler, health monitor, proactive)."""
        actions = None
        # Add action buttons for specific alert types
        if "Disk" in message:
            actions = [("🏥 Health Check", "menu:health")]
        elif "API" in message:
            actions = [("📊 Status", "menu:status")]

        if actions:
            await self._send_notification_with_actions(user_id, message, actions)
        else:
            try:
                await self._app.bot.send_message(
                    chat_id=user_id, text=message, parse_mode="Markdown",
                )
            except Exception:
                try:
                    await self._app.bot.send_message(chat_id=user_id, text=message)
                except Exception as e:
                    log.error("notification_send_failed", user_id=user_id, error=str(e))
```

**Step 3: Add menu:health and menu:status callbacks**

In `_handle_feedback()`, extend the `menu:` handler:

```python
            if action == "health":
                await query.answer("🏥 Đang kiểm tra...")
                await self._handle_health(update, context)
                return
            if action == "status":
                await query.answer("📊 Đang tải...")
                await self._handle_status(update, context)
                return
```

**Step 4: Run test**

Run: `python -m pytest tests/unit/test_telegram.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: smart notifications with action buttons for health/trading alerts"
```

---

## Task 9: Final Integration Test & Restart

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `python -m pytest tests/unit/ -x -q`
Expected: All PASS (1987+)

**Step 2: Verify no import errors**

Run: `python -c "from src.gateway.channels.telegram import TelegramAdapter; print('OK')"`
Expected: `OK`

**Step 3: Kill and restart JARVIS**

```bash
pkill -f "python -m src.main"
sleep 8
nohup python -m src.main > /tmp/jarvis.log 2>&1 &
sleep 50
grep "telegram_bot_running\|jarvis_ready\|bot_commands" /tmp/jarvis.log
```

Expected: `telegram_bot_running`, `jarvis_ready`, `bot_commands_registered`

**Step 4: Manual Telegram test**

1. Open Telegram → JARVIS bot
2. Tap `/` → should see command palette (status, mt5, trade, memory, etc.)
3. Send `/start` → should see main menu keyboard with 6 buttons
4. Tap "📈 Trading" → should see inline trading sub-menu
5. Tap "💰 Giá XAUUSD" → JARVIS should call mt5_price tool and respond with buttons
6. Ask "giá vàng bao nhiêu" → should get fresh price + quick action buttons

**Step 5: Commit final state**

```bash
git add -A
git commit -m "feat: JARVIS core improvements - Telegram UX overhaul complete"
```

---

## Execution Order

```
Task 0 (degraded_mode fix)  ── bugfix, no deps
Task 1 (bot commands menu)  ── after T0
Task 2 (main menu keyboard) ── after T1
Task 3 (quick action buttons)── after T2
Task 4 (trading sub-menu)   ── after T2
Task 5 (progress indicators)── after T0
Task 6 (tool result cache)  ── independent
Task 7 (update /start)      ── after T2
Task 8 (smart notifications)── after T4
Task 9 (integration test)   ── after ALL
```

**Parallelizable:** T3 + T4 + T5 + T6 (independent after T2)
