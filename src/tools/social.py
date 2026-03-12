"""Social Media Tools — Post, search, monitor across Twitter/X, Telegram, Email.

Cung cap cac tool tuong tac voi mang xa hoi va email:
- Twitter/X: dang tweet, tim kiem tweet
- Telegram: gui tin nhan
- Email: gui email qua SMTP
- Social monitoring: theo doi thuong hieu/tu khoa
- Social analytics: tong hop su hien dien tren mang xa hoi
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.social")

_MAX_TWEET_LENGTH = 280


# ---------------------------------------------------------------------------
# Helper: check env vars
# ---------------------------------------------------------------------------

def _check_env_vars(*keys: str) -> str | None:
    """Return error message if any env var is missing, else None."""
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        return (
            f"Thieu bien moi truong: {', '.join(missing)}. "
            f"Vui long cau hinh trong file .env hoac environment."
        )
    return None


# ===========================================================================
# 1. twitter_post — Post tweet via Twitter/X API v2
# ===========================================================================

def _sync_twitter_post(content: str, reply_to: str = "") -> dict:
    """Post a tweet using tweepy (sync, runs via to_thread)."""
    import tweepy

    api_key = os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.getenv("TWITTER_ACCESS_SECRET", "")

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    kwargs: dict = {"text": content}
    if reply_to:
        kwargs["in_reply_to_tweet_id"] = reply_to

    response = client.create_tweet(**kwargs)
    tweet_id = response.data["id"]
    # Build the tweet URL (username not always available, use generic x.com link)
    tweet_url = f"https://x.com/i/web/status/{tweet_id}"
    return {"tweet_id": tweet_id, "tweet_url": tweet_url}


async def handle_twitter_post(content: str, reply_to: str = "") -> ToolResult:
    """Dang tweet len Twitter/X. Toi da 280 ky tu."""
    start = time.monotonic()

    if not content or not content.strip():
        return ToolResult(success=False, output="", error="Noi dung tweet khong duoc de trong")

    if len(content) > _MAX_TWEET_LENGTH:
        return ToolResult(
            success=False, output="",
            error=f"Tweet vuot qua {_MAX_TWEET_LENGTH} ky tu (hien tai: {len(content)}). Vui long rut gon.",
        )

    # Check credentials
    env_err = _check_env_vars(
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
    )
    if env_err:
        return ToolResult(success=False, output="", error=env_err)

    try:
        result = await asyncio.to_thread(_sync_twitter_post, content, reply_to)
        elapsed = int((time.monotonic() - start) * 1000)
        log.info(
            "twitter_post_success",
            tweet_id=result["tweet_id"],
            length=len(content),
        )
        return ToolResult(
            success=True,
            output=f"Tweet da dang thanh cong!\nURL: {result['tweet_url']}",
            execution_time_ms=elapsed,
            data=result,
        )
    except ImportError:
        return ToolResult(
            success=False, output="",
            error="tweepy chua duoc cai dat. Chay: pip install tweepy",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("twitter_post_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Loi dang tweet: {e}",
            execution_time_ms=elapsed,
        )


# ===========================================================================
# 2. twitter_search — Search tweets
# ===========================================================================

def _sync_twitter_search(query: str, max_results: int) -> list[dict]:
    """Search recent tweets using tweepy (sync, runs via to_thread)."""
    import tweepy

    api_key = os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.getenv("TWITTER_ACCESS_SECRET", "")

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    response = client.search_recent_tweets(
        query=query,
        max_results=max(10, min(max_results, 100)),
        tweet_fields=["created_at", "author_id", "public_metrics", "lang"],
    )

    tweets = []
    if response.data:
        for tweet in response.data:
            metrics = tweet.public_metrics or {}
            tweets.append({
                "id": tweet.id,
                "text": tweet.text,
                "created_at": str(tweet.created_at) if tweet.created_at else "",
                "author_id": tweet.author_id,
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
            })
    return tweets


async def handle_twitter_search(query: str, max_results: int = 10) -> ToolResult:
    """Tim kiem tweet tren Twitter/X."""
    start = time.monotonic()

    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Tu khoa tim kiem khong duoc de trong")

    max_results = max(10, min(max_results, 100))

    env_err = _check_env_vars(
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
    )
    if env_err:
        return ToolResult(success=False, output="", error=env_err)

    try:
        tweets = await asyncio.to_thread(_sync_twitter_search, query, max_results)
        elapsed = int((time.monotonic() - start) * 1000)

        if not tweets:
            return ToolResult(
                success=True,
                output=f"Khong tim thay tweet nao cho: '{query}'",
                execution_time_ms=elapsed,
                data={"results": [], "count": 0},
            )

        lines = [f"Ket qua tim kiem tweet cho: '{query}' ({len(tweets)} ket qua)\n"]
        for i, t in enumerate(tweets, 1):
            url = f"https://x.com/i/web/status/{t['id']}"
            lines.append(f"{i}. {t['text'][:150]}")
            lines.append(f"   Likes: {t['likes']} | RT: {t['retweets']} | Replies: {t['replies']}")
            lines.append(f"   {url}")
            lines.append(f"   {t['created_at']}\n")

        log.info("twitter_search_success", query=query, count=len(tweets))
        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={"results": tweets, "count": len(tweets)},
        )
    except ImportError:
        return ToolResult(
            success=False, output="",
            error="tweepy chua duoc cai dat. Chay: pip install tweepy",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("twitter_search_error", query=query, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Loi tim kiem tweet: {e}",
            execution_time_ms=elapsed,
        )


# ===========================================================================
# 3. telegram_send — Send Telegram message
# ===========================================================================

async def handle_telegram_send(
    chat_id: str,
    text: str,
    parse_mode: str = "",
) -> ToolResult:
    """Gui tin nhan Telegram toi bat ky chat nao."""
    start = time.monotonic()

    if not text or not text.strip():
        return ToolResult(success=False, output="", error="Noi dung tin nhan khong duoc de trong")

    if not chat_id or not chat_id.strip():
        return ToolResult(success=False, output="", error="chat_id khong duoc de trong")

    env_err = _check_env_vars("TELEGRAM_BOT_TOKEN")
    if env_err:
        return ToolResult(success=False, output="", error=env_err)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode and parse_mode in ("HTML", "Markdown"):
        payload["parse_mode"] = parse_mode

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(api_url, json=payload)
            data = resp.json()

        elapsed = int((time.monotonic() - start) * 1000)

        if data.get("ok"):
            msg_id = data.get("result", {}).get("message_id", "")
            log.info("telegram_send_success", chat_id=chat_id, message_id=msg_id)
            return ToolResult(
                success=True,
                output=f"Tin nhan da gui thanh cong toi chat {chat_id} (message_id: {msg_id})",
                execution_time_ms=elapsed,
                data={"message_id": msg_id, "chat_id": chat_id},
            )
        else:
            error_desc = data.get("description", "Unknown error")
            error_code = data.get("error_code", 0)
            log.warning("telegram_send_failed", chat_id=chat_id, error=error_desc)
            return ToolResult(
                success=False, output="",
                error=f"Telegram API loi ({error_code}): {error_desc}",
                execution_time_ms=elapsed,
            )

    except ImportError:
        return ToolResult(
            success=False, output="",
            error="httpx chua duoc cai dat. Chay: pip install httpx",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("telegram_send_error", chat_id=chat_id, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Loi gui Telegram: {e}",
            execution_time_ms=elapsed,
        )


# ===========================================================================
# 4. email_send — Send email via SMTP
# ===========================================================================

def _sync_send_email(
    to: str,
    subject: str,
    body: str,
    html: bool = False,
) -> dict:
    """Send email via SMTP (sync, runs via to_thread)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_from
    msg["To"] = to
    msg["Subject"] = subject

    if html:
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [to], msg.as_string())

    return {"from": smtp_from, "to": to, "subject": subject}


