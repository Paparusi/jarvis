# JARVIS Bug Bounty Pipeline — Design Document

## Goal

Build a fully autonomous bug bounty hunting pipeline that runs 24/7 — monitors programs, scans targets, finds vulnerabilities, verifies findings, generates reports, and notifies Bi for review + submission. JARVIS becomes a passive income machine.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     JARVIS Bug Bounty Pipeline                         │
│                                                                         │
│  [Program Monitor]──→[Target Queue]──→[Recon Engine]──→[Vuln Scanner]  │
│        ↑                                                     │          │
│        │                                              [Verifier]        │
│        │                                                     │          │
│  [Bounty Tracker]←──[Bi Review]←──[Notifier]←──[Report Writer]        │
│        │                  │                                             │
│        └──────[Feedback Loop — learn from accepted/rejected]───────────┘
└─────────────────────────────────────────────────────────────────────────┘
```

## Modules

### 1. Program Monitor (`src/bounty/monitor.py`)

Crawl bug bounty platforms to find profitable targets.

**Data sources:**
- HackerOne API (public programs, GraphQL endpoint)
- Bugcrowd (scrape program list)
- Intigriti, YesWeHack (secondary)

**Program selection criteria:**
- Has monetary bounty (skip reputation-only)
- Web app in scope (matches our toolset)
- Priority scoring:
  - NEW programs (< 7 days) = highest priority (less competition)
  - Updated scope = high priority
  - High bounty table = high priority
  - Low report count = medium priority (less picked over)

**Output:** `BountyProgram` records stored in SQLite (`bounty_programs` table):
- program_id, platform, name, url, scope (domains/wildcards)
- bounty_low, bounty_high, bounty_currency
- launch_date, last_checked, priority_score
- status: active/paused/completed

**Schedule:** Check for new programs every 6 hours.

### 2. Target Queue (`src/bounty/queue.py`)

Priority queue that feeds targets to the scanning pipeline.

**Queue logic:**
- New programs go to front of queue
- Each scope entry (domain/wildcard) becomes a Target
- Rate limiting per target (max 1 scan/24h, respect program rules)
- Backoff on 429/ban detection
- Max concurrent scans: 3 (prevent resource exhaustion)

**Target states:** `queued → scanning → scanned → rescan_scheduled`

**SQLite table:** `bounty_targets`
- target_id, program_id, domain, scope_type (domain/wildcard/ip)
- last_scanned, scan_count, next_scan, state
- findings_count, false_positive_count

### 3. Recon Engine (`src/bounty/recon.py`)

Comprehensive reconnaissance using existing JARVIS tools.

**Phase 1 — Passive Recon (no direct contact):**
- `subdomain_enum` → discover subdomains
- `google_dork` → find exposed files, admin panels, error pages
- `wayback_lookup` → find old endpoints, removed pages
- `github_leaks` → leaked secrets, API keys, credentials
- `tech_detect` → identify tech stack (frameworks, CMS, languages)
- `whois` → ownership info, registration date

**Phase 2 — Active Recon (light touch):**
- `http_headers` → security header analysis
- `ssl_check` → certificate issues
- `port_scan` → open ports and services (rate-limited)
- `dns_lookup` → DNS records, zone transfer attempts
- `cve_lookup` → known CVEs for detected tech/versions

**Output:** `ReconResult` — attack surface map:
- Subdomains list with live/dead status
- Tech stack per subdomain
- Interesting endpoints (admin, API, upload, login)
- Potential secrets/leaks found
- Known CVEs applicable

### 4. Vuln Scanner (`src/bounty/scanner.py`)

Automated vulnerability scanning using existing + new tools.

**Existing tools (ready to use):**
- `xss_scan` → reflected/stored XSS
- `sqli_test` → SQL injection
- `cors_check` → CORS misconfiguration
- `lfi_test` → local file inclusion
- `header_audit` → missing security headers
- `waf_detect` → WAF identification (adjust payloads)
- `dir_bruteforce` → hidden directories/files

**New tools to build:**
- `idor_test` → Insecure Direct Object Reference (parameter manipulation)
- `ssrf_test` → Server-Side Request Forgery (internal network access)
- `auth_bypass` → Authentication/authorization bypass checks
- `open_redirect` → Unvalidated redirects
- `info_disclosure` → Sensitive data in responses (emails, IPs, stack traces)

**Scan strategy:**
- Use Swarm for parallel scanning across endpoints
- Respect rate limits (configurable per program)
- WAF-aware: detect WAF → adjust payloads
- Focus on high-value vulns first: SQLi > XSS > IDOR > SSRF > others

**Output:** Raw `Finding` objects with confidence scores.

### 5. Vulnerability Verifier (`src/bounty/verifier.py`)

Reduce false positives — only report verified findings.

**Verification steps:**
1. **Reproduce:** Re-run the exploit to confirm it works
2. **Browser verify:** Use Playwright to confirm visual impact (for XSS)
3. **Impact assess:** What can attacker actually do?
4. **Dedup:** Check if same vuln exists across endpoints (report once)
5. **CVSS scoring:** Calculate severity score
6. **Bounty estimate:** Predict payout based on program history

**Confidence levels:**
- CONFIRMED (90%+) → auto-generate report
- LIKELY (70-90%) → generate report, flag for Bi review
- POSSIBLE (50-70%) → log only, don't report
- FALSE_POSITIVE (<50%) → discard, feed to ML for learning

**Output:** Verified `BountyFinding` with PoC, CVSS, estimated bounty.

### 6. Report Generator (`src/bounty/reporter.py`)

Generate platform-specific bug bounty reports.

**Report format (HackerOne standard):**
```markdown
## Summary
[One-line description of the vulnerability]

