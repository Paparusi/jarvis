"""Crypto & Hacking Utility Tools — Encoding, hashing, JWT, network recon.

A collection of utility tools for security research, CTF, and general
development workflows. All tools use standard library where possible.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import math
import re
import secrets
import socket
import ssl
import string
import time
import urllib.parse
from datetime import datetime, timezone

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.crypto_utils")


# ---------------------------------------------------------------------------
# 1. Base64 encode/decode
# ---------------------------------------------------------------------------

async def base64_encode_decode(text: str, action: str = "encode") -> ToolResult:
    """Encode or decode a string using Base64."""
    start = time.monotonic()

    action = action.lower()
    if action not in ("encode", "decode"):
        return ToolResult(success=False, output="", error="action must be 'encode' or 'decode'")

    if not text:
        return ToolResult(success=False, output="", error="text must not be empty")

    try:
        if action == "encode":
            result = base64.b64encode(text.encode("utf-8")).decode("ascii")
        else:
            # Try standard base64, then URL-safe
            try:
                result = base64.b64decode(text, validate=True).decode("utf-8", errors="replace")
            except Exception:
                result = base64.urlsafe_b64decode(text + "==").decode("utf-8", errors="replace")

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Action: {action}\nInput: {text[:200]}\nResult: {result}",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Base64 {action} failed: {e}")


# ---------------------------------------------------------------------------
# 2. Hash tool
# ---------------------------------------------------------------------------

async def hash_text(text: str, algorithm: str = "sha256") -> ToolResult:
    """Compute hash of text using the specified algorithm."""
    start = time.monotonic()

    algorithm = algorithm.lower()
    supported = {"md5", "sha1", "sha256", "sha512"}
    if algorithm not in supported:
        return ToolResult(
            success=False, output="",
            error=f"Unsupported algorithm '{algorithm}'. Use: {', '.join(sorted(supported))}",
        )

    if not text:
        return ToolResult(success=False, output="", error="text must not be empty")

    try:
        h = hashlib.new(algorithm)
        h.update(text.encode("utf-8"))
        digest = h.hexdigest()
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Algorithm: {algorithm.upper()}\nInput: {text[:200]}\nHash: {digest}",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Hash failed: {e}")


# ---------------------------------------------------------------------------
# 3. URL encode/decode
# ---------------------------------------------------------------------------

async def url_encode_decode(text: str, action: str = "encode") -> ToolResult:
    """URL-encode or URL-decode a string."""
    start = time.monotonic()

    action = action.lower()
    if action not in ("encode", "decode"):
        return ToolResult(success=False, output="", error="action must be 'encode' or 'decode'")

    if not text:
        return ToolResult(success=False, output="", error="text must not be empty")

    try:
        if action == "encode":
            result = urllib.parse.quote(text, safe="")
        else:
            result = urllib.parse.unquote(text)

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Action: {action}\nInput: {text[:200]}\nResult: {result}",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"URL {action} failed: {e}")


# ---------------------------------------------------------------------------
# 4. JWT decode (no verification)
# ---------------------------------------------------------------------------

def _b64url_decode(data: str) -> bytes:
    """Decode base64url without padding."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


async def jwt_decode(token: str) -> ToolResult:
    """Decode a JWT token into header and payload (no signature verification)."""
    start = time.monotonic()

    if not token:
        return ToolResult(success=False, output="", error="token must not be empty")

    parts = token.strip().split(".")
    if len(parts) not in (3, 5):  # 3 = JWS, 5 = JWE
        return ToolResult(
            success=False, output="",
            error=f"Invalid JWT format: expected 3 parts (got {len(parts)})",
        )

    try:
        header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))

        # Check common time fields
        notes = []
        now = time.time()
        if "exp" in payload:
            exp_dt = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
            if payload["exp"] < now:
                notes.append(f"EXPIRED at {exp_dt.isoformat()}")
            else:
                notes.append(f"Expires: {exp_dt.isoformat()}")
        if "iat" in payload:
            iat_dt = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
            notes.append(f"Issued: {iat_dt.isoformat()}")
        if "nbf" in payload:
            nbf_dt = datetime.fromtimestamp(payload["nbf"], tz=timezone.utc)
            notes.append(f"Not before: {nbf_dt.isoformat()}")

        elapsed = int((time.monotonic() - start) * 1000)
        output_lines = [
            "=== JWT Header ===",
            json.dumps(header, indent=2, ensure_ascii=False),
            "",
            "=== JWT Payload ===",
            json.dumps(payload, indent=2, ensure_ascii=False),
        ]
        if notes:
            output_lines += ["", "=== Notes ==="] + notes

        return ToolResult(
            success=True,
            output="\n".join(output_lines),
            execution_time_ms=elapsed,
            data={"header": header, "payload": payload},
        )
    except json.JSONDecodeError as e:
        return ToolResult(success=False, output="", error=f"Failed to parse JWT JSON: {e}")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"JWT decode failed: {e}")