async def handle_email_send(
    to: str,
    subject: str,
    body: str,
    html: bool = False,
) -> ToolResult:
    """Gui email qua SMTP. Yeu cau xac nhan truoc khi gui."""
    start = time.monotonic()

    if not to or not to.strip():
        return ToolResult(success=False, output="", error="Dia chi email nguoi nhan khong duoc de trong")

    if not subject or not subject.strip():
        return ToolResult(success=False, output="", error="Tieu de email khong duoc de trong")

    if not body or not body.strip():
        return ToolResult(success=False, output="", error="Noi dung email khong duoc de trong")

    # Basic email format check
    if "@" not in to or "." not in to:
        return ToolResult(success=False, output="", error=f"Dia chi email khong hop le: {to}")

    env_err = _check_env_vars("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS")
    if env_err:
        return ToolResult(success=False, output="", error=env_err)

    try:
        result = await asyncio.to_thread(_sync_send_email, to, subject, body, html)
        elapsed = int((time.monotonic() - start) * 1000)
        log.info("email_send_success", to=to, subject=subject[:50])
        return ToolResult(
            success=True,
            output=f"Email da gui thanh cong!\nTu: {result['from']}\nDen: {result['to']}\nTieu de: {result['subject']}",
            execution_time_ms=elapsed,
            data=result,
        )
    except ImportError as e:
        return ToolResult(
            success=False, output="",
            error=f"Thieu thu vien: {e}",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("email_send_error", to=to, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Loi gui email: {e}",
            execution_time_ms=elapsed,
        )


# ===========================================================================
# 5. social_monitor — Monitor social media mentions
# ===========================================================================

def _sync_social_monitor(keyword: str, platforms: str) -> list[dict]:
    """Search for brand/keyword mentions across social platforms using DuckDuckGo."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    # Build site-specific query
    platform_map = {
        "twitter": "site:twitter.com OR site:x.com",
        "reddit": "site:reddit.com",
        "linkedin": "site:linkedin.com",
        "facebook": "site:facebook.com",
        "youtube": "site:youtube.com",
    }

    platform_list = [p.strip().lower() for p in platforms.split(",")]
    site_filters = []
    for p in platform_list:
        if p in platform_map:
            site_filters.append(platform_map[p])

    if not site_filters:
        # Fallback: search all known platforms
        site_filters = list(platform_map.values())

    query = f"{keyword} ({' OR '.join(site_filters)})"

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=20, region="wt-wt"))

    # Categorize results by platform
    categorized = []
    for r in results:
        href = r.get("href", "")
        platform = "other"
        for p_name, _ in platform_map.items():
            if p_name in href or (p_name == "twitter" and "x.com" in href):
                platform = p_name
                break
        categorized.append({
            "title": r.get("title", ""),
            "url": href,
            "snippet": r.get("body", ""),
            "platform": platform,
        })

    return categorized


async def handle_social_monitor(
    keyword: str,
    platforms: str = "twitter,reddit,linkedin",
) -> ToolResult:
    """Theo doi de cap thuong hieu/tu khoa tren mang xa hoi."""
    start = time.monotonic()

    if not keyword or not keyword.strip():
        return ToolResult(success=False, output="", error="Tu khoa theo doi khong duoc de trong")

    try:
        results = await asyncio.to_thread(_sync_social_monitor, keyword, platforms)
        elapsed = int((time.monotonic() - start) * 1000)

        if not results:
            return ToolResult(
                success=True,
                output=f"Khong tim thay de cap nao cho '{keyword}' tren {platforms}",
                execution_time_ms=elapsed,
                data={"results": [], "count": 0},
            )

        # Group by platform
        by_platform: dict[str, list[dict]] = {}
        for r in results:
            p = r["platform"]
            by_platform.setdefault(p, []).append(r)

        lines = [f"Ket qua theo doi '{keyword}' tren mang xa hoi ({len(results)} de cap)\n"]

        for platform, items in by_platform.items():
            lines.append(f"--- {platform.upper()} ({len(items)} ket qua) ---")
            for i, item in enumerate(items, 1):
                lines.append(f"  {i}. {item['title'][:120]}")
                lines.append(f"     {item['url']}")
                lines.append(f"     {item['snippet'][:150]}\n")

        log.info("social_monitor_success", keyword=keyword, count=len(results))
        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "results": results,
                "count": len(results),
                "by_platform": {k: len(v) for k, v in by_platform.items()},
            },
        )

    except ImportError:
        return ToolResult(
            success=False, output="",
            error="duckduckgo-search chua duoc cai dat. Chay: pip install duckduckgo-search",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("social_monitor_error", keyword=keyword, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Loi theo doi mang xa hoi: {e}",
            execution_time_ms=elapsed,
        )


# ===========================================================================
# 6. social_analytics — Social presence summary
# ===========================================================================

def _sync_social_analytics(brand: str) -> dict:
    """Aggregate social presence data for a brand/topic using DuckDuckGo."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    platforms = {
        "twitter": f'"{brand}" site:twitter.com OR site:x.com',
        "reddit": f'"{brand}" site:reddit.com',
        "linkedin": f'"{brand}" site:linkedin.com',
        "facebook": f'"{brand}" site:facebook.com',
        "youtube": f'"{brand}" site:youtube.com',
        "news": f'"{brand}" news',
    }

    analytics: dict = {}
    with DDGS() as ddgs:
        for platform, query in platforms.items():
            try:
                results = list(ddgs.text(query, max_results=5, region="wt-wt"))
                analytics[platform] = {
                    "mention_count": len(results),
                    "top_results": [
                        {
                            "title": r.get("title", ""),
                            "url": r.get("href", ""),
                            "snippet": r.get("body", "")[:200],
                        }
                        for r in results[:3]
                    ],
                }
            except Exception:
                analytics[platform] = {"mention_count": 0, "top_results": []}

    return analytics


