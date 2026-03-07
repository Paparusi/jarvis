"""Confidence Calibrator — Meta-cognitive layer for JARVIS.

Phân tích response quality và quyết định:
- confidence >= 0.80 → accept response (gửi cho user)
- confidence 0.40-0.80 → escalate to cloud (local không đủ tốt)
- confidence < 0.40 → reject + escalate (phải dùng cloud)

Signals đánh giá:
1. Response length vs expected (dựa trên query complexity)
2. Language consistency (trả lời đúng ngôn ngữ user dùng)
3. Contains relevant content (not generic filler)
4. Code quality (nếu query yêu cầu code)
5. Repetition check
6. Coherence (does response actually address the query?)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.utils.logging import get_logger

log = get_logger("metacognition.confidence")


@dataclass
class ConfidenceSignal:
    """Individual confidence signal with weight."""
    name: str
    score: float  # 0.0 - 1.0
    weight: float  # importance weight
    reason: str = ""


@dataclass
class ConfidenceReport:
    """Full confidence assessment of a response."""
    overall_score: float
    signals: list[ConfidenceSignal] = field(default_factory=list)
    decision: str = ""  # "accept", "escalate", "reject"
    reasoning: str = ""

    @property
    def should_escalate(self) -> bool:
        return self.decision in ("escalate", "reject")


class ConfidenceCalibrator:
    """Assess response quality and decide whether to accept or escalate."""

    def __init__(
        self,
        accept_threshold: float = 0.80,
        escalate_threshold: float = 0.40,
    ) -> None:
        self._accept = accept_threshold
        self._escalate = escalate_threshold

    def assess(
        self,
        query: str,
        response: str,
        complexity: str = "medium",
    ) -> ConfidenceReport:
        """Assess confidence in a local model response.

        Args:
            query: The user's original message
            response: The model's response
            complexity: Query complexity (simple/medium/complex)
        """
        signals = []

        # 1. Response length appropriateness
        signals.append(self._check_length(query, response, complexity))

        # 2. Language consistency
        signals.append(self._check_language(query, response))

        # 3. Content relevance (not generic filler)
        signals.append(self._check_relevance(query, response))

        # 4. Repetition / coherence
        signals.append(self._check_coherence(response))

        # 5. Code quality (if code was expected)
        code_signal = self._check_code(query, response)
        if code_signal:
            signals.append(code_signal)

        # 6. Completeness (response doesn't cut off mid-sentence)
        signals.append(self._check_completeness(response))

        # Calculate weighted score
        total_weight = sum(s.weight for s in signals)
        if total_weight == 0:
            overall = 0.0
        else:
            overall = sum(s.score * s.weight for s in signals) / total_weight

        # Decision
        if overall >= self._accept:
            decision = "accept"
            reasoning = f"Confidence {overall:.2f} >= {self._accept} — response quality sufficient"
        elif overall >= self._escalate:
            decision = "escalate"
            reasoning = f"Confidence {overall:.2f} is marginal — escalating to cloud for better quality"
        else:
            decision = "reject"
            reasoning = f"Confidence {overall:.2f} < {self._escalate} — response too low quality"

        report = ConfidenceReport(
            overall_score=overall,
            signals=signals,
            decision=decision,
            reasoning=reasoning,
        )

        log.info(
            "confidence_assessed",
            score=f"{overall:.2f}",
            decision=decision,
            signals={s.name: f"{s.score:.2f}" for s in signals},
        )

        return report

    def _check_length(self, query: str, response: str, complexity: str) -> ConfidenceSignal:
        """Check if response length is appropriate for the query complexity."""
        resp_len = len(response.strip())
        query_len = len(query.strip())

        # Expected minimum lengths by complexity
        min_lengths = {"simple": 5, "medium": 20, "complex": 50}
        min_len = min_lengths.get(complexity, 20)

        if resp_len < min_len:
            score = max(0.0, resp_len / min_len)
            reason = f"Response too short ({resp_len} chars, expected >= {min_len})"
        elif resp_len > query_len * 0.3 or resp_len > min_len:
            score = 1.0
            reason = "Good length"
        else:
            score = 0.5
            reason = "Marginally short"

        return ConfidenceSignal("length", score, weight=1.5, reason=reason)

    def _check_language(self, query: str, response: str) -> ConfidenceSignal:
        """Check if response language matches query language.

        Note: Local models (especially with thinking mode) often respond in
        English even to Vietnamese queries. This is acceptable — the content
        quality matters more than language match. We use a soft penalty.
        """
        vi_chars = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)

        query_is_vi = bool(vi_chars.search(query))
        response_is_vi = bool(vi_chars.search(response))

        if query_is_vi:
            if response_is_vi:
                return ConfidenceSignal("language", 1.0, weight=0.5, reason="Language match (VI)")
            else:
                # Soft penalty — response content may still be correct
                return ConfidenceSignal("language", 0.7, weight=0.5, reason="Query is VI but response is EN (acceptable)")

        return ConfidenceSignal("language", 1.0, weight=0.3, reason="Language OK")

    def _check_relevance(self, query: str, response: str) -> ConfidenceSignal:
        """Check if response is relevant (not just generic filler)."""
        response_lower = response.lower()
        query_lower = query.lower()

        # Generic filler phrases (signs of low confidence/quality)
        filler_phrases = [
            "i don't know", "i'm not sure", "i cannot", "i can't",
            "tôi không biết", "tôi không chắc", "tôi không thể",
            "as an ai", "as a language model",
        ]

        filler_count = sum(1 for p in filler_phrases if p in response_lower)
        if filler_count >= 2:
            return ConfidenceSignal("relevance", 0.1, weight=2.0, reason=f"Too many filler phrases ({filler_count})")

        # Check keyword overlap (words + numbers/symbols)
        stopwords = {"là", "gì", "có", "và", "của", "cho", "thì", "bằng", "mấy",
                      "the", "a", "is", "to", "of", "what", "how", "can", "do"}
        query_words = set(query_lower.split()) - stopwords
        resp_words = set(response_lower.split())
        overlap = query_words & resp_words

        # Also check numeric/symbol overlap (e.g., "1+1" → "2")
        query_nums = set(re.findall(r'\d+', query))
        resp_nums = set(re.findall(r'\d+', response))
        num_overlap = query_nums & resp_nums

        if len(query_words) > 0:
            overlap_ratio = len(overlap) / len(query_words)
        else:
            overlap_ratio = 0.5

        # Numeric overlap counts as relevance
        has_numeric_relevance = bool(num_overlap) and bool(query_nums)

        if overlap_ratio >= 0.3 or has_numeric_relevance:
            score = min(1.0, 0.7 + overlap_ratio)
        elif overlap_ratio > 0:
            score = 0.6
        else:
            # Short responses to short queries are likely fine
            if len(query.split()) <= 8 and len(response.strip()) >= 5:
                score = 0.7
            else:
                score = 0.4

        return ConfidenceSignal("relevance", score, weight=1.5, reason=f"Keyword overlap: {overlap_ratio:.0%}")

    def _check_coherence(self, response: str) -> ConfidenceSignal:
        """Check for repetition and incoherence."""
        words = response.split()
        if len(words) < 5:
            return ConfidenceSignal("coherence", 0.5, weight=1.0, reason="Too short to assess")

        # Check for excessive word repetition
        from collections import Counter
        word_counts = Counter(w.lower() for w in words if len(w) > 2)
        if word_counts:
            most_common_word, most_common_count = word_counts.most_common(1)[0]
            repetition_ratio = most_common_count / len(words)

            if repetition_ratio > 0.4:
                return ConfidenceSignal(
                    "coherence", 0.1, weight=1.5,
                    reason=f"Excessive repetition: '{most_common_word}' ({repetition_ratio:.0%})"
                )

        # Check for repeated phrases (n-grams, n=3)
        if len(words) >= 9:
            trigrams = [" ".join(words[i:i+3]).lower() for i in range(len(words) - 2)]
            trigram_counts = Counter(trigrams)
            if trigram_counts:
                most_common_tri, tri_count = trigram_counts.most_common(1)[0]
                if tri_count >= 4 and tri_count / len(trigrams) > 0.3:
                    return ConfidenceSignal(
                        "coherence", 0.1, weight=1.5,
                        reason=f"Repeated phrase: '{most_common_tri}' ({tri_count}x)"
                    )

        # Check for repeated sentences
        sentences = [s.strip() for s in re.split(r'[.!?。\n]', response) if s.strip()]
        if len(sentences) >= 3:
            unique_sentences = set(sentences)
            if len(unique_sentences) < len(sentences) * 0.5:
                return ConfidenceSignal(
                    "coherence", 0.2, weight=1.5,
                    reason="Many repeated sentences"
                )

        return ConfidenceSignal("coherence", 1.0, weight=1.0, reason="Good coherence")

    def _check_code(self, query: str, response: str) -> ConfidenceSignal | None:
        """If query asks for code, check if response contains code."""
        code_indicators = [
            "viết code", "write code", "viết hàm", "write function",
            "code", "script", "function", "debug", "fix code",
            "implement", "class", "method",
        ]

        query_lower = query.lower()
        expects_code = any(ind in query_lower for ind in code_indicators)
        if not expects_code:
            return None

        has_code = "```" in response or "def " in response or "function " in response
        if has_code:
            return ConfidenceSignal("code", 1.0, weight=2.0, reason="Contains code block")
        else:
            return ConfidenceSignal("code", 0.3, weight=2.0, reason="Expected code but none found")

    def _check_completeness(self, response: str) -> ConfidenceSignal:
        """Check if response seems complete (not cut off mid-sentence)."""
        response = response.strip()
        if not response:
            return ConfidenceSignal("completeness", 0.0, weight=1.0, reason="Empty response")

        # Check for obvious truncation
        last_char = response[-1]
        if last_char in ".!?。）)」\n" or response.endswith("```"):
            return ConfidenceSignal("completeness", 1.0, weight=0.8, reason="Response ends properly")

        # Ends mid-word or mid-sentence
        if last_char.isalpha() and len(response) > 100:
            return ConfidenceSignal("completeness", 0.4, weight=0.8, reason="Possible truncation")

        return ConfidenceSignal("completeness", 0.8, weight=0.5, reason="Unclear ending")
