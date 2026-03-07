"""Network Tools — Port scanning, DNS lookup, ping, traceroute.

All tools run via async subprocess with timeouts. Designed for
authorized security testing and network diagnostics.
"""

from __future__ import annotations

import asyncio
import time

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.network")

_CMD_TIMEOUT = 60  # seconds


async def _run_cmd(cmd: list[str], timeout: int = _CMD_TIMEOUT) -> tuple[str, str, int]:
    """Run a command and return (stdout, stderr, returncode)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Command timed out after {timeout}s", 1
    except FileNotFoundError as e:
        return "", f"Command not found: {e}", 127


async def port_scan(
    target: str,
    ports: str = "22,80,443,8080,8443,3306,5432,6379,27017",
    scan_type: str = "connect",
) -> ToolResult:
    """Scan ports on a target host."""
    start = time.monotonic()

    # Validate target (prevent injection)
    if not target or any(c in target for c in [";", "&", "|", "`", "$", "(", ")"]):
        return ToolResult(success=False, output="", error="Invalid target")

    # Try nmap first, fallback to manual connect scan
    if scan_type == "nmap":
        stdout, stderr, rc = await _run_cmd(
            ["nmap", "-Pn", "-p", ports, "--open", "-T4", target],
            timeout=_CMD_TIMEOUT,
        )
        if rc == 127:  # nmap not installed
            return ToolResult(success=False, output="", error="nmap not installed. Use scan_type='connect'")
        elapsed = int((time.monotonic() - start) * 1000)
        output = stdout or stderr
        return ToolResult(success=rc == 0, output=output[:5000], execution_time_ms=elapsed)

    # Connect scan (no nmap needed)
    port_list = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
    results = []

    async def _check_port(port: int) -> str:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(target, port), timeout=3,
            )
            writer.close()
            await writer.wait_closed()
            return f"  {port}/tcp  OPEN"
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return f"  {port}/tcp  closed"

    tasks = [_check_port(p) for p in port_list[:50]]  # Max 50 ports
    scan_results = await asyncio.gather(*tasks)
    elapsed = int((time.monotonic() - start) * 1000)

    open_count = sum(1 for r in scan_results if "OPEN" in r)
    output = (
        f"Port scan: {target}\n"
        f"Ports scanned: {len(port_list)} | Open: {open_count}\n\n"
        + "\n".join(scan_results)
    )

    return ToolResult(success=True, output=output, execution_time_ms=elapsed)


async def dns_lookup(domain: str, record_type: str = "A") -> ToolResult:
    """DNS lookup for a domain."""
    start = time.monotonic()

    if not domain or any(c in domain for c in [";", "&", "|", "`", "$"]):
        return ToolResult(success=False, output="", error="Invalid domain")

    record_type = record_type.upper()
    if record_type not in ("A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "PTR", "SRV", "ANY"):
        return ToolResult(success=False, output="", error=f"Unsupported record type: {record_type}")

    # Try dig first
    stdout, stderr, rc = await _run_cmd(
        ["dig", "+short", record_type, domain], timeout=10,
    )

    if rc == 127:
        # dig not available, try nslookup
        stdout, stderr, rc = await _run_cmd(
            ["nslookup", "-type=" + record_type, domain], timeout=10,
        )

    elapsed = int((time.monotonic() - start) * 1000)
    output = stdout.strip() or stderr.strip() or "No records found"

    return ToolResult(
        success=rc == 0,
        output=f"DNS {record_type} for {domain}:\n{output}",
        execution_time_ms=elapsed,
    )


async def ping(target: str, count: int = 4) -> ToolResult:
    """Ping a host to check connectivity and latency."""
    start = time.monotonic()

    if not target or any(c in target for c in [";", "&", "|", "`", "$"]):
        return ToolResult(success=False, output="", error="Invalid target")

    count = min(max(1, count), 10)  # Clamp 1-10

    stdout, stderr, rc = await _run_cmd(
        ["ping", "-c", str(count), "-W", "3", target], timeout=count * 5,
    )
    elapsed = int((time.monotonic() - start) * 1000)

    output = stdout or stderr
    return ToolResult(success=rc == 0, output=output[:3000], execution_time_ms=elapsed)


async def traceroute(target: str) -> ToolResult:
    """Trace network route to a host."""
    start = time.monotonic()

    if not target or any(c in target for c in [";", "&", "|", "`", "$"]):
        return ToolResult(success=False, output="", error="Invalid target")

    # Try traceroute, fallback to tracepath
    stdout, stderr, rc = await _run_cmd(
        ["traceroute", "-m", "20", "-w", "2", target], timeout=60,
    )

    if rc == 127:
        stdout, stderr, rc = await _run_cmd(
            ["tracepath", target], timeout=60,
        )

    elapsed = int((time.monotonic() - start) * 1000)
    output = stdout or stderr
    return ToolResult(success=rc == 0, output=output[:5000], execution_time_ms=elapsed)


# Tool definitions

port_scan_tool = ToolDefinition(
    name="port_scan",
    description="Scan ports on a target host. Supports nmap or connect scan. For authorized security testing.",
    parameters=[
        ToolParameter(name="target", type="string", description="Target host (IP or hostname)"),
        ToolParameter(
            name="ports", type="string",
            description="Comma-separated port list",
            required=False, default="22,80,443,8080,8443,3306,5432,6379,27017",
        ),
        ToolParameter(
            name="scan_type", type="string",
            description="Scan method: 'connect' (no nmap needed) or 'nmap'",
            required=False, default="connect",
            enum=["connect", "nmap"],
        ),
    ],
    handler=port_scan,
    timeout_seconds=65,
)

dns_lookup_tool = ToolDefinition(
    name="dns_lookup",
    description="DNS lookup for a domain — resolve A, AAAA, MX, NS, TXT, CNAME records.",
    parameters=[
        ToolParameter(name="domain", type="string", description="Domain name to lookup"),
        ToolParameter(
            name="record_type", type="string",
            description="DNS record type",
            required=False, default="A",
            enum=["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "ANY"],
        ),
    ],
    handler=dns_lookup,
    timeout_seconds=15,
)

ping_tool = ToolDefinition(
    name="ping",
    description="Ping a host to check connectivity and measure latency.",
    parameters=[
        ToolParameter(name="target", type="string", description="Target host (IP or hostname)"),
        ToolParameter(
            name="count", type="integer", description="Number of pings (1-10)",
            required=False, default=4,
        ),
    ],
    handler=ping,
    timeout_seconds=30,
)

traceroute_tool = ToolDefinition(
    name="traceroute",
    description="Trace the network route to a host — shows each hop along the path.",
    parameters=[
        ToolParameter(name="target", type="string", description="Target host (IP or hostname)"),
    ],
    handler=traceroute,
    timeout_seconds=65,
)
