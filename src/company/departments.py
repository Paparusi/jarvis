"""Department definitions and tool allocation for JARVIS Company.

Each department owns a set of tools and responds to specific keyword patterns.
The classify_department() function uses keyword scoring to route requests.
"""

from __future__ import annotations

from enum import Enum

from src.utils.logging import get_logger

log = get_logger("company.departments")


class Department(str, Enum):
    """JARVIS company departments."""

    GENERAL = "general"        # CEO handles directly
    FINANCE = "finance"        # Trading, market analysis, MT5
    SECURITY = "security"      # Pentesting, OSINT, vulnerability scanning
    ENGINEERING = "engineering" # Code analysis, git, docker, development
    RESEARCH = "research"      # Web search, document analysis, deep research
    OPERATIONS = "operations"  # Scheduling, TTS, image analysis, system ops


# Tool name → Department mapping
TOOL_ALLOCATION: dict[Department, set[str]] = {
    Department.FINANCE: {
        "mt5_price", "mt5_candles", "mt5_account", "mt5_positions",
        "mt5_order", "mt5_close", "mt5_history", "market_session",
        "technical_indicators", "trading_calendar",
        "mt5_analyze", "mt5_signal", "mt5_risk",
        "mt5_journal_log", "mt5_journal_stats", "mt5_journal_sync",
        "mt5_smc",
        "trade_plan", "trade_status", "trade_config",
        "trade_control", "trade_pending", "trade_history",
    },
    Department.SECURITY: {
        "subdomain_enum", "http_headers", "cve_lookup", "reverse_dns",
        "tech_detect", "port_scan", "dns_lookup",
        "google_dork", "username_search", "email_harvest",
        "wayback_lookup", "github_leaks",
        "dir_bruteforce", "sqli_test", "xss_scan", "cors_check",
        "waf_detect", "lfi_test", "header_audit",
        "hash_identify", "hash_crack", "cipher_decode", "encoding_chain",
        "exploit_search", "reverse_shell_gen", "payload_encode", "gtfobins_lookup",
        "file_metadata", "stego_detect", "ioc_extract", "log_analyze",
        "virustotal_lookup", "abuseipdb_check", "malware_hash_check", "shodan_search",
        "subdomain_takeover", "js_secrets_scan", "open_redirect", "nuclei_scan",
        "subfinder_enum", "httpx_probe", "katana_crawl", "gau_urls", "ffuf_fuzz",
    },
    Department.ENGINEERING: {
        "ast_analyze", "complexity_check", "dependency_graph",
        "code_search", "diff_summary",
        "git_status", "git_diff", "git_log", "git_commit", "git_branch",
        "docker_ps", "docker_logs", "docker_exec", "docker_images", "docker_compose",
        "run_python", "shell",
        "read_file", "write_file", "list_dir",
    },
    Department.RESEARCH: {
        "web_search", "fetch_url", "browse_web", "deep_search", "screenshot",
        "ingest_document", "query_documents",
        "http_request",
    },
    Department.OPERATIONS: {
        "text_to_speech", "analyze_image", "ocr_image",
        "base64_encode", "hash_generate", "url_encode",
        "jwt_decode", "hex_convert", "regex_test", "timestamp_convert",
        "ip_info", "whois_lookup", "ssl_check",
        "generate_password", "cidr_calc",
    },
}

# Tools available to ALL departments (shared utilities)
SHARED_TOOLS: set[str] = {
    "web_search", "fetch_url", "read_file", "write_file", "list_dir",
    "run_python", "code_exec",
}

# Keyword patterns for fast department classification (no LLM needed)
DEPARTMENT_KEYWORDS: dict[Department, list[str]] = {
    Department.FINANCE: [
        "xauusd", "gold", "vàng", "giá vàng", "mt5", "trading", "trade",
        "lệnh", "position", "pending", "buy", "sell", "sl", "tp", "lot",
        "pip", "spread", "bid", "ask", "order", "entry", "profit", "loss",
        "phân tích thị trường", "phân tích kỹ thuật", "setup", "zone",
        "confluence", "risk", "reward", "breakeven", "trailing",
        "fibonacci", "session", "london", "new york",
        "/mt5", "/trade", "/signal",
    ],
    Department.SECURITY: [
        "scan", "vuln", "vulnerability", "exploit", "pentest", "recon",
        "subdomain", "cve", "xss", "sqli", "sql injection", "lfi",
        "brute", "fuzz", "osint", "dork", "hack", "bounty", "hunt",
        "nuclei", "nmap", "port scan", "reverse shell", "payload",
        "forensic", "malware", "threat", "stego", "ioc",
        "/pentest", "/bounty", "/hunt",
    ],
    Department.ENGINEERING: [
        "code", "debug", "refactor", "git", "commit", "branch", "merge",
        "docker", "container", "deploy", "build", "test", "lint",
        "function", "class", "module", "import", "error", "bug", "fix",
        "python", "javascript", "typescript", "rust", "ast", "complexity",
        "dependency", "diff",
    ],
    Department.RESEARCH: [
        "tìm kiếm", "search", "tra cứu", "research", "tìm hiểu",
        "tin tức", "news", "article", "paper",
        "document", "pdf", "summarize", "tóm tắt",
        "deep search", "browse",
    ],
    Department.OPERATIONS: [
        "nhắc nhở", "remind", "lịch", "schedule", "hẹn",
        "đọc ảnh", "image", "ocr", "screenshot",
        "tts", "đọc", "voice", "speak",
        "convert", "encode", "decode", "hash",
        "/remind", "/digest",
    ],
}


def classify_department(message: str) -> Department:
    """Classify a message to a department using keyword scoring.

    Returns Department.GENERAL if no strong match (CEO handles directly).
    """
    text_lower = message.lower()
    scores: dict[Department, int] = {}

    for dept, keywords in DEPARTMENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[dept] = score

    if not scores:
        return Department.GENERAL

    return max(scores, key=scores.get)


def get_department_tools(dept: Department) -> set[str]:
    """Get tool names allocated to a department + shared tools."""
    dept_tools = TOOL_ALLOCATION.get(dept, set())
    return dept_tools | SHARED_TOOLS


def get_department_display_name(dept: Department) -> str:
    """Vietnamese display names for departments."""
    names = {
        Department.GENERAL: "CEO (General)",
        Department.FINANCE: "Phong Tai chinh",
        Department.SECURITY: "Phong An ninh",
        Department.ENGINEERING: "Phong Ky thuat",
        Department.RESEARCH: "Phong Nghien cuu",
        Department.OPERATIONS: "Phong Van hanh",
    }
    return names.get(dept, dept.value)
