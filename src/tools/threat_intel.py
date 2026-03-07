"""Threat Intelligence Tools — Tra cuu thong tin de doa an ninh mang.

Provides VirusTotal lookup, AbuseIPDB reputation check, malware hash
analysis (MalwareBazaar/ThreatFox/URLhaus), and Shodan service discovery.
Uses free API alternatives when API keys are not configured.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
import socket
import time

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.threat_intel")

_TIMEOUT = 20  # seconds
_USER_AGENT = "JARVIS/2.0"
_DANGEROUS_CHARS = [";", "&", "|", "`", "$", "(", ")"]

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal", "169.254.169.254",
}


def _validate_input(value: str, label: str) -> str | None:
    """Validate input against injection characters. Returns error message or None."""
    if not value or not value.strip():
        return f"{label} must not be empty"
    if any(c in value for c in _DANGEROUS_CHARS):
        return f"Invalid {label}: contains disallowed characters"
    return None


def _validate_ip(ip: str) -> str | None:
    """Validate IP address format. Returns error message or None."""
    err = _validate_input(ip, "IP address")
    if err:
        return err
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
        parts = ip.split(".")
        if all(0 <= int(p) <= 255 for p in parts):
            return None
    if ":" in ip and re.match(r"^[0-9a-fA-F:]+$", ip):
        return None
    return f"Invalid IP address format: {ip}"


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is private/loopback/link-local."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _ssrf_check_ip(ip_str: str) -> str | None:
    """Block SSRF against private IPs. Returns error or None."""
    if ip_str.lower() in _BLOCKED_HOSTS:
        return "Blocked: internal/private host"
    if _is_private_ip(ip_str):
        return "Blocked: private/loopback IP address"
    return None


def _ssrf_check_domain(domain: str) -> str | None:
    """Block SSRF for domain names that resolve to private IPs. Returns error or None."""
    if domain.lower() in _BLOCKED_HOSTS:
        return "Blocked: internal/private host"
    try:
        resolved = socket.getaddrinfo(domain, None, socket.AF_UNSPEC)
        for _, _, _, _, addr in resolved:
            if _is_private_ip(addr[0]):
                return "Blocked: domain resolves to private IP"
    except (socket.gaierror, OSError):
        pass
    return None


def _validate_hash(hash_value: str) -> str | None:
    """Validate a hash string (MD5, SHA1, or SHA256). Returns error or None."""
    err = _validate_input(hash_value, "hash")
    if err:
        return err
    cleaned = hash_value.strip().lower()
    if not re.match(r"^[a-f0-9]+$", cleaned):
        return f"Invalid hash format: must be hexadecimal"
    if len(cleaned) not in (32, 40, 64):
        return f"Invalid hash length ({len(cleaned)}): expected 32 (MD5), 40 (SHA1), or 64 (SHA256)"
    return None


def _detect_indicator_type(indicator: str) -> str:
    """Auto-detect the type of a threat indicator."""
    cleaned = indicator.strip()

    # Hash: 32 (MD5), 40 (SHA1), or 64 (SHA256) hex chars
    if re.match(r"^[a-fA-F0-9]+$", cleaned) and len(cleaned) in (32, 40, 64):
        return "hash"

    # URL: starts with http:// or https://
    if cleaned.startswith(("http://", "https://")):
        return "url"

    # IP: dotted decimal
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", cleaned):
        return "ip"

    # Default: domain
    return "domain"


# ---------------------------------------------------------------------------
# 1. VirusTotal Lookup
# ---------------------------------------------------------------------------

async def virustotal_lookup(
    indicator: str,
    indicator_type: str = "auto",
) -> ToolResult:
    """Check a file hash, URL, IP, or domain against VirusTotal.

    Uses the VirusTotal v3 API. Requires VIRUSTOTAL_API_KEY environment
    variable (free tier available at virustotal.com).
    """
    start = time.monotonic()

    err = _validate_input(indicator, "indicator")
    if err:
        return ToolResult(success=False, output="", error=err)

    api_key = os.environ.get("VIRUSTOTAL_API_KEY", "")
    if not api_key:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False,
            output=(
                "VIRUSTOTAL_API_KEY chua duoc cau hinh.\n"
                "Dang ky API key mien phi tai: https://www.virustotal.com/gui/join-us\n"
                "Sau do them vao .env: VIRUSTOTAL_API_KEY=your_key"
            ),
            error="Missing VIRUSTOTAL_API_KEY",
            execution_time_ms=elapsed,
        )

    cleaned = indicator.strip()

    if indicator_type == "auto":
        indicator_type = _detect_indicator_type(cleaned)

    # Build API URL based on indicator type
    base_url = "https://www.virustotal.com/api/v3"

    if indicator_type == "hash":
        err = _validate_hash(cleaned)
        if err:
            return ToolResult(success=False, output="", error=err)
        url = f"{base_url}/files/{cleaned.lower()}"

    elif indicator_type == "url":
        url_id = base64.urlsafe_b64encode(cleaned.encode()).decode().rstrip("=")
        url = f"{base_url}/urls/{url_id}"

    elif indicator_type == "ip":
        err = _validate_ip(cleaned)
        if err:
            return ToolResult(success=False, output="", error=err)
        ssrf_err = _ssrf_check_ip(cleaned)
        if ssrf_err:
            return ToolResult(success=False, output="", error=ssrf_err)
        url = f"{base_url}/ip_addresses/{cleaned}"

    elif indicator_type == "domain":
        ssrf_err = _ssrf_check_domain(cleaned)
        if ssrf_err:
            return ToolResult(success=False, output="", error=ssrf_err)
        url = f"{base_url}/domains/{cleaned}"

    else:
        return ToolResult(
            success=False, output="",
            error=f"Unknown indicator_type: {indicator_type}",
        )

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"x-apikey": api_key, "User-Agent": _USER_AGENT},
            )

        elapsed = int((time.monotonic() - start) * 1000)

        if resp.status_code == 404:
            return ToolResult(
                success=True,
                output=f"Khong tim thay ket qua cho {indicator_type}: {cleaned}",
                execution_time_ms=elapsed,
                data={"indicator": cleaned, "type": indicator_type, "found": False},
            )

        if resp.status_code == 429:
            return ToolResult(
                success=False, output="",
                error="VirusTotal API rate limit. Vui long doi va thu lai.",
                execution_time_ms=elapsed,
            )

        resp.raise_for_status()
        data = resp.json()
        attrs = data.get("data", {}).get("attributes", {})

        return _format_vt_result(cleaned, indicator_type, attrs, elapsed)

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"VirusTotal request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("virustotal_lookup_error", indicator=cleaned, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"VirusTotal lookup failed: {e}",
            execution_time_ms=elapsed,
        )


def _format_vt_result(
    indicator: str,
    indicator_type: str,
    attrs: dict,
    elapsed: int,
) -> ToolResult:
    """Format VirusTotal API response into a readable ToolResult."""
    stats = attrs.get("last_analysis_stats", {})
    malicious = stats.get("malicious", 0)
    suspicious = stats.get("suspicious", 0)
    undetected = stats.get("undetected", 0)
    harmless = stats.get("harmless", 0)
    total = malicious + suspicious + undetected + harmless
    reputation = attrs.get("reputation", "N/A")

    # Determine risk level
    if total == 0:
        risk = "UNKNOWN"
    elif malicious == 0 and suspicious == 0:
        risk = "CLEAN"
    elif malicious <= 3:
        risk = "LOW"
    elif malicious <= 10:
        risk = "MEDIUM"
    else:
        risk = "HIGH"

    # Collect top detection names
    results = attrs.get("last_analysis_results", {})
    detections = []
    for engine, result in results.items():
        if result.get("category") == "malicious" and result.get("result"):
            detections.append(f"{engine}: {result['result']}")
    detections = detections[:10]

    lines = [
        f"VirusTotal — {indicator_type.upper()}: {indicator}",
        f"Detection: {malicious}/{total} engines flagged as malicious",
        f"Suspicious: {suspicious} | Harmless: {harmless} | Undetected: {undetected}",
        f"Reputation score: {reputation}",
        f"Risk level: {risk}",
    ]

    if detections:
        lines.append("")
        lines.append(f"Top detections ({len(detections)}):")
        for det in detections:
            lines.append(f"  {det}")

    # Type-specific extra info
    if indicator_type == "hash":
        name = attrs.get("meaningful_name", attrs.get("names", [""])[0] if attrs.get("names") else "")
        file_type = attrs.get("type_description", "N/A")
        size = attrs.get("size", 0)
        if name:
            lines.append(f"\nFile: {name}")
        lines.append(f"Type: {file_type} | Size: {size} bytes")

    elif indicator_type == "domain":
        registrar = attrs.get("registrar", "N/A")
        creation = attrs.get("creation_date", "N/A")
        lines.append(f"\nRegistrar: {registrar} | Created: {creation}")

    elif indicator_type == "ip":
        country = attrs.get("country", "N/A")
        asn = attrs.get("asn", "N/A")
        as_owner = attrs.get("as_owner", "N/A")
        lines.append(f"\nCountry: {country} | ASN: {asn} ({as_owner})")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "indicator": indicator,
            "type": indicator_type,
            "found": True,
            "malicious": malicious,
            "total": total,
            "risk": risk,
            "reputation": reputation,
        },
    )


# ---------------------------------------------------------------------------
# 2. AbuseIPDB Check
# ---------------------------------------------------------------------------

async def abuseipdb_check(ip: str, max_age_days: int = 90) -> ToolResult:
    """Check an IP address reputation on AbuseIPDB.

    Uses AbuseIPDB v2 API if ABUSEIPDB_API_KEY is set. Falls back to
    getipintel.net free check otherwise.
    """
    start = time.monotonic()

    err = _validate_ip(ip)
    if err:
        return ToolResult(success=False, output="", error=err)

    ssrf_err = _ssrf_check_ip(ip)
    if ssrf_err:
        return ToolResult(success=False, output="", error=ssrf_err)

    max_age_days = max(1, min(max_age_days, 365))

    api_key = os.environ.get("ABUSEIPDB_API_KEY", "")

    if api_key:
        return await _abuseipdb_api_check(ip, max_age_days, api_key, start)

    return await _abuseipdb_free_check(ip, start)


async def _abuseipdb_api_check(
    ip: str,
    max_age_days: int,
    api_key: str,
    start: float,
) -> ToolResult:
    """Check IP using the AbuseIPDB v2 API."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={
                    "ipAddress": ip,
                    "maxAgeInDays": str(max_age_days),
                    "verbose": "true",
                },
                headers={
                    "Key": api_key,
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )

        elapsed = int((time.monotonic() - start) * 1000)

        if resp.status_code == 429:
            return ToolResult(
                success=False, output="",
                error="AbuseIPDB rate limit. Vui long doi va thu lai.",
                execution_time_ms=elapsed,
            )

        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", {})

        confidence = data.get("abuseConfidenceScore", 0)
        total_reports = data.get("totalReports", 0)
        country = data.get("countryCode", "N/A")
        isp = data.get("isp", "N/A")
        usage_type = data.get("usageType", "N/A")
        domain = data.get("domain", "N/A")
        is_whitelisted = data.get("isWhitelisted", False)
        last_reported = data.get("lastReportedAt", "N/A")

        # Risk assessment
        if confidence == 0:
            risk = "CLEAN"
        elif confidence <= 25:
            risk = "LOW"
        elif confidence <= 75:
            risk = "MEDIUM"
        else:
            risk = "HIGH"

        # Extract report categories
        reports = data.get("reports", [])
        categories = set()
        for report in reports[:20]:
            for cat in report.get("categories", []):
                categories.add(cat)

        category_names = _translate_abuseipdb_categories(categories)

        lines = [
            f"AbuseIPDB — IP: {ip}",
            f"Abuse confidence: {confidence}% | Risk: {risk}",
            f"Total reports: {total_reports} (last {max_age_days} days)",
            f"Country: {country} | ISP: {isp}",
            f"Usage type: {usage_type} | Domain: {domain}",
            f"Whitelisted: {is_whitelisted}",
            f"Last reported: {last_reported}",
        ]

        if category_names:
            lines.append(f"\nReport categories: {', '.join(sorted(category_names))}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "ip": ip,
                "confidence": confidence,
                "risk": risk,
                "total_reports": total_reports,
                "country": country,
                "isp": isp,
            },
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"AbuseIPDB request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("abuseipdb_check_error", ip=ip, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"AbuseIPDB check failed: {e}",
            execution_time_ms=elapsed,
        )