# ---------------------------------------------------------------------------
# 5. Hex convert (text <-> hex)
# ---------------------------------------------------------------------------

async def hex_convert(text: str, action: str = "to_hex") -> ToolResult:
    """Convert text to hex or hex to text."""
    start = time.monotonic()

    action = action.lower()
    if action not in ("to_hex", "from_hex"):
        return ToolResult(
            success=False, output="",
            error="action must be 'to_hex' or 'from_hex'",
        )

    if not text:
        return ToolResult(success=False, output="", error="text must not be empty")

    try:
        if action == "to_hex":
            result = text.encode("utf-8").hex()
            # Format with spaces every 2 chars for readability
            spaced = " ".join(result[i:i + 2] for i in range(0, len(result), 2))
            output = f"Text -> Hex\nInput: {text[:200]}\nHex: {result}\nFormatted: {spaced}"
        else:
            # Strip spaces, 0x prefix, common separators
            cleaned = text.replace(" ", "").replace("0x", "").replace(":", "").replace("-", "")
            if not all(c in "0123456789abcdefABCDEF" for c in cleaned):
                return ToolResult(success=False, output="", error="Invalid hex characters in input")
            if len(cleaned) % 2 != 0:
                return ToolResult(success=False, output="", error="Hex string must have even length")
            result = bytes.fromhex(cleaned).decode("utf-8", errors="replace")
            output = f"Hex -> Text\nInput: {text[:200]}\nText: {result}"

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=True, output=output, execution_time_ms=elapsed)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Hex convert failed: {e}")


# ---------------------------------------------------------------------------
# 6. Regex test
# ---------------------------------------------------------------------------

async def regex_test(pattern: str, text: str, flags: str = "") -> ToolResult:
    """Test a regex pattern against text and show all matches."""
    start = time.monotonic()

    if not pattern:
        return ToolResult(success=False, output="", error="pattern must not be empty")
    if not text:
        return ToolResult(success=False, output="", error="text must not be empty")

    # Parse flags
    re_flags = 0
    flag_map = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}
    for f in flags:
        if f in flag_map:
            re_flags |= flag_map[f]

    try:
        compiled = re.compile(pattern, re_flags)
    except re.error as e:
        return ToolResult(success=False, output="", error=f"Invalid regex: {e}")

    matches = list(compiled.finditer(text))
    elapsed = int((time.monotonic() - start) * 1000)

    if not matches:
        return ToolResult(
            success=True,
            output=f"Pattern: /{pattern}/{flags}\nMatches: 0\nNo matches found.",
            execution_time_ms=elapsed,
        )

    lines = [f"Pattern: /{pattern}/{flags}", f"Matches: {len(matches)}", ""]
    for i, m in enumerate(matches[:50]):  # Limit to 50 matches
        line = f"  [{i}] pos {m.start()}-{m.end()}: {m.group()!r}"
        if m.groups():
            line += f"  groups={m.groups()}"
        if m.groupdict():
            line += f"  named={m.groupdict()}"
        lines.append(line)

    if len(matches) > 50:
        lines.append(f"\n  ... and {len(matches) - 50} more matches")

    return ToolResult(success=True, output="\n".join(lines), execution_time_ms=elapsed)


# ---------------------------------------------------------------------------
# 7. Timestamp convert
# ---------------------------------------------------------------------------