async def handle_social_analytics(brand: str) -> ToolResult:
    """Tong hop su hien dien cua thuong hieu/chu de tren mang xa hoi."""
    start = time.monotonic()

    if not brand or not brand.strip():
        return ToolResult(success=False, output="", error="Ten thuong hieu khong duoc de trong")

    try:
        analytics = await asyncio.to_thread(_sync_social_analytics, brand)
        elapsed = int((time.monotonic() - start) * 1000)

        total_mentions = sum(p["mention_count"] for p in analytics.values())

        lines = [
            f"Bao cao hien dien mang xa hoi: '{brand}'",
            f"Tong so de cap tim thay: {total_mentions}\n",
        ]

        for platform, data in analytics.items():
            count = data["mention_count"]
            indicator = "***" if count >= 4 else "**" if count >= 2 else "*" if count >= 1 else "-"
            lines.append(f"{indicator} {platform.upper()}: {count} de cap")
            for r in data.get("top_results", []):
                lines.append(f"    - {r['title'][:100]}")
                lines.append(f"      {r['url']}")
            lines.append("")

        log.info("social_analytics_success", brand=brand, total=total_mentions)
        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "brand": brand,
                "total_mentions": total_mentions,
                "platforms": analytics,
            },
        )

    except ImportError:
        return ToolResult(
            success=False, output="",
            error="duckduckgo-search chua duoc cai dat. Chay: pip install duckduckgo-search",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("social_analytics_error", brand=brand, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Loi phan tich mang xa hoi: {e}",
            execution_time_ms=elapsed,
        )


# ===========================================================================
# Tool Definitions — exported at module level
# ===========================================================================

twitter_post_tool = ToolDefinition(
    name="twitter_post",
    description=(
        "Dang tweet len Twitter/X. Ho tro reply vao tweet khac. "
        "Toi da 280 ky tu. Can cau hinh TWITTER_API_KEY, TWITTER_API_SECRET, "
        "TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET."
    ),
    parameters=[
        ToolParameter(
            name="content",
            type="string",
            description="Noi dung tweet (toi da 280 ky tu)",
        ),
        ToolParameter(
            name="reply_to",
            type="string",
            description="ID cua tweet can reply (bo trong neu dang tweet moi)",
            required=False,
            default="",
        ),
    ],
    handler=handle_twitter_post,
    timeout_seconds=30,
)

twitter_search_tool = ToolDefinition(
    name="twitter_search",
    description=(
        "Tim kiem tweet tren Twitter/X theo tu khoa. "
        "Tra ve danh sach tweet voi so lieu tuong tac (likes, retweets, replies)."
    ),
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Tu khoa hoac cau truy van tim kiem tweet",
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="So ket qua toi da (mac dinh 10, toi da 100)",
            required=False,
            default=10,
        ),
    ],
    handler=handle_twitter_search,
    timeout_seconds=30,
)