_ABUSEIPDB_CATEGORIES = {
    1: "DNS Compromise", 2: "DNS Poisoning", 3: "Fraud Orders",
    4: "DDoS Attack", 5: "FTP Brute-Force", 6: "Ping of Death",
    7: "Phishing", 8: "Fraud VoIP", 9: "Open Proxy",
    10: "Web Spam", 11: "Email Spam", 12: "Blog Spam",
    14: "Port Scan", 15: "Hacking", 16: "SQL Injection",
    17: "Spoofing", 18: "Brute-Force", 19: "Bad Web Bot",
    20: "Exploited Host", 21: "Web App Attack", 22: "SSH",
    23: "IoT Targeted",
}


def _translate_abuseipdb_categories(category_ids: set) -> list[str]:
    """Translate AbuseIPDB numeric category IDs to names."""
    names = []
    for cat_id in category_ids:
        name = _ABUSEIPDB_CATEGORIES.get(cat_id)
        if name:
            names.append(name)
        else:
            names.append(f"Category-{cat_id}")
    return names


async def _abuseipdb_free_check(ip: str, start: float) -> ToolResult:
    """Fallback: check IP using the free getipintel.net service."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                "https://check.getipintel.net/check.php",
                params={"ip": ip, "contact": "jarvis@example.com", "format": "json"},
                headers={"User-Agent": _USER_AGENT},
            )

        elapsed = int((time.monotonic() - start) * 1000)
        resp.raise_for_status()
        data = resp.json()

        probability = float(data.get("result", 0))

        if probability < 0:
            return ToolResult(
                success=False, output="",
                error=f"getipintel error: {data.get('message', 'unknown')}",
                execution_time_ms=elapsed,
            )

        # Risk assessment based on proxy/VPN probability
        if probability < 0.5:
            risk = "LOW"
        elif probability < 0.9:
            risk = "MEDIUM"
        else:
            risk = "HIGH"

        lines = [
            f"IP Check (getipintel.net, free fallback) — IP: {ip}",
            f"Proxy/VPN probability: {probability:.2%}",
            f"Risk level: {risk}",
            "",
            "Luu y: Day la ket qua tu nguon mien phi (getipintel.net).",
            "De co thong tin chi tiet hon, cau hinh ABUSEIPDB_API_KEY.",
        ]

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "ip": ip,
                "probability": probability,
                "risk": risk,
                "source": "getipintel.net",
            },
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"getipintel request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("abuseipdb_free_check_error", ip=ip, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"IP reputation check failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 3. Malware Hash Check (abuse.ch — MalwareBazaar, ThreatFox, URLhaus)
# ---------------------------------------------------------------------------

async def malware_hash_check(hash_value: str) -> ToolResult:
    """Check a file hash against free malware databases (abuse.ch).

    Queries MalwareBazaar, ThreatFox, and URLhaus simultaneously.
    All abuse.ch APIs are free and require no API key.
    """
    start = time.monotonic()

    err = _validate_hash(hash_value)
    if err:
        return ToolResult(success=False, output="", error=err)

    cleaned = hash_value.strip().lower()

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # Run all three lookups in parallel
        bazaar_task = _check_malwarebazaar(client, cleaned)
        threatfox_task = _check_threatfox(client, cleaned)
        urlhaus_task = _check_urlhaus(client, cleaned)

        results = await _gather_safe(bazaar_task, threatfox_task, urlhaus_task)

    elapsed = int((time.monotonic() - start) * 1000)

    bazaar_result, threatfox_result, urlhaus_result = results

    lines = [f"Malware Hash Check: {cleaned}", ""]
    found_any = False

    # MalwareBazaar results
    if bazaar_result:
        found_any = True
        lines.append("[MalwareBazaar]")
        for key, val in bazaar_result.items():
            lines.append(f"  {key}: {val}")
        lines.append("")

    # ThreatFox results
    if threatfox_result:
        found_any = True
        lines.append("[ThreatFox]")
        for key, val in threatfox_result.items():
            lines.append(f"  {key}: {val}")
        lines.append("")

    # URLhaus results
    if urlhaus_result:
        found_any = True
        lines.append("[URLhaus]")
        for key, val in urlhaus_result.items():
            lines.append(f"  {key}: {val}")
        lines.append("")

    if not found_any:
        lines.append("Khong tim thay thong tin ve hash nay trong cac nguon abuse.ch.")
        lines.append("Hash co the sach hoac chua duoc bao cao.")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "hash": cleaned,
            "found": found_any,
            "malwarebazaar": bazaar_result,
            "threatfox": threatfox_result,
            "urlhaus": urlhaus_result,
        },
    )


async def _gather_safe(*tasks) -> list[dict | None]:
    """Run multiple async tasks, returning None for any that fail."""
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for result in gathered:
        if isinstance(result, Exception):
            log.debug("malware_check_subtask_failed", error=str(result))
            results.append(None)
        else:
            results.append(result)
    return results


async def _check_malwarebazaar(client: httpx.AsyncClient, hash_val: str) -> dict | None:
    """Query MalwareBazaar for a hash."""
    try:
        resp = await client.post(
            "https://mb-api.abuse.ch/api/v1/",
            data={"query": "get_info", "hash": hash_val},
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("query_status") != "hash_not_found" and data.get("data"):
            entry = data["data"][0]
            return {
                "Malware family": entry.get("signature", "N/A"),
                "File type": entry.get("file_type", "N/A"),
                "File size": f"{entry.get('file_size', 'N/A')} bytes",
                "First seen": entry.get("first_seen", "N/A"),
                "Tags": ", ".join(entry.get("tags", [])) or "N/A",
                "Reporter": entry.get("reporter", "N/A"),
                "Origin country": entry.get("origin_country", "N/A"),
            }
    except Exception as e:
        log.debug("malwarebazaar_error", error=str(e))
    return None


async def _check_threatfox(client: httpx.AsyncClient, hash_val: str) -> dict | None:
    """Query ThreatFox for a hash."""
    try:
        resp = await client.post(
            "https://threatfox-api.abuse.ch/api/v1/",
            json={"query": "search_hash", "search_term": hash_val},
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("query_status") == "ok" and data.get("data"):
            entry = data["data"][0]
            return {
                "Threat type": entry.get("threat_type", "N/A"),
                "Malware": entry.get("malware_printable", "N/A"),
                "Confidence": f"{entry.get('confidence_level', 'N/A')}%",
                "First seen": entry.get("first_seen", "N/A"),
                "Last seen": entry.get("last_seen", "N/A"),
                "Tags": ", ".join(entry.get("tags", [])) or "N/A",
            }
    except Exception as e:
        log.debug("threatfox_error", error=str(e))
    return None


async def _check_urlhaus(client: httpx.AsyncClient, hash_val: str) -> dict | None:
    """Query URLhaus for a hash (payload lookup)."""
    try:
        # URLhaus uses md5_hash or sha256_hash based on length
        if len(hash_val) == 32:
            form_data = {"md5_hash": hash_val}
        elif len(hash_val) == 64:
            form_data = {"sha256_hash": hash_val}
        else:
            # SHA1 not directly supported by URLhaus payload endpoint
            return None

        resp = await client.post(
            "https://urlhaus-api.abuse.ch/v1/payload/",
            data=form_data,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("query_status") == "ok":
            urls = data.get("urls", [])
            url_count = len(urls)
            first_url = urls[0] if urls else {}
            return {
                "File type": data.get("file_type", "N/A"),
                "File size": f"{data.get('file_size', 'N/A')} bytes",
                "Signature": data.get("signature", "N/A"),
                "First seen": data.get("firstseen", "N/A"),
                "URLs distributing": str(url_count),
                "URL status": first_url.get("url_status", "N/A") if first_url else "N/A",
            }
    except Exception as e:
        log.debug("urlhaus_error", error=str(e))
    return None


# ---------------------------------------------------------------------------
# 4. Shodan Search
# ---------------------------------------------------------------------------

async def shodan_search(query: str, search_type: str = "search") -> ToolResult:
    """Search Shodan for exposed services and vulnerabilities.

    Uses Shodan API if SHODAN_API_KEY is set. Falls back to the free
    InternetDB service (no key required, IP lookups only).
    """
    start = time.monotonic()

    err = _validate_input(query, "query")
    if err:
        return ToolResult(success=False, output="", error=err)

    cleaned = query.strip()
    api_key = os.environ.get("SHODAN_API_KEY", "")

    if api_key:
        return await _shodan_api_search(cleaned, search_type, api_key, start)

    # Free fallback: InternetDB only works with IPs
    ip_err = _validate_ip(cleaned)
    if ip_err:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False,
            output=(
                "SHODAN_API_KEY chua duoc cau hinh.\n"
                "Free fallback (InternetDB) chi ho tro tra cuu IP.\n"
                "Dang ky API key tai: https://account.shodan.io/register\n"
                "Hoac nhap mot dia chi IP de dung InternetDB mien phi."
            ),
            error="Missing SHODAN_API_KEY (non-IP query requires key)",
            execution_time_ms=elapsed,
        )

    ssrf_err = _ssrf_check_ip(cleaned)
    if ssrf_err:
        return ToolResult(success=False, output="", error=ssrf_err)

    return await _shodan_internetdb(cleaned, start)


async def _shodan_api_search(
    query: str,
    search_type: str,
    api_key: str,
    start: float,
) -> ToolResult:
    """Query the Shodan API with an API key."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            if search_type == "host":
                # SSRF check for host IP
                ssrf_err = _ssrf_check_ip(query)
                if ssrf_err:
                    return ToolResult(success=False, output="", error=ssrf_err)
                url = f"https://api.shodan.io/shodan/host/{query}"
                params = {"key": api_key}

            elif search_type == "dns":
                ssrf_err = _ssrf_check_domain(query)
                if ssrf_err:
                    return ToolResult(success=False, output="", error=ssrf_err)
                url = "https://api.shodan.io/dns/resolve"
                params = {"key": api_key, "hostnames": query}

            else:
                # Default: search
                url = "https://api.shodan.io/shodan/host/search"
                params = {"key": api_key, "query": query}

            resp = await client.get(
                url, params=params,
                headers={"User-Agent": _USER_AGENT},
            )

        elapsed = int((time.monotonic() - start) * 1000)

        if resp.status_code == 401:
            return ToolResult(
                success=False, output="",
                error="Shodan API key khong hop le hoac het han.",
                execution_time_ms=elapsed,
            )

        if resp.status_code == 429:
            return ToolResult(
                success=False, output="",
                error="Shodan API rate limit. Vui long doi va thu lai.",
                execution_time_ms=elapsed,
            )

        resp.raise_for_status()
        data = resp.json()

        return _format_shodan_result(query, search_type, data, elapsed)

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"Shodan request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("shodan_search_error", query=query, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Shodan search failed: {e}",
            execution_time_ms=elapsed,
        )


