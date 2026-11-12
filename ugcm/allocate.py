"""Split a cohort of N ads across format x angle cells by weight.

Uses the largest-remainder (Hamilton) method so the counts always sum to N exactly and every
cell gets its fair share, rounded honestly. Ties break on cell order, so it is deterministic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .brief import Brief


@dataclass(frozen=True)
class Slot:
    index: int          # 1-based ad number in the cohort
    format_id: str
    angle_id: str

    @property
    def ad_id(self) -> str:
        return f"AD-{self.index:03d}"


def largest_remainder(weights: dict[str, float], total: int) -> dict[str, int]:
    if total < 0:
        raise ValueError("total must be >= 0")
    s = sum(weights.values())
    if s <= 0:
        raise ValueError("weights must sum to more than 0")
    quotas = {k: total * w / s for k, w in weights.items()}
    counts = {k: math.floor(q) for k, q in quotas.items()}
    left = total - sum(counts.values())
    order = sorted(weights, key=lambda k: (-(quotas[k] - counts[k]), list(weights).index(k)))
    for k in order[:left]:
        counts[k] += 1
    return counts


def allocate(brief: Brief, format_weights: dict[str, float] | None = None) -> list[Slot]:
    """Cell weight = format weight x angle weight. Formats default to equal weight."""
    fw = format_weights or {f.id: f.weight for f in brief.formats}
    cells = {
        (f.id, a.id): fw.get(f.id, 0.0) * a.weight
        for f in brief.formats
        for a in brief.angles
    }
    cells = {k: v for k, v in cells.items() if v > 0}
    counts = largest_remainder({f"{f}|{a}": w for (f, a), w in cells.items()}, brief.cohort_size)
    slots: list[Slot] = []
    # Interleave formats so the posting calendar does not run one format for days.
    queue = [(k.split("|")[0], k.split("|")[1], n) for k, n in counts.items() if n > 0]
    remaining = {(f, a): n for f, a, n in queue}
    i = 1
    while any(remaining.values()):
        for (f, a) in list(remaining):
            if remaining[(f, a)] > 0:
                slots.append(Slot(i, f, a))
                remaining[(f, a)] -= 1
                i += 1
    return slots
