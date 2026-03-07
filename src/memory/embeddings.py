"""Embedding service for JARVIS memory — LOCAL semantic embeddings.

Dùng fastembed (ONNX) — chạy hoàn toàn local, không cần API key.
Model: BAAI/bge-small-en-v1.5 (~33MB, 384 dimensions, multilingual OK).
"""

from __future__ import annotations

import hashlib

import numpy as np

from src.utils.logging import get_logger

log = get_logger("memory.embeddings")

_model = None
_cache: dict[str, np.ndarray] = {}


def _get_model():
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding("BAAI/bge-small-en-v1.5")
        log.info("embedding_model_loaded", model="bge-small-en-v1.5")
    return _model


async def get_embedding(text: str) -> np.ndarray:
    """Get embedding vector for text using local model."""
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    model = _get_model()
    vectors = list(model.embed([text]))
    vector = np.array(vectors[0], dtype=np.float32)
    _cache[cache_key] = vector
    return vector


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))