telegram_send_tool = ToolDefinition(
    name="telegram_send",
    description=(
        "Gui tin nhan Telegram toi bat ky chat/group/channel nao. "
        "Ho tro HTML va Markdown formatting. Can cau hinh TELEGRAM_BOT_TOKEN."
    ),
    parameters=[
        ToolParameter(
            name="chat_id",
            type="string",
            description="ID cua chat/group/channel Telegram",
        ),
        ToolParameter(
            name="text",
            type="string",
            description="Noi dung tin nhan can gui",
        ),
        ToolParameter(
            name="parse_mode",
            type="string",
            description="Che do format: HTML hoac Markdown (bo trong neu khong can)",
            required=False,
            default="",
            enum=["HTML", "Markdown"],
        ),
    ],
    handler=handle_telegram_send,
    timeout_seconds=15,
)

email_send_tool = ToolDefinition(
    name="email_send",
    description=(
        "Gui email qua SMTP. Ho tro plain text va HTML. "
        "Can cau hinh SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM. "
        "Yeu cau xac nhan truoc khi gui de dam bao an toan."
    ),
    parameters=[
        ToolParameter(
            name="to",
            type="string",
            description="Dia chi email nguoi nhan",
        ),
        ToolParameter(
            name="subject",
            type="string",
            description="Tieu de email",
        ),
        ToolParameter(
            name="body",
            type="string",
            description="Noi dung email (plain text hoac HTML)",
        ),
        ToolParameter(
            name="html",
            type="boolean",
            description="Gui dang HTML thay vi plain text (mac dinh: false)",
            required=False,
            default=False,
        ),
    ],
    handler=handle_email_send,
    timeout_seconds=30,
    requires_confirmation=True,
)