async def timestamp_convert(value: str, action: str = "to_human") -> ToolResult:
    """Convert between unix timestamp and human-readable datetime."""
    start = time.monotonic()

    action = action.lower()
    if action not in ("to_human", "to_unix"):
        return ToolResult(
            success=False, output="",
            error="action must be 'to_human' or 'to_unix'",
        )

    if not value:
        return ToolResult(success=False, output="", error="value must not be empty")

    try:
        if action == "to_human":
            ts = float(value)
            # Detect milliseconds vs seconds
            if ts > 1e12:
                ts = ts / 1000.0
                note = " (detected as milliseconds)"
            else:
                note = ""

            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
            dt_local = datetime.fromtimestamp(ts)
            output = (
                f"Timestamp: {value}{note}\n"
                f"UTC:   {dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                f"Local: {dt_local.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"ISO:   {dt_utc.isoformat()}"
            )
        else:
            # Try various datetime formats
            dt = None
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M:%S",
            ]
            for fmt in formats:
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    break
                except ValueError:
                    continue

            if dt is None:
                return ToolResult(
                    success=False, output="",
                    error=f"Could not parse datetime: '{value}'. Try YYYY-MM-DD HH:MM:SS format.",
                )

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            output = (
                f"Datetime: {value}\n"
                f"Unix (seconds): {int(ts)}\n"
                f"Unix (milliseconds): {int(ts * 1000)}"
            )

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=True, output=output, execution_time_ms=elapsed)
    except (ValueError, OverflowError, OSError) as e:
        return ToolResult(success=False, output="", error=f"Timestamp conversion failed: {e}")


# ---------------------------------------------------------------------------
# 8. IP info (geolocation via ip-api.com)
# ---------------------------------------------------------------------------

async def ip_info(ip: str) -> ToolResult:
    """Get geolocation and network info for an IP address."""
    start = time.monotonic()

    if not ip:
        return ToolResult(success=False, output="", error="ip must not be empty")

    # Basic validation
    ip = ip.strip()
    if any(c in ip for c in [";", "&", "|", "`", "$"]):
        return ToolResult(success=False, output="", error="Invalid IP address")

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=66846719")

        data = resp.json()
        elapsed = int((time.monotonic() - start) * 1000)

        if data.get("status") == "fail":
            return ToolResult(
                success=False, output="",
                error=f"IP lookup failed: {data.get('message', 'unknown')}",
            )

        lines = [f"IP Info: {ip}", ""]
        field_map = [
            ("country", "Country"),
            ("regionName", "Region"),
            ("city", "City"),
            ("zip", "ZIP"),
            ("lat", "Latitude"),
            ("lon", "Longitude"),
            ("timezone", "Timezone"),
            ("isp", "ISP"),
            ("org", "Organization"),
            ("as", "AS"),
            ("reverse", "Reverse DNS"),
            ("mobile", "Mobile"),
            ("proxy", "Proxy/VPN"),
            ("hosting", "Hosting"),
        ]
        for key, label in field_map:
            if key in data and data[key] not in (None, ""):
                lines.append(f"  {label}: {data[key]}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data=data,
        )
    except httpx.TimeoutException:
        return ToolResult(success=False, output="", error="IP info request timed out")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"IP info failed: {e}")


# ---------------------------------------------------------------------------
# 9. WHOIS lookup
# ---------------------------------------------------------------------------

async def whois_lookup(domain: str) -> ToolResult:
    """Run a WHOIS lookup on a domain."""
    start = time.monotonic()

    if not domain:
        return ToolResult(success=False, output="", error="domain must not be empty")

    # Sanitize
    domain = domain.strip().lower()
    if any(c in domain for c in [";", "&", "|", "`", "$", "(", ")", " "]):
        return ToolResult(success=False, output="", error="Invalid domain name")

    try:
        proc = await asyncio.create_subprocess_exec(
            "whois", domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        elapsed = int((time.monotonic() - start) * 1000)

        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")

        if proc.returncode == 127 or "not found" in stderr_text.lower():
            return ToolResult(
                success=False, output="",
                error="whois command not found or domain lookup failed",
            )

        output = stdout_text[:5000] if stdout_text else stderr_text[:2000]
        if len(stdout_text) > 5000:
            output += "\n...[truncated]"

        return ToolResult(
            success=proc.returncode == 0,
            output=output,
            execution_time_ms=elapsed,
        )
    except FileNotFoundError:
        return ToolResult(success=False, output="", error="whois command not installed")
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error="WHOIS lookup timed out after 15s")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"WHOIS failed: {e}")


