"""Vector helpers (stdlib only).

Embeddings are stored as packed 32-bit floats; similarity is computed in Python.
Fine at SQLite/dev scale — the Postgres and Neo4j backends push this into the
database.
"""

from __future__ import annotations

import math
from array import array

from .models import Vector

_TYPECODE = "f"


def pack(vec: Vector) -> bytes:
    """Pack a float vector into a compact ``bytes`` blob."""
    return array(_TYPECODE, vec).tobytes()


def unpack(blob: bytes) -> Vector:
    """Reverse :func:`pack`."""
    out = array(_TYPECODE)
    out.frombytes(blob)
    return list(out)


def mean(vectors: list[Vector]) -> Vector:
    """Component-wise mean of equal-length vectors; ``[]`` if the input is empty."""
    usable = [v for v in vectors if v]
    if not usable:
        return []
    dim = len(usable[0])
    acc = [0.0] * dim
    for vec in usable:
        if len(vec) != dim:
            continue
        for i, x in enumerate(vec):
            acc[i] += x
    return [x / len(usable) for x in acc]


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity in ``[-1, 1]``; ``0.0`` if either vector is empty/zero."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
