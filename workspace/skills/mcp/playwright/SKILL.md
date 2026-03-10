---
name: mcp-playwright
description: >
  Tools from MCP server 'playwright'.
  Available tools: browser_close, browser_resize, browser_console_messages, browser_handle_dialog, browser_evaluate, browser_file_upload, browser_fill_form, browser_install, browser_press_key, browser_type, browser_navigate, browser_navigate_back, browser_network_requests, browser_run_code, browser_take_screenshot, browser_snapshot, browser_click, browser_drag, browser_hover, browser_select_option, browser_tabs, browser_wait_for
version: 1.0.0
metadata:
  jarvis:
    auto_generated: true
    created_by: mcp_bridge
    category: mcp
    priority: 0.7
---

# MCP: playwright

## Available Tools
- `browser_close`: Close the page
- `browser_resize`: Resize the browser window
- `browser_console_messages`: Returns all console messages
- `browser_handle_dialog`: Handle a dialog
- `browser_evaluate`: Evaluate JavaScript expression on page or element
- `browser_file_upload`: Upload one or multiple files
- `browser_fill_form`: Fill multiple form fields
- `browser_install`: Install the browser specified in the config. Call this if you get an error about the browser not being installed.
- `browser_press_key`: Press a key on the keyboard
- `browser_type`: Type text into editable element
- `browser_navigate`: Navigate to a URL
- `browser_navigate_back`: Go back to the previous page in the history
- `browser_network_requests`: Returns all network requests since loading the page
- `browser_run_code`: Run Playwright code snippet
- `browser_take_screenshot`: Take a screenshot of the current page. You can't perform actions based on the screenshot, use browser_snapshot for actions.
- `browser_snapshot`: Capture accessibility snapshot of the current page, this is better than screenshot
- `browser_click`: Perform click on a web page
- `browser_drag`: Perform drag and drop between two elements
- `browser_hover`: Hover over element on page
- `browser_select_option`: Select an option in a dropdown
- `browser_tabs`: List, create, close, or select a browser tab.
- `browser_wait_for`: Wait for text to appear or disappear or a specified time to pass

## Workflow
1. Identify which MCP tool is needed for the user's request
2. Call the appropriate tool with correct parameters
3. Format the result for the user

## Rules
- Always use the MCP-prefixed tool name (e.g., `mcp_playwright_toolname`)
- Handle errors gracefully — MCP servers may be unavailable