## Severity
[Critical/High/Medium/Low] — CVSS X.X

## Steps to Reproduce
1. Navigate to [URL]
2. [Action]
3. Observe [result]

## Impact
[What an attacker can achieve]

## Proof of Concept
[Screenshot/payload/request-response]

## Suggested Fix
[Remediation recommendation]
```

**Platform adapters:**
- HackerOne format
- Bugcrowd format
- Generic markdown

### 7. Notification + Review (`src/bounty/notifier.py`)

Notify Bi via Telegram with finding summary.

**Telegram message format:**
```
🎯 Bug Bounty Finding!

Program: [name] (HackerOne)
Target: [domain]
Vuln: SQL Injection — /api/users?id=1
Severity: HIGH (CVSS 8.6)
Confidence: 95% CONFIRMED
Est. Bounty: $500-$2,000

📋 Report ready — /bounty review 42
```

**Commands:**
- `/bounty status` — pipeline stats (running scans, findings, earnings)
- `/bounty programs` — list monitored programs
- `/bounty findings` — list pending findings
- `/bounty review <id>` — show full report for review
- `/bounty approve <id>` — mark as approved (Bi submits manually)
- `/bounty reject <id>` — mark as false positive (feeds learning)
- `/bounty earnings` — total earnings tracker
- `/bounty start/stop` — start/stop the pipeline

### 8. Bounty Tracker (`src/bounty/tracker.py`)

Track submissions, earnings, and learn from results.

**SQLite tables:**
- `bounty_findings` — all verified findings
- `bounty_submissions` — submitted reports + status
- `bounty_earnings` — paid bounties

**Feedback loop:**
- Accepted finding → boost similar patterns in scanner
- Rejected (duplicate) → mark target area as covered
- Rejected (not applicable) → adjust scope understanding
- Rejected (informative) → lower severity threshold

**Metrics (Prometheus):**
- `bounty_scans_total` — total scans run
- `bounty_findings_total` — findings by severity
- `bounty_submissions_total` — submissions by status
- `bounty_earnings_total` — total earnings in USD

## Database Schema

```sql
-- Programs being monitored
CREATE TABLE bounty_programs (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,          -- hackerone, bugcrowd
    program_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT,
    scope_domains TEXT,              -- JSON array of domains/wildcards
    bounty_low INTEGER DEFAULT 0,
    bounty_high INTEGER DEFAULT 0,
    priority_score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked TIMESTAMP,
    UNIQUE(platform, program_id)
);

-- Individual scan targets
CREATE TABLE bounty_targets (
    id INTEGER PRIMARY KEY,
    program_id INTEGER REFERENCES bounty_programs(id),
    domain TEXT NOT NULL,
    scope_type TEXT DEFAULT 'domain',
    state TEXT DEFAULT 'queued',
    last_scanned TIMESTAMP,
    next_scan TIMESTAMP,
    scan_count INTEGER DEFAULT 0,
    findings_count INTEGER DEFAULT 0,
    UNIQUE(program_id, domain)
);

-- Verified findings
CREATE TABLE bounty_findings (
    id INTEGER PRIMARY KEY,
    target_id INTEGER REFERENCES bounty_targets(id),
    vuln_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    cvss REAL,
    confidence REAL,
    title TEXT NOT NULL,
    description TEXT,
    steps_to_reproduce TEXT,
    poc TEXT,
    impact TEXT,
    suggested_fix TEXT,
    estimated_bounty_low INTEGER,
    estimated_bounty_high INTEGER,
    status TEXT DEFAULT 'pending',    -- pending, approved, submitted, accepted, rejected
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Earnings tracking
CREATE TABLE bounty_earnings (
    id INTEGER PRIMARY KEY,
    finding_id INTEGER REFERENCES bounty_findings(id),
    platform TEXT,
    amount REAL,
    currency TEXT DEFAULT 'USD',
    paid_at TIMESTAMP
);
```

## Existing Tools Reuse

| Module | Existing Tools (ready) | New Tools Needed |
|--------|----------------------|------------------|
| Recon | subdomain_enum, google_dork, wayback_lookup, github_leaks, tech_detect, whois, http_headers, ssl_check, port_scan, dns_lookup, cve_lookup | — |
| Scanner | xss_scan, sqli_test, cors_check, lfi_test, header_audit, waf_detect, dir_bruteforce | idor_test, ssrf_test, auth_bypass, open_redirect, info_disclosure |
| Verifier | Playwright (browser verify) | PoC reproducer logic |
| Reporter | ReportGenerator (existing) | Platform-specific formatters |
| Infrastructure | Swarm (parallel), SQLite, Telegram, Prometheus | — |

## Implementation Priority

1. **Phase A:** Core pipeline (Monitor → Queue → Recon → basic Scanner → Report → Notify)
2. **Phase B:** Verifier + advanced scanner tools (IDOR, SSRF, auth bypass)
3. **Phase C:** Feedback loop + ML learning from results
4. **Phase D:** Multi-platform support (Bugcrowd, Intigriti)

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Getting IP banned | Rate limiting, rotate user agents, respect robots.txt |
| False positives waste time | Verifier module + confidence filtering |
| Duplicate reports | Check program's disclosed reports first |
| Legal issues | Only scan in-scope targets, follow program rules |
| Resource usage | Queue limits, scan scheduling, concurrent scan cap |

## Success Criteria

- Pipeline runs autonomously 24/7
- < 20% false positive rate on submitted reports
- First valid finding within 2 weeks of deployment
- $500+ earnings within first 2 months
- Telegram notifications with one-tap review flow
