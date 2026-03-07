---
name: webapp-testing
description: >
  Toolkit for testing web applications using Playwright. Supports verifying
  frontend functionality, debugging UI behavior, capturing browser screenshots,
  and viewing browser logs. Use when testing local or remote web apps.
version: 1.0.0
license: MIT
metadata:
  jarvis:
    emoji: "🧪"
    category: productivity
    priority: 0.70
    created_by: ported
    requires:
      bins: [python3]
    mcp_tools: [playwright]
---

# Web Application Testing

## Khi nào kích hoạt
- User muốn test web application
- User cần debug UI behavior
- User muốn chụp screenshot trang web
- User cần verify frontend functionality

## Decision Tree

```
User task → Is it static HTML?
    ├─ Yes → Read HTML file → Identify selectors → Playwright script
    └─ No (dynamic webapp) → Is server running?
        ├─ No → Start server first, then Playwright
        └─ Yes → Reconnaissance-then-action:
            1. Navigate + wait for networkidle
            2. Screenshot / inspect DOM
            3. Identify selectors
            4. Execute actions
```

## Workflow

### 1. Setup
```bash
pip install playwright
python -m playwright install chromium
```

### 2. Basic Script Pattern
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('http://localhost:5173')
    page.wait_for_load_state('networkidle')  # CRITICAL for dynamic apps
    # ... automation logic
    browser.close()
```

### 3. Reconnaissance-Then-Action
1. **Inspect rendered DOM:**
   ```python
   page.screenshot(path='/tmp/inspect.png', full_page=True)
   content = page.content()
   buttons = page.locator('button').all()
   ```
2. **Identify selectors** from inspection
3. **Execute actions** with discovered selectors

### 4. Server Management (nếu cần)
```bash
# Single server
python scripts/with_server.py --server "npm run dev" --port 5173 -- python test.py

# Multiple servers (backend + frontend)
python scripts/with_server.py \
  --server "cd backend && python server.py" --port 3000 \
  --server "cd frontend && npm run dev" --port 5173 \
  -- python test.py
```

## Quy tắc
- LUÔN dùng `headless=True` cho automation
- LUÔN `wait_for_load_state('networkidle')` trước khi inspect DOM
- LUÔN close browser khi done
- Dùng descriptive selectors: `text=`, `role=`, CSS, IDs
- Add appropriate waits: `wait_for_selector()`, `wait_for_timeout()`

## Common Pitfalls
- **DON'T** inspect DOM before `networkidle` on dynamic apps
- **DON'T** forget to close browser (resource leak)
- **DO** use screenshots for debugging UI issues
- **DO** capture console logs when debugging JS errors