def _format_shodan_result(
    query: str,
    search_type: str,
    data: dict,
    elapsed: int,
) -> ToolResult:
    """Format Shodan API response into a readable ToolResult."""
    lines = [f"Shodan — {search_type.upper()}: {query}", ""]

    if search_type == "host":
        ip = data.get("ip_str", query)
        org = data.get("org", "N/A")
        os_name = data.get("os", "N/A")
        country = data.get("country_name", "N/A")
        city = data.get("city", "N/A")
        ports = data.get("ports", [])
        vulns = data.get("vulns", [])

        lines.append(f"IP: {ip} | Org: {org}")
        lines.append(f"OS: {os_name} | Location: {city}, {country}")
        lines.append(f"Open ports ({len(ports)}): {', '.join(str(p) for p in ports[:20])}")

        if vulns:
            lines.append(f"\nVulnerabilities ({len(vulns)}):")
            for vuln in vulns[:10]:
                lines.append(f"  {vuln}")

        services = data.get("data", [])
        if services:
            lines.append(f"\nServices ({len(services)}):")
            for svc in services[:10]:
                port = svc.get("port", "?")
                transport = svc.get("transport", "tcp")
                product = svc.get("product", "unknown")
                version = svc.get("version", "")
                banner_text = svc.get("data", "")[:100]
                lines.append(f"  {port}/{transport} — {product} {version}")
                if banner_text:
                    lines.append(f"    Banner: {banner_text}")

    elif search_type == "dns":
        for hostname, ip in data.items():
            lines.append(f"  {hostname} -> {ip}")

    else:
        # search results
        total = data.get("total", 0)
        matches = data.get("matches", [])
        lines.append(f"Total results: {total} (showing {len(matches)})")
        lines.append("")

        for match in matches[:10]:
            ip = match.get("ip_str", "?")
            port = match.get("port", "?")
            org = match.get("org", "N/A")
            product = match.get("product", "unknown")
            country = match.get("location", {}).get("country_name", "N/A")
            lines.append(f"  {ip}:{port} — {product} | Org: {org} | {country}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"query": query, "search_type": search_type, "raw": data},
    )