# ---------------------------------------------------------------------------
# 10. SSL certificate check
# ---------------------------------------------------------------------------

async def ssl_check(hostname: str, port: int = 443) -> ToolResult:
    """Check SSL/TLS certificate info for a hostname."""
    start = time.monotonic()

    if not hostname:
        return ToolResult(success=False, output="", error="hostname must not be empty")

    hostname = hostname.strip().lower()
    # Strip protocol prefix if provided
    if hostname.startswith("https://"):
        hostname = hostname[8:]
    if hostname.startswith("http://"):
        hostname = hostname[7:]
    # Strip path
    hostname = hostname.split("/")[0]
    # Strip port from hostname if embedded
    if ":" in hostname:
        parts = hostname.rsplit(":", 1)
        hostname = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            pass

    if any(c in hostname for c in [";", "&", "|", "`", "$"]):
        return ToolResult(success=False, output="", error="Invalid hostname")

    try:
        # Run in executor to avoid blocking
        def _get_cert():
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
                s.settimeout(10)
                s.connect((hostname, port))
                cert = s.getpeercert()
                cipher = s.cipher()
                version = s.version()
                return cert, cipher, version

        loop = asyncio.get_event_loop()
        cert, cipher, tls_version = await asyncio.wait_for(
            loop.run_in_executor(None, _get_cert),
            timeout=15,
        )

        elapsed = int((time.monotonic() - start) * 1000)

        # Parse cert fields
        subject = dict(x[0] for x in cert.get("subject", ()))
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        not_before = cert.get("notBefore", "")
        not_after = cert.get("notAfter", "")
        serial = cert.get("serialNumber", "")

        # SANs
        san_list = []
        for san_type, san_value in cert.get("subjectAltName", ()):
            san_list.append(f"{san_type}:{san_value}")

        # Check expiry
        expiry_note = ""
        if not_after:
            try:
                exp_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                days_left = (exp_dt - datetime.now(timezone.utc).replace(tzinfo=None)).days
                if days_left < 0:
                    expiry_note = f"  *** EXPIRED {abs(days_left)} days ago ***"
                elif days_left < 30:
                    expiry_note = f"  *** EXPIRES in {days_left} days ***"
                else:
                    expiry_note = f"  ({days_left} days remaining)"
            except ValueError:
                pass

        lines = [
            f"SSL Certificate: {hostname}:{port}",
            "",
            f"  Subject:    {subject.get('commonName', 'N/A')}",
            f"  Issuer:     {issuer.get('organizationName', '')} ({issuer.get('commonName', '')})",
            f"  Serial:     {serial}",
            f"  Not Before: {not_before}",
            f"  Not After:  {not_after}{expiry_note}",
            f"  TLS:        {tls_version}",
            f"  Cipher:     {cipher[0] if cipher else 'N/A'} ({cipher[2] if cipher else '?'}-bit)",
        ]
        if san_list:
            lines.append(f"  SANs ({len(san_list)}):")
            for san in san_list[:20]:
                lines.append(f"    - {san}")
            if len(san_list) > 20:
                lines.append(f"    ... and {len(san_list) - 20} more")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={"subject": subject, "issuer": issuer, "not_after": not_after},
        )
    except ssl.SSLCertVerificationError as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output=f"SSL verification failed: {e}",
            error=str(e), execution_time_ms=elapsed,
        )
    except (socket.gaierror, socket.timeout, ConnectionRefusedError) as e:
        return ToolResult(success=False, output="", error=f"Connection failed: {e}")
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error="SSL check timed out after 15s")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"SSL check failed: {e}")


# ---------------------------------------------------------------------------
# 11. Password generator
# ---------------------------------------------------------------------------

