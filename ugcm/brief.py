"""Cohort brief: the one page every downstream stage reads.

The brief is data, not prose. It carries the approved claim list word for word, the
targets written down before launch, and the angle weights that the feedback loop rewrites
for the next cohort.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class BriefError(ValueError):
    """The brief is incomplete or fails the fit check. The pipeline stops."""


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    beats: str
    weight: float = 1.0


@dataclass(frozen=True)
class Angle:
    id: str
    name: str
    weight: float = 1.0


@dataclass(frozen=True)
class Brand:
    name: str
    product: str
    price_usd: float
    url: str
    required_words: tuple[str, ...]
    spelling: dict[str, str]
    forbidden_words: tuple[str, ...]
    competitors: tuple[str, ...]


@dataclass(frozen=True)
class Disclosure:
    ai_label: bool
    paid_partnership: bool
    affiliate: bool


@dataclass(frozen=True)
class Targets:
    views_floor: int
    conversions_per_1k_views_target: float
    min_posts_before_format_kill: int


@dataclass(frozen=True)
class Brief:
    cohort_id: str
    as_of: str
    seed: int
    cohort_size: int
    brand: Brand
    goal: str
    cta: str
    audience: str
    approved_claims: tuple[str, ...]
    disclosure: Disclosure
    fit: dict[str, bool]
    targets: Targets
    formats: tuple[Format, ...]
    angles: tuple[Angle, ...]
    emotions: tuple[str, ...]
    platforms: tuple[str, ...]
    length_s: tuple[int, int]
    aspect: str
    note: str = ""
    parent_cohort: str | None = None
    feedback_note: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    # --- lookups -------------------------------------------------------------------
    def format_by_id(self, fid: str) -> Format:
        for f in self.formats:
            if f.id == fid:
                return f
        raise KeyError(fid)

    def angle_by_id(self, aid: str) -> Angle:
        for a in self.angles:
            if a.id == aid:
                return a
        raise KeyError(aid)

    def fit_score(self) -> int:
        return sum(1 for v in self.fit.values() if v)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


REQUIRED = ["cohort_id", "brand", "cta", "approved_claims", "formats", "angles", "cohort_size", "targets", "fit"]


def parse_brief(data: dict[str, Any]) -> Brief:
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise BriefError(f"brief is missing: {', '.join(missing)}")
    if not data["approved_claims"]:
        # The skill's rule: health, money and legal claims must match an approved list.
        # No list means stop and ask for one.
        raise BriefError("approved_claims is empty. Stop and ask the owner for the approved claim list.")
    b = data["brand"]
    brand = Brand(
        name=b["name"],
        product=b["product"],
        price_usd=float(b["price_usd"]),
        url=b["url"],
        required_words=tuple(b.get("required_words", [b["name"]])),
        spelling=dict(b.get("spelling", {})),
        forbidden_words=tuple(b.get("forbidden_words", [])),
        competitors=tuple(b.get("competitors", [])),
    )
    t = data["targets"]
    brief = Brief(
        cohort_id=data["cohort_id"],
        as_of=data.get("as_of", "1970-01-01"),
        seed=int(data.get("seed", 0)),
        cohort_size=int(data["cohort_size"]),
        brand=brand,
        goal=data.get("goal", ""),
        cta=data["cta"],
        audience=data.get("audience", ""),
        approved_claims=tuple(data["approved_claims"]),
        disclosure=Disclosure(**data.get("disclosure", {"ai_label": True, "paid_partnership": False, "affiliate": False})),
        fit={k: bool(v) for k, v in data["fit"].items()},
        targets=Targets(
            views_floor=int(t["views_floor"]),
            conversions_per_1k_views_target=float(t["conversions_per_1k_views_target"]),
            min_posts_before_format_kill=int(t.get("min_posts_before_format_kill", 10)),
        ),
        formats=tuple(Format(id=f["id"], name=f["name"], beats=f.get("beats", ""), weight=float(f.get("weight", 1.0))) for f in data["formats"]),
        angles=tuple(Angle(id=a["id"], name=a["name"], weight=float(a.get("weight", 1.0))) for a in data["angles"]),
        emotions=tuple(data.get("emotions", ["curiosity"])),
        platforms=tuple(data.get("platforms", ["tiktok"])),
        length_s=tuple(data.get("length_s", [15, 35])),  # type: ignore[arg-type]
        aspect=data.get("aspect", "9:16"),
        note=data.get("note", ""),
        parent_cohort=data.get("parent_cohort"),
        feedback_note=data.get("feedback_note"),
        raw=data,
    )
    validate(brief)
    return brief


def validate(brief: Brief) -> None:
    if brief.fit_score() < 3:
        no = [k for k, v in brief.fit.items() if not v]
        raise BriefError(f"not a fit: only {brief.fit_score()} of {len(brief.fit)} fit checks pass (no: {', '.join(no)})")
    if brief.cohort_size < 1:
        raise BriefError("cohort_size must be at least 1")
    if not brief.formats or not brief.angles:
        raise BriefError("need at least one format and one angle")
    if sum(a.weight for a in brief.angles) <= 0:
        raise BriefError("angle weights must sum to more than 0")
    if sum(f.weight for f in brief.formats) <= 0:
        raise BriefError("format weights must sum to more than 0 (every format was killed)")
    if any(a.weight < 0 for a in brief.angles) or any(f.weight < 0 for f in brief.formats):
        raise BriefError("weights cannot be negative")
    ids = [f.id for f in brief.formats] + [a.id for a in brief.angles]
    if len(ids) != len(set(ids)):
        raise BriefError("format and angle ids must be unique")


def load_brief(path: Path) -> Brief:
    return parse_brief(json.loads(Path(path).read_text(encoding="utf-8")))
