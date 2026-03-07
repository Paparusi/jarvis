---
name: api-tester
description: >
  Test and debug REST APIs — send requests, check responses, validate
  schemas, test authentication, and detect common API issues.
version: 1.0.0
metadata:
  jarvis:
    category: security
    emoji: "🔗"
    priority: 0.80
    mcp_tools: []
---

# API Tester

## Khi nao kich hoat
Khi user yeu cau:
- Test API endpoint
- Debug API response
- "goi API nay giup tao", "check endpoint nay"
- API security testing, auth testing

## Workflow
1. Parse API request tu user input (URL, method, headers, body)
2. Send request (dung `http_request`)
3. Analyze response:
   - Status code validation
   - Response time check
   - JSON schema validation (dung `run_python`)
   - Error message analysis
4. Security checks:
   - CORS headers
   - Auth token handling
   - Rate limiting headers
   - Information disclosure in errors
5. Tong hop ket qua

## Rules
- Luon hien thi full request va response cho transparency
- Mask sensitive data (tokens, passwords) trong output
- Test ca happy path va error cases
- Neu API tra loi 401/403, huong dan user cach authenticate
- Goi y improvements cho API design neu phat hien issues

## Output format
```
## API Test: [METHOD] [URL]

### Request
- Method: POST
- Headers: Content-Type: application/json
- Body: {...}

### Response
- Status: 200 OK (150ms)
- Headers: Content-Type: application/json
- Body: {...}

### Analysis
- Response time: Good (< 200ms)
- Status: Expected
- Schema: Valid JSON

### Security Notes
- CORS: Properly configured
- Auth: Bearer token accepted
- Rate limit: 100 req/min (X-RateLimit header)
```
