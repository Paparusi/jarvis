"""Crypto Attack Tools — Hash identification, rainbow table lookup, classical ciphers, encoding chains.

Tools for CTF challenges, security research, and cryptanalysis workflows.
Includes hash identification, rainbow table lookups, classical cipher decoding,
and multi-layer encoding chain detection/decoding.
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import re
import string
import time
import urllib.parse
from collections import Counter

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.crypto_attack")


# ---------------------------------------------------------------------------
# English frequency analysis helpers
# ---------------------------------------------------------------------------

# Relative letter frequencies in English (source: standard corpus analysis)
_ENGLISH_FREQ = {
    "e": 12.70, "t": 9.06, "a": 8.17, "o": 7.51, "i": 6.97,
    "n": 6.75, "s": 6.33, "h": 6.09, "r": 5.99, "d": 4.25,
    "l": 4.03, "c": 2.78, "u": 2.76, "m": 2.41, "w": 2.36,
    "f": 2.23, "g": 2.02, "y": 1.97, "p": 1.93, "b": 1.29,
    "v": 0.98, "k": 0.77, "j": 0.15, "x": 0.15, "q": 0.10,
    "z": 0.07,
}

_COMMON_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her",
    "she", "or", "an", "will", "my", "one", "all", "would", "there",
    "their", "what", "so", "up", "out", "if", "about", "who", "get",
    "which", "go", "me", "when", "make", "can", "like", "time", "no",
    "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then",
    "now", "look", "only", "come", "its", "over", "think", "also",
    "back", "after", "use", "two", "how", "our", "work", "first",
    "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "is", "are", "was", "were", "been",
    "has", "had", "did", "does", "may", "must", "shall", "should",
}


def _score_english(text: str) -> float:
    """Score how likely a string is English text (0.0 to 100.0).

    Uses letter frequency correlation and common word detection.
    """
    if not text:
        return 0.0

    lower = text.lower()

    # Frequency correlation score (0-50 points)
    alpha_chars = [c for c in lower if c in string.ascii_lowercase]
    if not alpha_chars:
        return 0.0

    freq_score = 0.0
    counts = Counter(alpha_chars)
    total = len(alpha_chars)
    for letter, expected_pct in _ENGLISH_FREQ.items():
        observed_pct = (counts.get(letter, 0) / total) * 100
        diff = abs(expected_pct - observed_pct)
        freq_score += max(0, 2.0 - diff)  # Up to 2 points per letter

    # Common word score (0-50 points)
    words = set(re.findall(r"[a-z]+", lower))
    word_hits = words & _COMMON_WORDS
    word_score = min(50.0, len(word_hits) * 5.0)

    # Printable ratio bonus
    printable_ratio = sum(1 for c in text if c.isprintable()) / max(len(text), 1)
    printable_bonus = printable_ratio * 10.0

    return min(100.0, freq_score + word_score + printable_bonus)


# ---------------------------------------------------------------------------
# 1. Hash Identify
# ---------------------------------------------------------------------------

_HASH_PATTERNS = [
    # (name, regex_pattern, confidence_note)
    ("CRC32", r"^[a-fA-F0-9]{8}$", "8 hex chars"),
    ("MySQL (old)", r"^[a-fA-F0-9]{16}$", "16 hex chars"),
    ("MD5 / NTLM", r"^[a-fA-F0-9]{32}$", "32 hex chars — could be MD5 or NTLM"),
    ("SHA1", r"^[a-fA-F0-9]{40}$", "40 hex chars"),
    ("MySQL (new)", r"^\*[a-fA-F0-9]{40}$", "* + 40 hex chars"),
    ("SHA256", r"^[a-fA-F0-9]{64}$", "64 hex chars"),
    ("SHA512", r"^[a-fA-F0-9]{128}$", "128 hex chars"),
    ("bcrypt", r"^\$2[aby]\$.{56}$", "bcrypt format ($2a$/$2b$/$2y$)"),
    ("Argon2", r"^\$argon2(id?|d)\$.+", "Argon2 format"),
]


async def hash_identify(hash_value: str) -> ToolResult:
    """Identify the hash type from a hash string."""
    start = time.monotonic()

    if not hash_value:
        return ToolResult(success=False, output="", error="hash_value must not be empty")

    hash_value = hash_value.strip()
    matches = []

    # Try base64 decode first to see if it wraps a known hash
    decoded_from_b64 = None
    if not re.match(r"^[a-fA-F0-9]+$", hash_value) and not hash_value.startswith("$"):
        try:
            raw = base64.b64decode(hash_value, validate=True)
            hex_decoded = raw.hex()
            decoded_from_b64 = hex_decoded
        except Exception:
            pass

    # Match against known patterns
    targets = [("original", hash_value)]
    if decoded_from_b64:
        targets.append(("base64-decoded", decoded_from_b64))

    for source_label, value in targets:
        for name, pattern, note in _HASH_PATTERNS:
            if re.match(pattern, value):
                confidence = "high"
                if name == "CRC32":
                    confidence = "medium"
                if name == "MD5 / NTLM":
                    confidence = "medium"
                if name == "MySQL (old)":
                    confidence = "low"

                entry = {
                    "type": name,
                    "confidence": confidence,
                    "pattern": note,
                    "source": source_label,
                    "matched_value": value,
                }
                matches.append(entry)

    elapsed = int((time.monotonic() - start) * 1000)

    if not matches:
        return ToolResult(
            success=True,
            output=f"Hash: {hash_value[:100]}\nKhong nhan dien duoc loai hash nao.",
            execution_time_ms=elapsed,
        )

    lines = [f"Hash: {hash_value[:100]}", f"Do dai: {len(hash_value)} ky tu", ""]
    lines.append(f"Tim thay {len(matches)} loai co the:")
    for i, m in enumerate(matches):
        source_info = f" (from {m['source']})" if m["source"] != "original" else ""
        lines.append(
            f"  [{i + 1}] {m['type']} — {m['confidence']} confidence{source_info}"
        )
        lines.append(f"       Pattern: {m['pattern']}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"matches": matches},
    )


# ---------------------------------------------------------------------------
# 2. Hash Crack (rainbow table lookup)
# ---------------------------------------------------------------------------

async def hash_crack(hash_value: str, hash_type: str = "auto") -> ToolResult:
    """Lookup a hash in free rainbow table APIs."""
    start = time.monotonic()

    if not hash_value:
        return ToolResult(success=False, output="", error="hash_value must not be empty")

    hash_value = hash_value.strip().lower()
    hash_type = hash_type.strip().lower()

    # Auto-detect hash type from length
    if hash_type == "auto":
        length = len(hash_value)
        if length == 32:
            hash_type = "md5"
        elif length == 40:
            hash_type = "sha1"
        elif length == 64:
            hash_type = "sha256"
        elif length == 128:
            hash_type = "sha512"
        else:
            hash_type = "md5"

    results = []
    plaintext = None

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        # API 1: md5decrypt.net
        try:
            resp = await client.get(
                "https://md5decrypt.net/Api/api.php",
                params={
                    "hash": hash_value,
                    "hash_type": hash_type,
                    "email": "derev44@gmail.com",
                    "code": "1152464b80a61728",
                },
            )
            body = resp.text.strip()
            if resp.status_code == 200 and body and body != hash_value and "ERROR" not in body.upper():
                plaintext = body
                results.append(f"md5decrypt.net: {body}")
        except Exception as exc:
            results.append(f"md5decrypt.net: loi — {exc}")

        # API 2: hashify.net
        if not plaintext:
            try:
                url = f"https://api.hashify.net/hash/{hash_type}/{hash_value}"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    found = data.get("Plaintext") or data.get("plaintext") or data.get("result")
                    if found:
                        plaintext = found
                        results.append(f"hashify.net: {found}")
                    else:
                        results.append("hashify.net: khong tim thay")
            except Exception as exc:
                results.append(f"hashify.net: loi — {exc}")

        # API 3: hashtoolkit.com (scrape)
        if not plaintext:
            try:
                resp = await client.get(
                    f"https://hashtoolkit.com/reverse-hash",
                    params={"hash": hash_value},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200:
                    body = resp.text
                    # Look for the plaintext in the response
                    match = re.search(
                        r'class="res-text"[^>]*>([^<]+)<', body
                    )
                    if match:
                        found = match.group(1).strip()
                        if found:
                            plaintext = found
                            results.append(f"hashtoolkit.com: {found}")
                    else:
                        results.append("hashtoolkit.com: khong tim thay")
            except Exception as exc:
                results.append(f"hashtoolkit.com: loi — {exc}")

    elapsed = int((time.monotonic() - start) * 1000)

    lines = [
        f"Hash: {hash_value}",
        f"Loai: {hash_type.upper()}",
        "",
    ]

    if plaintext:
        lines.append(f"Ket qua: {plaintext}")
        lines.append("")
        lines.append("Chi tiet cac API:")
    else:
        lines.append("Khong tim thay plaintext trong rainbow tables.")
        lines.append("")
        lines.append("Chi tiet cac API:")

    for r in results:
        lines.append(f"  - {r}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"plaintext": plaintext, "hash_type": hash_type},
    )


# ---------------------------------------------------------------------------
# 3. Cipher Decode — Classical ciphers
# ---------------------------------------------------------------------------

def _caesar_shift(text: str, shift: int) -> str:
    """Apply Caesar cipher shift to text."""
    result = []
    for c in text:
        if c.isalpha():
            base = ord("A") if c.isupper() else ord("a")
            result.append(chr((ord(c) - base + shift) % 26 + base))
        else:
            result.append(c)
    return "".join(result)


def _atbash(text: str) -> str:
    """Apply Atbash cipher (A<->Z, B<->Y, etc.)."""
    result = []
    for c in text:
        if c.isalpha():
            base = ord("A") if c.isupper() else ord("a")
            result.append(chr(base + 25 - (ord(c) - base)))
        else:
            result.append(c)
    return "".join(result)


def _vigenere_decode(text: str, key: str) -> str:
    """Decode Vigenere cipher with known key."""
    if not key:
        return text

    key_upper = key.upper()
    result = []
    key_idx = 0
    for c in text:
        if c.isalpha():
            shift = ord(key_upper[key_idx % len(key_upper)]) - ord("A")
            base = ord("A") if c.isupper() else ord("a")
            result.append(chr((ord(c) - base - shift) % 26 + base))
            key_idx += 1
        else:
            result.append(c)
    return "".join(result)


def _rail_fence_decode(text: str, rails: int) -> str:
    """Decode rail fence cipher."""
    if rails < 2 or rails >= len(text):
        return text

    # Build the pattern of rail indices
    pattern = []
    for i in range(len(text)):
        cycle = 2 * (rails - 1)
        pos = i % cycle
        rail = pos if pos < rails else cycle - pos
        pattern.append(rail)

    # Calculate lengths of each rail
    rail_lengths = [0] * rails
    for r in pattern:
        rail_lengths[r] += 1

    # Split ciphertext into rails
    rail_texts = []
    offset = 0
    for length in rail_lengths:
        rail_texts.append(text[offset : offset + length])
        offset += length

    # Read off in pattern order
    rail_indices = [0] * rails
    result = []
    for r in pattern:
        idx = rail_indices[r]
        if idx < len(rail_texts[r]):
            result.append(rail_texts[r][idx])
            rail_indices[r] += 1

    return "".join(result)


def _estimate_vigenere_key_length(text: str) -> int:
    """Use Index of Coincidence to estimate Vigenere key length (Friedman test)."""
    alpha_only = [c.upper() for c in text if c.isalpha()]
    n = len(alpha_only)
    if n < 20:
        return 3  # Default guess for very short texts

    best_length = 3
    best_ic = 0.0

    for key_len in range(2, min(21, n // 2)):
        groups = [[] for _ in range(key_len)]
        for i, c in enumerate(alpha_only):
            groups[i % key_len].append(c)

        total_ic = 0.0
        valid_groups = 0
        for group in groups:
            gn = len(group)
            if gn < 2:
                continue
            counts = Counter(group)
            ic = sum(c * (c - 1) for c in counts.values()) / (gn * (gn - 1))
            total_ic += ic
            valid_groups += 1

        if valid_groups > 0:
            avg_ic = total_ic / valid_groups
            # English IC is ~0.0667, random is ~0.0385
            if avg_ic > best_ic:
                best_ic = avg_ic
                best_length = key_len

    return best_length


def _crack_vigenere_with_key_length(text: str, key_length: int) -> tuple[str, str]:
    """Given a key length, find the most likely Vigenere key using frequency analysis."""
    alpha_only = [c.upper() for c in text if c.isalpha()]

    key_chars = []
    for i in range(key_length):
        group = [alpha_only[j] for j in range(i, len(alpha_only), key_length)]
        if not group:
            key_chars.append("A")
            continue

        # Try each shift and pick the one with best English frequency match
        best_shift = 0
        best_score = float("inf")
        counts = Counter(group)
        group_len = len(group)

        for shift in range(26):
            score = 0.0
            for letter_idx in range(26):
                observed = counts.get(chr((letter_idx + shift) % 26 + ord("A")), 0) / group_len
                expected = _ENGLISH_FREQ.get(chr(letter_idx + ord("a")), 0) / 100
                score += (observed - expected) ** 2
            if score < best_score:
                best_score = score
                best_shift = shift

        key_chars.append(chr(best_shift + ord("A")))

    key = "".join(key_chars)
    decoded = _vigenere_decode(text, key)
    return decoded, key


def _cipher_decode_sync(
    ciphertext: str, cipher: str, key: str | None
) -> dict:
    """Synchronous cipher decoding logic (CPU-intensive for brute force)."""
    results = []

    if cipher in ("caesar", "auto"):
        best_score = -1.0
        best_shift = 0
        best_text = ciphertext
        for shift in range(1, 26):
            decoded = _caesar_shift(ciphertext, -shift)
            score = _score_english(decoded)
            if score > best_score:
                best_score = score
                best_shift = shift
                best_text = decoded
        results.append({
            "cipher": "Caesar",
            "decoded": best_text,
            "key": f"shift={best_shift}",
            "confidence": round(best_score, 1),
        })

    if cipher in ("rot13", "auto"):
        decoded = _caesar_shift(ciphertext, -13)
        score = _score_english(decoded)
        results.append({
            "cipher": "ROT13",
            "decoded": decoded,
            "key": "shift=13",
            "confidence": round(score, 1),
        })

    if cipher in ("atbash", "auto"):
        decoded = _atbash(ciphertext)
        score = _score_english(decoded)
        results.append({
            "cipher": "Atbash",
            "decoded": decoded,
            "key": "reverse alphabet",
            "confidence": round(score, 1),
        })

    if cipher in ("vigenere", "auto"):
        if key:
            decoded = _vigenere_decode(ciphertext, key)
            score = _score_english(decoded)
            results.append({
                "cipher": "Vigenere",
                "decoded": decoded,
                "key": key,
                "confidence": round(score, 1),
            })
        else:
            # Try to crack key length via Friedman test
            key_length = _estimate_vigenere_key_length(ciphertext)
            decoded, found_key = _crack_vigenere_with_key_length(ciphertext, key_length)
            score = _score_english(decoded)
            results.append({
                "cipher": "Vigenere",
                "decoded": decoded,
                "key": f"{found_key} (estimated, length={key_length})",
                "confidence": round(score, 1),
            })

    if cipher in ("rail_fence", "auto"):
        best_score = -1.0
        best_rails = 2
        best_text = ciphertext
        for rails in range(2, 11):
            decoded = _rail_fence_decode(ciphertext, rails)
            score = _score_english(decoded)
            if score > best_score:
                best_score = score
                best_rails = rails
                best_text = decoded
        results.append({
            "cipher": "Rail Fence",
            "decoded": best_text,
            "key": f"rails={best_rails}",
            "confidence": round(best_score, 1),
        })

    return results


async def cipher_decode(
    ciphertext: str, cipher: str = "auto", key: str = ""
) -> ToolResult:
    """Decode classical ciphers (Caesar, ROT13, Vigenere, Atbash, Rail Fence)."""
    start = time.monotonic()

    if not ciphertext:
        return ToolResult(success=False, output="", error="ciphertext must not be empty")

    cipher = cipher.strip().lower()
    valid_ciphers = {"caesar", "rot13", "vigenere", "atbash", "rail_fence", "auto"}
    if cipher not in valid_ciphers:
        return ToolResult(
            success=False, output="",
            error=f"Cipher '{cipher}' khong hop le. Dung: {', '.join(sorted(valid_ciphers))}",
        )

    # Run CPU-intensive decoding in a thread
    results = await asyncio.to_thread(
        _cipher_decode_sync, ciphertext, cipher, key or None
    )

    elapsed = int((time.monotonic() - start) * 1000)

    if not results:
        return ToolResult(
            success=True,
            output="Khong giai ma duoc.",
            execution_time_ms=elapsed,
        )

    # Sort by confidence, best first
    results.sort(key=lambda r: r["confidence"], reverse=True)

    lines = [
        f"Ciphertext: {ciphertext[:200]}",
        f"Cipher yeu cau: {cipher}",
        "",
    ]

    if cipher == "auto":
        lines.append(f"Ket qua tot nhat: {results[0]['cipher']}")
        lines.append(f"  Decoded: {results[0]['decoded'][:500]}")
        lines.append(f"  Key: {results[0]['key']}")
        lines.append(f"  Confidence: {results[0]['confidence']}")
        lines.append("")
        lines.append("Tat ca ket qua:")

    for i, r in enumerate(results):
        lines.append(f"  [{i + 1}] {r['cipher']} (confidence: {r['confidence']})")
        lines.append(f"       Key: {r['key']}")
        lines.append(f"       Decoded: {r['decoded'][:300]}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"results": results, "best": results[0]},
    )


# ---------------------------------------------------------------------------
# 4. Encoding Chain — Multi-layer encoding detection and decoding
# ---------------------------------------------------------------------------

def _is_printable_ascii(text: str) -> bool:
    """Check if text is mostly printable ASCII."""
    if not text:
        return False
    printable_count = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    return printable_count / len(text) > 0.95


def _try_base64_decode(text: str) -> tuple[str | None, str]:
    """Try to decode Base64 (standard or URL-safe). Returns (decoded, variant).

    Rejects decodes that produce mostly non-printable characters (likely not
    actually base64-encoded text).
    """
    stripped = text.strip()

    # Must be at least 4 chars and look like base64
    if len(stripped) < 4:
        return None, ""

    # Skip strings that look like hex with 0x prefix
    if stripped.lower().startswith("0x"):
        return None, ""

    # Standard base64
    if re.match(r"^[A-Za-z0-9+/]+=*$", stripped) and len(stripped) % 4 <= 2:
        try:
            decoded = base64.b64decode(stripped, validate=True).decode("utf-8", errors="replace")
            if decoded and decoded != stripped and _is_printable_ascii(decoded):
                return decoded, "Base64"
        except Exception:
            pass

    # URL-safe base64
    if re.match(r"^[A-Za-z0-9_-]+=*$", stripped):
        try:
            padded = stripped + "=" * (4 - len(stripped) % 4) if len(stripped) % 4 else stripped
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            if decoded and decoded != stripped and _is_printable_ascii(decoded):
                return decoded, "Base64 (URL-safe)"
        except Exception:
            pass

    return None, ""


def _try_url_decode(text: str) -> str | None:
    """Try URL decoding if text contains percent-encoded sequences."""
    if "%" not in text:
        return None
    decoded = urllib.parse.unquote(text)
    if decoded != text:
        return decoded
    return None


def _try_hex_decode(text: str) -> str | None:
    """Try hex decoding (0x... or continuous hex string)."""
    stripped = text.strip()

    # 0x prefix
    if stripped.lower().startswith("0x"):
        hex_str = stripped[2:]
    elif re.match(r"^[a-fA-F0-9]+$", stripped) and len(stripped) >= 4 and len(stripped) % 2 == 0:
        hex_str = stripped
    else:
        return None

    try:
        decoded = bytes.fromhex(hex_str).decode("utf-8", errors="replace")
        if decoded and _is_printable_ascii(decoded):
            return decoded
    except Exception:
        pass
    return None


def _try_html_entity_decode(text: str) -> str | None:
    """Try HTML entity decoding (&amp; &#x41; &#65; etc.)."""
    if "&" not in text:
        return None
    decoded = html.unescape(text)
    if decoded != text:
        return decoded
    return None


def _try_unicode_escape_decode(text: str) -> str | None:
    r"""Try Unicode escape decoding (\uXXXX)."""
    if "\\u" not in text and r"\u" not in text:
        return None
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
        if decoded != text:
            return decoded
    except Exception:
        pass
    return None


def _try_octal_escape_decode(text: str) -> str | None:
    r"""Try octal escape decoding (\xxx)."""
    if "\\" not in text and "\\" not in repr(text):
        return None

    # Match sequences like \101 \150 etc.
    octal_pattern = r"\\([0-7]{1,3})"
    if not re.search(octal_pattern, text):
        return None

    try:
        decoded = re.sub(
            octal_pattern,
            lambda m: chr(int(m.group(1), 8)),
            text,
        )
        if decoded != text:
            return decoded
    except Exception:
        pass
    return None


def _try_binary_decode(text: str) -> str | None:
    """Try binary decoding (01100001 01100010 ...)."""
    stripped = text.strip().replace(" ", "")
    if not re.match(r"^[01]+$", stripped):
        return None
    if len(stripped) % 8 != 0:
        return None
    if len(stripped) < 8:
        return None

    try:
        chars = []
        for i in range(0, len(stripped), 8):
            byte = int(stripped[i : i + 8], 2)
            chars.append(chr(byte))
        decoded = "".join(chars)
        if _is_printable_ascii(decoded):
            return decoded
    except Exception:
        pass
    return None


def _try_jwt_decode(text: str) -> str | None:
    """Try JWT decoding (header.payload.signature)."""
    stripped = text.strip()
    parts = stripped.split(".")
    if len(parts) != 3:
        return None

    # Each part should look like base64url
    for part in parts[:2]:
        if not re.match(r"^[A-Za-z0-9_-]+$", part):
            return None

    try:
        def _b64url(data: str) -> bytes:
            padding = 4 - len(data) % 4
            if padding != 4:
                data += "=" * padding
            return base64.urlsafe_b64decode(data)

        header = json.loads(_b64url(parts[0]).decode("utf-8"))
        payload = json.loads(_b64url(parts[1]).decode("utf-8"))
        return (
            f"JWT Header: {json.dumps(header, indent=2, ensure_ascii=False)}\n"
            f"JWT Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}"
        )
    except Exception:
        return None


def _encoding_chain_sync(encoded_text: str, max_depth: int) -> list[dict]:
    """Synchronous multi-layer decoding logic."""
    steps = []
    current = encoded_text

    for depth in range(max_depth):
        decoded = None
        encoding = None

        # Try each decoding method in order: specific formats first, then generic
        # JWT (most specific structured format)
        result = _try_jwt_decode(current)
        if result is not None:
            decoded = result
            encoding = "JWT"

        # Hex (check before base64 since 0x-prefixed hex is unambiguous)
        if decoded is None:
            result = _try_hex_decode(current)
            if result is not None:
                decoded = result
                encoding = "Hex"

        # Binary (only 0s and 1s -- very specific pattern)
        if decoded is None:
            result = _try_binary_decode(current)
            if result is not None:
                decoded = result
                encoding = "Binary"

        # URL encoding (requires % characters)
        if decoded is None:
            result = _try_url_decode(current)
            if result is not None:
                decoded = result
                encoding = "URL encoding"

        # HTML entities (requires & characters)
        if decoded is None:
            result = _try_html_entity_decode(current)
            if result is not None:
                decoded = result
                encoding = "HTML entities"

        # Unicode escapes (requires \u sequences)
        if decoded is None:
            result = _try_unicode_escape_decode(current)
            if result is not None:
                decoded = result
                encoding = "Unicode escape"

        # Octal escapes (requires \ sequences)
        if decoded is None:
            result = _try_octal_escape_decode(current)
            if result is not None:
                decoded = result
                encoding = "Octal escape"

        # Base64 (most generic -- tried last to avoid false positives)
        if decoded is None:
            result, variant = _try_base64_decode(current)
            if result is not None:
                decoded = result
                encoding = variant

        if decoded is None:
            break

        steps.append({
            "depth": depth + 1,
            "encoding": encoding,
            "input": current[:200],
            "output": decoded[:500],
        })
        current = decoded

    return steps


async def encoding_chain(encoded_text: str, max_depth: int = 10) -> ToolResult:
    """Auto-detect and decode multi-layer encoding chains."""
    start = time.monotonic()

    if not encoded_text:
        return ToolResult(success=False, output="", error="encoded_text must not be empty")

    max_depth = min(max(1, max_depth), 50)

    steps = await asyncio.to_thread(_encoding_chain_sync, encoded_text, max_depth)

    elapsed = int((time.monotonic() - start) * 1000)

    if not steps:
        return ToolResult(
            success=True,
            output=f"Input: {encoded_text[:200]}\nKhong phat hien encoding nao.",
            execution_time_ms=elapsed,
        )

    final_output = steps[-1]["output"]

    lines = [
        f"Input: {encoded_text[:200]}",
        f"So lop encoding: {len(steps)}",
        "",
        "Cac buoc giai ma:",
    ]

    for step in steps:
        lines.append(f"  [{step['depth']}] {step['encoding']}")
        lines.append(f"       -> {step['output'][:300]}")

    lines.append("")
    lines.append(f"Ket qua cuoi: {final_output[:500]}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"steps": steps, "final": final_output},
    )


# ===========================================================================
# Tool Definitions
# ===========================================================================

hash_identify_tool = ToolDefinition(
    name="hash_identify",
    description="Nhan dien loai hash tu chuoi hash. Ho tro MD5, SHA1, SHA256, SHA512, NTLM, bcrypt, Argon2, MySQL, CRC32.",
    parameters=[
        ToolParameter(
            name="hash_value", type="string",
            description="Chuoi hash can nhan dien loai",
        ),
    ],
    handler=hash_identify,
)

hash_crack_tool = ToolDefinition(
    name="hash_crack",
    description="Tra cuu hash trong rainbow tables (API mien phi). Ho tro MD5, SHA1, SHA256, NTLM.",
    parameters=[
        ToolParameter(
            name="hash_value", type="string",
            description="Chuoi hash can tra cuu",
        ),
        ToolParameter(
            name="hash_type", type="string",
            description="Loai hash (tu dong nhan dien neu chon 'auto')",
            required=False, default="auto",
            enum=["md5", "sha1", "sha256", "ntlm", "auto"],
        ),
    ],
    handler=hash_crack,
    timeout_seconds=30,
)

cipher_decode_tool = ToolDefinition(
    name="cipher_decode",
    description="Giai ma cipher co dien: Caesar, ROT13, Vigenere, Atbash, Rail Fence. Tu dong thu tat ca neu chon 'auto'.",
    parameters=[
        ToolParameter(
            name="ciphertext", type="string",
            description="Van ban da ma hoa can giai ma",
        ),
        ToolParameter(
            name="cipher", type="string",
            description="Loai cipher (hoac 'auto' de thu tat ca)",
            required=False, default="auto",
            enum=["caesar", "rot13", "vigenere", "atbash", "rail_fence", "auto"],
        ),
        ToolParameter(
            name="key", type="string",
            description="Khoa giai ma (bat buoc cho Vigenere neu biet, bo trong de tu dong tim)",
            required=False, default="",
        ),
    ],
    handler=cipher_decode,
)

encoding_chain_tool = ToolDefinition(
    name="encoding_chain",
    description="Tu dong phat hien va giai ma chuoi encoding nhieu lop: Base64, URL, Hex, HTML entities, Unicode, Octal, Binary, JWT.",
    parameters=[
        ToolParameter(
            name="encoded_text", type="string",
            description="Van ban da duoc encode (co the nhieu lop)",
        ),
        ToolParameter(
            name="max_depth", type="integer",
            description="So lop giai ma toi da (mac dinh 10)",
            required=False, default=10,
        ),
    ],
    handler=encoding_chain,
)
