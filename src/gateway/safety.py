"""Safety Guardrails — Rate limiting, content safety, prompt injection defense.

Protects JARVIS from:
- Excessive requests (rate limiting per user)
- Prompt injection attacks
- Harmful content generation requests
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from src.utils.logging import get_logger

log = get_logger("gateway.safety")


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    max_requests: int = 30       # Max requests per window
    window_seconds: int = 60     # Time window in seconds
    cooldown_seconds: int = 30   # Cooldown after limit hit


@dataclass
class SafetyResult:
    """Result of a safety check."""
    safe: bool
    reason: str = ""
    category: str = ""  # "rate_limit", "injection", "content"


class RateLimiter:
    """Per-user rate limiting with sliding window."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._cooldowns: dict[str, float] = {}

    def check(self, user_id: str) -> SafetyResult:
        """Check if user is within rate limits."""
        now = time.time()

        # Check cooldown
        if user_id in self._cooldowns:
            if now < self._cooldowns[user_id]:
                remaining = int(self._cooldowns[user_id] - now)
                return SafetyResult(
                    safe=False,
                    reason=f"Bạn đang gửi tin quá nhanh. Thử lại sau {remaining}s.",
                    category="rate_limit",
                )
            else:
                del self._cooldowns[user_id]

        # Clean old requests outside window
        window_start = now - self._config.window_seconds
        self._requests[user_id] = [
            t for t in self._requests[user_id] if t > window_start
        ]

        # Check count
        if len(self._requests[user_id]) >= self._config.max_requests:
            self._cooldowns[user_id] = now + self._config.cooldown_seconds
            log.warning("rate_limit_hit", user_id=user_id,
                        count=len(self._requests[user_id]))
            return SafetyResult(
                safe=False,
                reason=f"Giới hạn {self._config.max_requests} tin nhắn/{self._config.window_seconds}s. "
                       f"Thử lại sau {self._config.cooldown_seconds}s.",
                category="rate_limit",
            )

        # Record request
        self._requests[user_id].append(now)
        return SafetyResult(safe=True)

    def get_usage(self, user_id: str) -> dict:
        """Get current usage stats for a user."""
        now = time.time()
        window_start = now - self._config.window_seconds
        recent = [t for t in self._requests.get(user_id, []) if t > window_start]
        return {
            "requests_in_window": len(recent),
            "max_requests": self._config.max_requests,
            "in_cooldown": user_id in self._cooldowns,
        }


# --- Prompt Injection Detection ---

_INJECTION_PATTERNS = [
    # Direct instruction override attempts
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)",
    r"disregard\s+(your|all|the)\s+(instructions|rules|guidelines)",
    r"forget\s+(everything|all)\s+(you|about|previous)",
    r"bỏ qua\s+(tất cả|mọi)\s*(hướng dẫn|quy tắc|lệnh)",
    r"new\s+instructions?\s*:",
    r"system\s*prompt\s*:",

    # Role manipulation
    r"you\s+are\s+now\s+(?:a|an|the)\s+(?:different|new|evil)",
    r"pretend\s+(to be|you are)\s+(?:a|an)\s+",
    r"act\s+as\s+(?:a|an)\s+(?:different|evil|unrestricted)",
    r"từ\s+giờ\s+(?:bạn|mày)\s+là\s+",

    # Jailbreak patterns
    r"DAN\s+mode",
    r"developer\s+mode\s+enabled",
    r"do\s+anything\s+now",
    r"jailbreak",

    # Prompt extraction
    r"(?:what|show|reveal|tell|print|display)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?prompt",
    r"repeat\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)",
]

_INJECTION_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def check_injection(text: str) -> SafetyResult:
    """Check for prompt injection attempts."""
    for pattern in _INJECTION_COMPILED:
        if pattern.search(text):
            log.warning("injection_detected", pattern=pattern.pattern[:50],
                        text_preview=text[:80])
            return SafetyResult(
                safe=False,
                reason="Tôi phát hiện nội dung có vẻ như cố thay đổi hướng dẫn của tôi. "
                       "Tôi chỉ tuân theo quy tắc an toàn đã được thiết lập.",
                category="injection",
            )
    return SafetyResult(safe=True)


# --- Content Safety ---

_HARMFUL_PATTERNS = [
    # Dangerous activities
    r"(?:cách|how\s+to)\s+(?:chế tạo|make|build)\s+(?:bom|bomb|explosive|thuốc nổ)",
    r"(?:cách|how\s+to)\s+(?:hack|tấn công|attack)\s+(?:vào|into)\s+",
    r"(?:cách|how\s+to)\s+(?:trộm|steal|lấy cắp)\s+",

    # Self-harm (respond with care resources instead)
    r"(?:muốn|want\s+to)\s+(?:tự tử|chết|die|kill\s+myself|suicide)",
]

_HARMFUL_COMPILED = [re.compile(p, re.IGNORECASE) for p in _HARMFUL_PATTERNS]


def check_content_safety(text: str) -> SafetyResult:
    """Check for harmful content requests."""
    for pattern in _HARMFUL_COMPILED:
        if pattern.search(text):
            log.warning("harmful_content_detected", text_preview=text[:80])
            return SafetyResult(
                safe=False,
                reason="Tôi không thể hỗ trợ với nội dung có thể gây hại. "
                       "Nếu bạn cần trợ giúp, hãy liên hệ đường dây hỗ trợ phù hợp.",
                category="content",
            )
    return SafetyResult(safe=True)


class SafetyGuard:
    """Combined safety guard — single entry point for all checks."""

    def __init__(self) -> None:
        self._rate_limiter = RateLimiter()

    def check_message(self, user_id: str, text: str) -> SafetyResult:
        """Run all safety checks on an incoming message.

        Returns SafetyResult (safe=True if all checks pass).
        """
        # 1. Rate limit
        rate_result = self._rate_limiter.check(user_id)
        if not rate_result.safe:
            return rate_result

        # 2. Prompt injection
        inject_result = check_injection(text)
        if not inject_result.safe:
            return inject_result

        # 3. Content safety
        content_result = check_content_safety(text)
        if not content_result.safe:
            return content_result

        return SafetyResult(safe=True)

    def get_rate_limit_usage(self, user_id: str) -> dict:
        return self._rate_limiter.get_usage(user_id)
