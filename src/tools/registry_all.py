"""Centralized tool registry — single source of truth for ALL tool definitions.

Import ALL_TOOLS in any adapter and register them:

    from src.tools.registry_all import ALL_TOOLS

    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)
"""

from __future__ import annotations

from src.tools.browser import browse_web_tool, deep_search_tool, screenshot_tool
from src.tools.code_exec import code_exec_tool
from src.tools.document import ingest_document_tool, query_documents_tool
from src.tools.tts import tts_tool
from src.tools.vision import analyze_image_tool, ocr_tool
from src.tools.file_ops import list_dir_tool, read_file_tool, write_file_tool
from src.tools.shell import shell_tool
from src.tools.web_search import fetch_url_tool, web_search_tool
from src.tools.http_client import http_request_tool
from src.tools.network import dns_lookup_tool, ping_tool, port_scan_tool, traceroute_tool
from src.tools.git_ops import (
    git_branch_tool, git_commit_tool, git_diff_tool,
    git_log_tool, git_status_tool,
)
from src.tools.docker_ops import (
    docker_compose_tool, docker_exec_tool, docker_images_tool,
    docker_logs_tool, docker_ps_tool,
)
from src.tools.crypto_utils import (
    base64_tool, hash_tool, url_encode_tool, jwt_decode_tool,
    hex_convert_tool, regex_test_tool, timestamp_tool,
    ip_info_tool, whois_tool, ssl_check_tool,
    generate_password_tool, cidr_calc_tool,
)
from src.tools.recon import (
    subdomain_enum_tool, http_headers_tool, cve_lookup_tool,
    reverse_dns_tool, tech_detect_tool,
)
from src.tools.code_analysis import (
    ast_analyze_tool, complexity_check_tool, dependency_graph_tool,
    code_search_tool, diff_summary_tool,
)
from src.tools.data_tools import (
    csv_analyze_tool, json_query_tool, sqlite_query_tool,
    text_stats_tool, json_transform_tool,
)
from src.tools.osint import (
    google_dork_tool, username_search_tool, email_harvest_tool,
    wayback_lookup_tool, github_leaks_tool,
)
from src.tools.web_attack import (
    dir_bruteforce_tool, sqli_test_tool, xss_scan_tool,
    cors_check_tool, waf_detect_tool, lfi_test_tool, header_audit_tool,
)
from src.tools.crypto_attack import (
    hash_identify_tool, hash_crack_tool, cipher_decode_tool,
    encoding_chain_tool,
)
from src.tools.exploit import (
    exploit_search_tool, reverse_shell_gen_tool, payload_encode_tool,
    gtfobins_lookup_tool,
)
from src.tools.forensics import (
    file_metadata_tool, stego_detect_tool, ioc_extract_tool,
    log_analyze_tool,
)
from src.tools.threat_intel import (
    virustotal_lookup_tool, abuseipdb_check_tool,
    malware_hash_check_tool, shodan_search_tool,
)
from src.tools.security_advanced import (
    subdomain_takeover_tool, js_secrets_scan_tool,
    open_redirect_tool, nuclei_scan_tool,
)
from src.tools.discovery import (
    subfinder_enum_tool, httpx_probe_tool, katana_crawl_tool,
    gau_urls_tool, ffuf_fuzz_tool,
)

from src.tools.base import ToolDefinition

ALL_TOOLS: list[ToolDefinition] = [
    # Core
    web_search_tool, fetch_url_tool, shell_tool,
    read_file_tool, write_file_tool, list_dir_tool,
    code_exec_tool,
    # HTTP & Browser
    http_request_tool, browse_web_tool, deep_search_tool, screenshot_tool,
    # Network
    port_scan_tool, dns_lookup_tool, ping_tool, traceroute_tool,
    # Git
    git_status_tool, git_diff_tool, git_log_tool,
    git_commit_tool, git_branch_tool,
    # Docker
    docker_ps_tool, docker_logs_tool, docker_exec_tool,
    docker_images_tool, docker_compose_tool,
    # Crypto & Utility
    base64_tool, hash_tool, url_encode_tool, jwt_decode_tool,
    hex_convert_tool, regex_test_tool, timestamp_tool,
    ip_info_tool, whois_tool, ssl_check_tool,
    generate_password_tool, cidr_calc_tool,
    # Security Recon
    subdomain_enum_tool, http_headers_tool, cve_lookup_tool,
    reverse_dns_tool, tech_detect_tool,
    # Code Analysis
    ast_analyze_tool, complexity_check_tool, dependency_graph_tool,
    code_search_tool, diff_summary_tool,
    # Data Tools
    csv_analyze_tool, json_query_tool, sqlite_query_tool,
    text_stats_tool, json_transform_tool,
    # Vision & Document & TTS
    analyze_image_tool, ocr_tool,
    ingest_document_tool, query_documents_tool,
    tts_tool,
    # OSINT
    google_dork_tool, username_search_tool, email_harvest_tool,
    wayback_lookup_tool, github_leaks_tool,
    # Web Attack
    dir_bruteforce_tool, sqli_test_tool, xss_scan_tool,
    cors_check_tool, waf_detect_tool, lfi_test_tool, header_audit_tool,
    # Crypto Attack
    hash_identify_tool, hash_crack_tool, cipher_decode_tool,
    encoding_chain_tool,
    # Exploit
    exploit_search_tool, reverse_shell_gen_tool, payload_encode_tool,
    gtfobins_lookup_tool,
    # Forensics
    file_metadata_tool, stego_detect_tool, ioc_extract_tool,
    log_analyze_tool,
    # Threat Intelligence
    virustotal_lookup_tool, abuseipdb_check_tool,
    malware_hash_check_tool, shodan_search_tool,
    # Security Advanced (Bug Bounty)
    subdomain_takeover_tool, js_secrets_scan_tool,
    open_redirect_tool, nuclei_scan_tool,
    # Discovery (ProjectDiscovery)
    subfinder_enum_tool, httpx_probe_tool, katana_crawl_tool,
    gau_urls_tool, ffuf_fuzz_tool,
]
