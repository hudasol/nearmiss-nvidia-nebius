"""Deterministic samplers over a stress envelope.

* ``latin_hypercube`` — the conventional design-of-experiments sweep used by the baseline.
* ``halton`` — a low-discrepancy sequence used only for the dense reference (oracle)
  sweep that establishes ground-truth labels. Using a different sequence from both
  strategies keeps the labels independent of how either strategy samples.
"""

from __future__ import annotations

import random

from nearmiss.domain.models import StressEnvelope, StressPoint

_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)


def _scale(envelope: StressEnvelope, unit: list[float]) -> StressPoint:
    return {
        a.name: a.low + u * (a.high - a.low)
        for a, u in zip(envelope.axes, unit, strict=True)
    }


def latin_hypercube(envelope: StressEnvelope, n: int, seed: int) -> list[StressPoint]:
    """``n`` stratified samples: each axis is split into ``n`` equal strata, each used once."""
    if n <= 0:
        return []
    rng = random.Random(seed)
    columns: list[list[float]] = []
    for _ in envelope.axes:
        strata = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(strata)
        columns.append(strata)
    return [_scale(envelope, [col[i] for col in columns]) for i in range(n)]


def _radical_inverse(index: int, base: int) -> float:
    result, f = 0.0, 1.0 / base
    i = index
    while i > 0:
        result += f * (i % base)
        i //= base
        f /= base
    return result


def halton(envelope: StressEnvelope, n: int, skip: int = 20) -> list[StressPoint]:
    if len(envelope.axes) > len(_PRIMES):
        raise ValueError("too many stress axes for the Halton sequence")
    return [
        _scale(envelope, [_radical_inverse(i + skip, _PRIMES[d]) for d in range(len(envelope.axes))])
        for i in range(n)
    ]
