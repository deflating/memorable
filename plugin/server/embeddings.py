"""Shared embedding utilities for Memorable.

Uses Apple NaturalLanguage framework (NLEmbedding) for 512-dim
sentence embeddings, running entirely on-device via the Neural Engine.

Extracted from observer.py so both the observation pipeline and
web API can use embeddings without circular imports.
"""

import array
import struct

# ── Apple NaturalLanguage Embeddings ──────────────────────────

_nl_embedding = None


def _get_embedding_model():
    """Lazy-load Apple NLEmbedding sentence model."""
    global _nl_embedding
    if _nl_embedding is None:
        import objc
        ns = {}
        objc.loadBundle(
            'NaturalLanguage', ns,
            '/System/Library/Frameworks/NaturalLanguage.framework',
        )
        NLEmbedding = ns['NLEmbedding']
        _nl_embedding = NLEmbedding.sentenceEmbeddingForLanguage_('en')
    return _nl_embedding


def embed_text(text: str) -> bytes | None:
    """Generate 512-dim sentence embedding, packed as float32 BLOB."""
    try:
        model = _get_embedding_model()
        if model is None:
            return None
        vec = model.vectorForString_(text[:500])
        if vec is None:
            return None
        return array.array('f', vec).tobytes()
    except Exception:
        return None


def cosine_distance(text1: str, text2: str) -> float:
    """Cosine distance between two texts. Lower = more similar."""
    try:
        model = _get_embedding_model()
        if model is None:
            return 2.0
        return model.distanceBetweenString_andString_distanceType_(
            text1[:500], text2[:500], 0
        )
    except Exception:
        return 2.0


def cosine_distance_vectors(vec_a: bytes, vec_b: bytes) -> float:
    """Cosine distance between two pre-computed embedding BLOBs.

    Each BLOB is 512 float32 values (2048 bytes).
    Returns distance in the same range as NLEmbedding: ~0.8 (similar) to ~1.5 (unrelated).
    """
    if not vec_a or not vec_b:
        return 2.0
    try:
        n = len(vec_a) // 4  # float32 = 4 bytes
        a = struct.unpack(f'{n}f', vec_a)
        b = struct.unpack(f'{n}f', vec_b)

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 2.0

        cosine_sim = dot / (norm_a * norm_b)
        # NLEmbedding uses cosine distance = 1 - cosine_similarity
        return 1.0 - cosine_sim
    except Exception:
        return 2.0


def semantic_score(dist: float) -> float:
    """Normalize NLEmbedding distance to 0-1 similarity score.

    Distances typically range 0.8 (very similar) to 1.5 (unrelated).
    """
    return max(0.0, (1.5 - dist) / 0.7)


def embedding_available() -> bool:
    """Check if embedding model is available (without loading it)."""
    try:
        model = _get_embedding_model()
        return model is not None
    except Exception:
        return False