social_monitor_tool = ToolDefinition(
    name="social_monitor",
    description=(
        "Theo doi de cap thuong hieu/tu khoa tren mang xa hoi. "
        "Tim kiem tren Twitter, Reddit, LinkedIn, Facebook, YouTube. "
        "Khong can API key — su dung DuckDuckGo."
    ),
    parameters=[
        ToolParameter(
            name="keyword",
            type="string",
            description="Tu khoa hoac ten thuong hieu can theo doi",
        ),
        ToolParameter(
            name="platforms",
            type="string",
            description="Danh sach platform, phan cach bang dau phay (mac dinh: twitter,reddit,linkedin)",
            required=False,
            default="twitter,reddit,linkedin",
        ),
    ],
    handler=handle_social_monitor,
    timeout_seconds=30,
)

social_analytics_tool = ToolDefinition(
    name="social_analytics",
    description=(
        "Tong hop su hien dien cua thuong hieu/chu de tren mang xa hoi. "
        "Quet qua Twitter, Reddit, LinkedIn, Facebook, YouTube va tin tuc. "
        "Tra ve bao cao tong quan voi so lieu de cap tren tung nen tang."
    ),
    parameters=[
        ToolParameter(
            name="brand",
            type="string",
            description="Ten thuong hieu hoac chu de can phan tich",
        ),
    ],
    handler=handle_social_analytics,
    timeout_seconds=60,
)
