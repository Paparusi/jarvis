"""HTTP Client Tool — Full HTTP request support (GET/POST/PUT/DELETE/PATCH).

Unlike fetch_url (simple GET + HTML→text), this tool supports:
- All HTTP methods with custom headers
- JSON/form body
- Auth headers (Bearer, Basic)
- Response headers inspection
- Status code awareness
"""

from __future__ import annotations

import asyncio
import json

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.http_client")

_TIMEOUT = 30
_MAX_RESPONSE_SIZE = 50000  # chars


async def http_request(
    url: str,
    method: str = "GET",
    headers: str = "{}",
    body: str = "",
    auth_token: str = "",
) -> ToolResult:
    """Make an HTTP request with full control."""
    import time
    start = time.monotonic()

    method = method.upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        return ToolResult(success=False, output="", error=f"Unsupported method: {method}")

    if not url or not url.startswith(("http://", "https://")):
        return ToolResult(success=False, output="", error="URL must start with http:// or https://")

    # Parse headers
    try:
        req_headers = json.loads(headers) if headers and headers != "{}" else {}
    except json.JSONDecodeError:
        req_headers = {}

    # Add auth header
    if auth_token:
        if auth_token.startswith("Basic "):
            req_headers["Authorization"] = auth_token
        else:
            req_headers["Authorization"] = f"Bearer {auth_token}"

    # Parse body
    req_body = None
    req_json = None
    if body and method in ("POST", "PUT", "PATCH"):
        try:
            req_json = json.loads(body)
        except json.JSONDecodeError:
            req_body = body

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=req_headers,
                json=req_json,
                content=req_body,
            )

        elapsed = int((time.monotonic() - start) * 1000)

        # Format response
        resp_body = response.text
        if len(resp_body) > _MAX_RESPONSE_SIZE:
            resp_body = resp_body[:_MAX_RESPONSE_SIZE] + "\n...[truncated]"

        # Try to pretty-print JSON responses
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                parsed = response.json()
                resp_body = json.dumps(parsed, indent=2, ensure_ascii=False)
                if len(resp_body) > _MAX_RESPONSE_SIZE:
                    resp_body = resp_body[:_MAX_RESPONSE_SIZE] + "\n...[truncated]"
            except Exception:
                pass

        output = (
            f"Status: {response.status_code} {response.reason_phrase}\n"
            f"Time: {elapsed}ms\n"
            f"Content-Type: {content_type}\n"
            f"Content-Length: {len(response.content)}\n"
            f"\n{resp_body}"
        )

        return ToolResult(
            success=True,
            output=output,
            execution_time_ms=elapsed,
            data={
                "status_code": response.status_code,
                "headers": dict(response.headers),
            },
        )

    except httpx.TimeoutException:
        return ToolResult(success=False, output="", error=f"Request timed out after {_TIMEOUT}s")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"HTTP error: {e}")


http_request_tool = ToolDefinition(
    name="http_request",
    description=(
        "Make HTTP requests (GET/POST/PUT/DELETE/PATCH) with custom headers, "
        "body, and auth. Use for API calls, webhooks, and authenticated requests."
    ),
    parameters=[
        ToolParameter(name="url", type="string", description="Full URL to request"),
        ToolParameter(
            name="method", type="string", description="HTTP method",
            required=False, default="GET",
            enum=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
        ToolParameter(
            name="headers", type="string",
            description="JSON string of request headers, e.g. '{\"Accept\": \"application/json\"}'",
            required=False, default="{}",
        ),
        ToolParameter(
            name="body", type="string",
            description="Request body (JSON string for APIs, or raw text)",
            required=False, default="",
        ),
        ToolParameter(
            name="auth_token", type="string",
            description="Auth token (auto-wrapped as 'Bearer <token>', or use 'Basic ...' prefix)",
            required=False, default="",
        ),
    ],
    handler=http_request,
    timeout_seconds=35,
)