async def _shodan_internetdb(ip: str, start: float) -> ToolResult:
    """Free fallback: query Shodan InternetDB (no API key needed)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                f"https://internetdb.shodan.io/{ip}",
                headers={"User-Agent": _USER_AGENT},
            )

        elapsed = int((time.monotonic() - start) * 1000)

        if resp.status_code == 404:
            return ToolResult(
                success=True,
                output=f"InternetDB: Khong tim thay thong tin cho IP {ip}",
                execution_time_ms=elapsed,
                data={"ip": ip, "found": False, "source": "internetdb"},
            )

        resp.raise_for_status()
        data = resp.json()

        ports = data.get("ports", [])
        hostnames = data.get("hostnames", [])
        cpes = data.get("cpes", [])
        vulns = data.get("vulns", [])
        tags = data.get("tags", [])

        lines = [
            f"Shodan InternetDB (free) — IP: {ip}",
            f"Open ports ({len(ports)}): {', '.join(str(p) for p in ports) or 'None'}",
            f"Hostnames: {', '.join(hostnames) or 'None'}",
            f"Tags: {', '.join(tags) or 'None'}",
        ]

        if cpes:
            lines.append(f"\nCPEs ({len(cpes)}):")
            for cpe in cpes[:15]:
                lines.append(f"  {cpe}")

        if vulns:
            lines.append(f"\nVulnerabilities ({len(vulns)}):")
            for vuln in vulns[:15]:
                lines.append(f"  {vuln}")

        # Security recommendations
        if vulns or len(ports) > 5:
            lines.append("\nKhuyen nghi:")
            if vulns:
                lines.append(f"  - Co {len(vulns)} lo hong bao mat can xu ly")
            if len(ports) > 5:
                lines.append(f"  - {len(ports)} ports mo — kiem tra va dong cac port khong can thiet")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "ip": ip,
                "found": True,
                "source": "internetdb",
                "ports": ports,
                "hostnames": hostnames,
                "vulns": vulns,
                "cpes": cpes,
                "tags": tags,
            },
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"InternetDB request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("shodan_internetdb_error", ip=ip, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"InternetDB lookup failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

virustotal_lookup_tool = ToolDefinition(
    name="virustotal_lookup",
    description=(
        "Tra cuu file hash, URL, IP, hoac domain tren VirusTotal. "
        "Kiem tra muc do nguy hiem, ty le phat hien malware, va danh gia rui ro."
    ),
    parameters=[
        ToolParameter(
            name="indicator", type="string",
            description="Hash (MD5/SHA1/SHA256), URL, IP, hoac domain can kiem tra",
        ),
        ToolParameter(
            name="indicator_type", type="string",
            description="Loai indicator: hash, url, ip, domain, hoac auto (tu dong nhan dien)",
            required=False, default="auto",
            enum=["hash", "url", "ip", "domain", "auto"],
        ),
    ],
    handler=virustotal_lookup,
    timeout_seconds=25,
)

abuseipdb_check_tool = ToolDefinition(
    name="abuseipdb_check",
    description=(
        "Kiem tra danh tieng IP tren AbuseIPDB. "
        "Xem diem do tin cay, so lan bao cao, quoc gia, ISP, va loai tan cong."
    ),
    parameters=[
        ToolParameter(
            name="ip", type="string",
            description="Dia chi IP can kiem tra (IPv4 hoac IPv6)",
        ),
        ToolParameter(
            name="max_age_days", type="integer",
            description="So ngay toi da de tim bao cao (1-365)",
            required=False, default=90,
        ),
    ],
    handler=abuseipdb_check,
    timeout_seconds=25,
)

malware_hash_check_tool = ToolDefinition(
    name="malware_hash_check",
    description=(
        "Kiem tra hash file qua cac nguon malware mien phi (MalwareBazaar, ThreatFox, URLhaus). "
        "Tra ve thong tin ve malware family, tags, file type, va thoi gian phat hien."
    ),
    parameters=[
        ToolParameter(
            name="hash_value", type="string",
            description="File hash (MD5, SHA1, hoac SHA256) can kiem tra",
        ),
    ],
    handler=malware_hash_check,
    timeout_seconds=30,
)

shodan_search_tool = ToolDefinition(
    name="shodan_search",
    description=(
        "Tim kiem thiet bi va dich vu tren Shodan. "
        "Xem ports mo, banner, lo hong, va thong tin ve dich vu Internet."
    ),
    parameters=[
        ToolParameter(
            name="query", type="string",
            description="IP address, domain, hoac tu khoa tim kiem Shodan",
        ),
        ToolParameter(
            name="search_type", type="string",
            description="Loai tim kiem: host (IP), search (tu khoa), dns (phan giai domain)",
            required=False, default="search",
            enum=["host", "search", "dns"],
        ),
    ],
    handler=shodan_search,
    timeout_seconds=25,
)
