---
name: crypto-attack
description: "Phân tích và tấn công mật mã — nhận diện hash, crack hash, giải mã cipher, và phân tích encoding chain."
version: "1.0.0"
author: "JARVIS CTO"
tags: [security, crypto, hash, cipher, encoding, cryptanalysis]
allowed-tools: [hash_identify, hash_crack, cipher_decode, encoding_chain]
metadata:
  jarvis:
    emoji: "🔐"
    category: security
    priority: 0.80
    success_rate: 1.0
    usage_count: 0
---

# Cryptographic Analysis

Phân tích và tấn công mật mã: nhận diện loại hash, crack hash bằng wordlist/rainbow table, giải mã các cipher cổ điển và hiện đại, phân tích chuỗi encoding lồng nhau.

## Khi nào kích hoạt

- Hash: "đây là hash gì?", "crack hash này", "nhận diện hash"
- Cipher: "giải mã chuỗi này", "decode cipher", "decrypt message này"
- Encoding: "decode chuỗi encoding này", "base64 lồng nhiều lớp"
- CTF: "giải challenge crypto này", "tìm flag từ ciphertext"
- Phân tích: "phân tích thuật toán mã hóa này", "có điểm yếu gì không"

## Workflow

### Hash Analysis
1. **Nhận diện** (`hash_identify`):
   - Phân tích format, length, character set
   - Xác định: MD5, SHA-1, SHA-256, SHA-512, bcrypt, NTLM, etc.
   - Detect salted hashes, multiple rounds
2. **Crack** (`hash_crack`):
   - Dictionary attack với wordlists phổ biến
   - Rule-based mutations
   - Rainbow table lookup
   - Brute force (cho hash ngắn)

### Cipher Analysis
3. **Giải mã** (`cipher_decode`):
   - Classical ciphers: Caesar, Vigenere, Substitution, Transposition
   - Modern: XOR, RC4, AES (nếu có key)
   - Frequency analysis cho unknown ciphers
   - Known-plaintext attack khi có cả plaintext và ciphertext

### Encoding Chain
4. **Phân tích encoding** (`encoding_chain`):
   - Detect encoding layers: Base64, Base32, Hex, URL encoding, ROT13
   - Auto-decode multi-layer encoding
   - Identify obfuscation patterns

## Quy tắc

- CHỈ crack hash với mục đích hợp pháp (CTF, pentest có authorization, recovery)
- KHÔNG hỗ trợ crack credentials bất hợp pháp
- Giải thích rõ thuật toán và điểm yếu được khai thác
- Gợi ý thuật toán mạnh hơn khi phát hiện crypto yếu
- Luôn cảnh báo khi phát hiện mã hóa không an toàn (MD5, SHA-1, DES)

## Output format

```
## Crypto Analysis Report

### Hash Identification
- Input: 5f4dcc3b5aa765d61d8327deb882cf99
- Type: MD5 (confidence: 95%)
- Length: 32 hex chars (128 bits)
- Salted: No

### Crack Result
- Status: CRACKED ✓
- Plaintext: password
- Method: Dictionary attack (rockyou.txt)
- Time: 0.3s

### Security Assessment
- [CRITICAL] MD5 is cryptographically broken
- Recommendation: Migrate to bcrypt/argon2 with salt
```

```
## Encoding Chain Analysis

### Input
- SGVsbG8gV29ybGQ=

### Decoding Steps
1. Base64 → "Hello World"

### Multi-layer Example
- Input: U0dWc2JHOD0=
- Layer 1 (Base64): SGVsbG8=
- Layer 2 (Base64): Hello
- Total layers: 2
```

## Ví dụ truy vấn

- "Đây là hash gì: 5f4dcc3b5aa765d61d8327deb882cf99"
- "Crack hash SHA-256 này"
- "Giải mã cipher Caesar chuỗi này"
- "Decode chuỗi base64 lồng nhiều lớp này"
- "Phân tích mã hóa của file này, có điểm yếu không?"
- "Giải challenge crypto CTF này"