async def generate_password(
    length: int = 16,
    charset: str = "all",
    count: int = 1,
) -> ToolResult:
    """Generate cryptographically secure random passwords."""
    start = time.monotonic()

    # Validate
    if length < 4:
        return ToolResult(success=False, output="", error="Length must be at least 4")
    if length > 128:
        return ToolResult(success=False, output="", error="Length must be at most 128")
    count = min(max(1, count), 20)

    # Build charset
    charset = charset.lower()
    charset_map = {
        "all": string.ascii_letters + string.digits + string.punctuation,
        "alphanumeric": string.ascii_letters + string.digits,
        "alpha": string.ascii_letters,
        "digits": string.digits,
        "hex": string.hexdigits[:16],
        "safe": string.ascii_letters + string.digits + "!@#$%^&*",
    }

    chars = charset_map.get(charset)
    if chars is None:
        return ToolResult(
            success=False, output="",
            error=f"Unknown charset '{charset}'. Use: {', '.join(sorted(charset_map))}",
        )

    passwords = []
    for _ in range(count):
        pw = "".join(secrets.choice(chars) for _ in range(length))
        passwords.append(pw)

    elapsed = int((time.monotonic() - start) * 1000)

    # Compute entropy
    entropy = math.log2(len(chars)) * length

    lines = [
        f"Generated {count} password(s) (length={length}, charset={charset})",
        f"Entropy: ~{entropy:.1f} bits",
        "",
    ]
    for i, pw in enumerate(passwords):
        lines.append(f"  [{i + 1}] {pw}")

    return ToolResult(success=True, output="\n".join(lines), execution_time_ms=elapsed)


# ---------------------------------------------------------------------------
# 12. CIDR calculator
# ---------------------------------------------------------------------------

async def cidr_calc(cidr: str) -> ToolResult:
    """Calculate CIDR network range details."""
    start = time.monotonic()

    if not cidr:
        return ToolResult(success=False, output="", error="cidr must not be empty")

    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return ToolResult(success=False, output="", error=f"Invalid CIDR: {e}")

    elapsed = int((time.monotonic() - start) * 1000)

    if isinstance(network, ipaddress.IPv4Network):
        num_hosts = max(0, network.num_addresses - 2) if network.prefixlen < 31 else network.num_addresses
        lines = [
            f"CIDR: {cidr}",
            f"Network (normalized): {network}",
            "",
            f"  Network Address: {network.network_address}",
            f"  Broadcast:       {network.broadcast_address}",
            f"  Netmask:         {network.netmask}",
            f"  Wildcard:        {network.hostmask}",
            f"  Prefix Length:   /{network.prefixlen}",
            f"  Total Addresses: {network.num_addresses}",
            f"  Usable Hosts:    {num_hosts}",
            f"  First Host:      {network.network_address + 1 if num_hosts > 0 else 'N/A'}",
            f"  Last Host:       {network.broadcast_address - 1 if num_hosts > 0 else 'N/A'}",
            f"  Is Private:      {network.is_private}",
        ]
    else:
        # IPv6
        lines = [
            f"CIDR: {cidr}",
            f"Network (normalized): {network}",
            "",
            f"  Network Address: {network.network_address}",
            f"  Prefix Length:   /{network.prefixlen}",
            f"  Total Addresses: {network.num_addresses}",
            f"  Is Private:      {network.is_private}",
        ]

    return ToolResult(success=True, output="\n".join(lines), execution_time_ms=elapsed)


# ===========================================================================
# Tool Definitions
# ===========================================================================

base64_tool = ToolDefinition(
    name="base64",
    description="Encode or decode Base64 strings. Useful for encoding data, decoding tokens, and CTF challenges.",
    parameters=[
        ToolParameter(name="text", type="string", description="Text to encode or Base64 string to decode"),
        ToolParameter(
            name="action", type="string", description="'encode' or 'decode'",
            required=False, default="encode",
            enum=["encode", "decode"],
        ),
    ],
    handler=base64_encode_decode,
)

hash_tool = ToolDefinition(
    name="hash",
    description="Compute hash (MD5, SHA1, SHA256, SHA512) of a text string.",
    parameters=[
        ToolParameter(name="text", type="string", description="Text to hash"),
        ToolParameter(
            name="algorithm", type="string", description="Hash algorithm to use",
            required=False, default="sha256",
            enum=["md5", "sha1", "sha256", "sha512"],
        ),
    ],
    handler=hash_text,
)

url_encode_tool = ToolDefinition(
    name="url_encode",
    description="URL-encode or URL-decode a string. Useful for web security testing and parameter manipulation.",
    parameters=[
        ToolParameter(name="text", type="string", description="Text to encode or URL-encoded string to decode"),
        ToolParameter(
            name="action", type="string", description="'encode' or 'decode'",
            required=False, default="encode",
            enum=["encode", "decode"],
        ),
    ],
    handler=url_encode_decode,
)

jwt_decode_tool = ToolDefinition(
    name="jwt_decode",
    description="Decode a JWT token into header and payload (no signature verification). Shows expiry status.",
    parameters=[
        ToolParameter(name="token", type="string", description="JWT token string to decode"),
    ],
    handler=jwt_decode,
)

hex_convert_tool = ToolDefinition(
    name="hex_convert",
    description="Convert text to hex or hex to text. Supports various hex formats (with/without spaces, 0x prefix).",
    parameters=[
        ToolParameter(name="text", type="string", description="Text or hex string to convert"),
        ToolParameter(
            name="action", type="string", description="'to_hex' (text->hex) or 'from_hex' (hex->text)",
            required=False, default="to_hex",
            enum=["to_hex", "from_hex"],
        ),
    ],
    handler=hex_convert,
)

regex_test_tool = ToolDefinition(
    name="regex_test",
    description="Test a regex pattern against text. Shows all matches with positions and capture groups.",
    parameters=[
        ToolParameter(name="pattern", type="string", description="Regex pattern to test"),
        ToolParameter(name="text", type="string", description="Text to match against"),
        ToolParameter(
            name="flags", type="string",
            description="Regex flags: i=ignorecase, m=multiline, s=dotall, x=verbose",
            required=False, default="",
        ),
    ],
    handler=regex_test,
)

timestamp_tool = ToolDefinition(
    name="timestamp",
    description="Convert between unix timestamp and human-readable datetime. Auto-detects seconds vs milliseconds.",
    parameters=[
        ToolParameter(
            name="value", type="string",
            description="Unix timestamp (e.g. '1709654400') or datetime string (e.g. '2024-03-05 12:00:00')",
        ),
        ToolParameter(
            name="action", type="string", description="'to_human' or 'to_unix'",
            required=False, default="to_human",
            enum=["to_human", "to_unix"],
        ),
    ],
    handler=timestamp_convert,
)

ip_info_tool = ToolDefinition(
    name="ip_info",
    description="Get geolocation, ISP, and network info for an IP address using ip-api.com.",
    parameters=[
        ToolParameter(name="ip", type="string", description="IP address to lookup (IPv4 or IPv6)"),
    ],
    handler=ip_info,
    timeout_seconds=15,
)

whois_tool = ToolDefinition(
    name="whois",
    description="Run WHOIS lookup on a domain. Shows registrar, creation date, expiry, nameservers.",
    parameters=[
        ToolParameter(name="domain", type="string", description="Domain name to lookup (e.g. 'example.com')"),
    ],
    handler=whois_lookup,
    timeout_seconds=20,
)

ssl_check_tool = ToolDefinition(
    name="ssl_check",
    description="Check SSL/TLS certificate info — expiry, issuer, SANs, cipher. Detects expired certs.",
    parameters=[
        ToolParameter(name="hostname", type="string", description="Hostname to check (e.g. 'google.com')"),
        ToolParameter(
            name="port", type="integer", description="Port (default 443)",
            required=False, default=443,
        ),
    ],
    handler=ssl_check,
    timeout_seconds=20,
)

generate_password_tool = ToolDefinition(
    name="generate_password",
    description="Generate cryptographically secure random passwords with configurable length and charset.",
    parameters=[
        ToolParameter(
            name="length", type="integer", description="Password length (4-128)",
            required=False, default=16,
        ),
        ToolParameter(
            name="charset", type="string",
            description="Character set to use",
            required=False, default="all",
            enum=["all", "alphanumeric", "alpha", "digits", "hex", "safe"],
        ),
        ToolParameter(
            name="count", type="integer", description="Number of passwords to generate (1-20)",
            required=False, default=1,
        ),
    ],
    handler=generate_password,
)

cidr_calc_tool = ToolDefinition(
    name="cidr_calc",
    description="Calculate CIDR network range — network address, broadcast, netmask, usable hosts.",
    parameters=[
        ToolParameter(
            name="cidr", type="string",
            description="CIDR notation (e.g. '192.168.1.0/24' or '10.0.0.0/8')",
        ),
    ],
    handler=cidr_calc,
)
