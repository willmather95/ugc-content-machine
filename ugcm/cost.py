"""Cost estimator and spend guard.

The estimate is printed BEFORE anything paid can run. A paid backend runs only when:
  1. the price book is marked verified (placeholder prices can never be spent against),
  2. the caller passes --approve-spend with the exact cohort estimate (to the cent), and
  3. the estimate is under the hard cap in config/prices.toml.
While running, every charge is metered against the approved amount and stops at the line.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


class SpendRefused(RuntimeError):
    """Base class: a paid step was blocked. The message says why and what to pass."""


class SpendNotApproved(SpendRefused):
    pass


class SpendMismatch(SpendRefused):
    pass


class SpendOverCap(SpendRefused):
    pass


class PricesUnverified(SpendRefused):
    pass


class BudgetExceeded(SpendRefused):
    pass


@dataclass(frozen=True)
class PriceBook:
    verified: bool
    verified_on: str
    source: str
    currency: str
    models: dict[str, dict[str, Any]]
    video_reroll_rate: Decimal
    buffer_pct: Decimal
    max_cohort_usd: Decimal

    def unit(self, key: str) -> Decimal:
        return Decimal(str(self.models[key]["usd_per_unit"]))


def load_prices(path: Path) -> PriceBook:
    with open(path, "rb") as fh:
        d = tomllib.load(fh)
    return PriceBook(
        verified=bool(d.get("verified", False)),
        verified_on=d.get("verified_on", ""),
        source=d.get("source", ""),
        currency=d.get("currency", "USD"),
        models=d["models"],
        video_reroll_rate=Decimal(str(d["contingency"]["video_reroll_rate"])),
        buffer_pct=Decimal(str(d["contingency"]["buffer_pct"])),
        max_cohort_usd=money(d["cap"]["max_cohort_usd"]),
    )


@dataclass(frozen=True)
class AssetPlan:
    """What one ad needs from the paid world. Built from the gated script."""

    ad_id: str
    video_seconds: float
    voice_chars: int
    llm_tokens: int
    images: int = 1


@dataclass
class LineItem:
    ad_id: str
    item: str
    model: str
    qty: Decimal
    unit: str
    unit_usd: Decimal
    usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


@dataclass
class Estimate:
    tier: str
    clip_seconds: int
    lines: list[LineItem] = field(default_factory=list)
    prices_verified: bool = False
    buffer_pct: Decimal = Decimal(0)
    cap: Decimal = Decimal(0)

    @property
    def subtotal(self) -> Decimal:
        return money(sum((l.usd for l in self.lines), Decimal(0)))

    @property
    def buffer(self) -> Decimal:
        return money(self.subtotal * self.buffer_pct / 100)

    @property
    def total(self) -> Decimal:
        return money(self.subtotal + self.buffer)

    def per_ad(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for l in self.lines:
            out[l.ad_id] = money(out.get(l.ad_id, Decimal(0)) + l.usd)
        return out

    def by_item(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for l in self.lines:
            out[l.item] = money(out.get(l.item, Decimal(0)) + l.usd)
        return out

    def to_dict(self) -> dict[str, Any]:
        per = self.per_ad()
        return {
            "tier": self.tier,
            "clip_seconds": self.clip_seconds,
            "prices_verified": self.prices_verified,
            "warning": None if self.prices_verified else "PLACEHOLDER PRICES. Verify before use.",
            "ads": len(per),
            "per_ad_usd": {k: str(v) for k, v in per.items()},
            "per_ad_avg_usd": str(money(sum(per.values(), Decimal(0)) / len(per))) if per else "0.00",
            "by_item_usd": {k: str(v) for k, v in self.by_item().items()},
            "subtotal_usd": str(self.subtotal),
            "buffer_pct": str(self.buffer_pct),
            "buffer_usd": str(self.buffer),
            "total_usd": str(self.total),
            "cap_usd": str(self.cap),
            "lines": [l.to_dict() for l in self.lines],
        }


def billed_video_seconds(seconds: float, clip_seconds: int) -> int:
    """Video models bill whole clips. A 26s script chained from 8s clips bills 32s."""
    if seconds <= 0:
        return 0
    return math.ceil(seconds / clip_seconds) * clip_seconds


def estimate(plans: list[AssetPlan], prices: PriceBook, *, tier: str = "video-draft", clip_seconds: int = 8, llm_paid: bool = False) -> Estimate:
    if tier not in prices.models:
        raise KeyError(f"no price for tier {tier!r}")
    est = Estimate(tier=tier, clip_seconds=clip_seconds, prices_verified=prices.verified, buffer_pct=prices.buffer_pct, cap=prices.max_cohort_usd)
    v_unit, i_unit, t_unit = prices.unit(tier), prices.unit("thumb-image"), prices.unit("voice")
    l_unit = prices.unit("llm-text") if llm_paid else prices.unit("offline")
    for p in plans:
        secs = Decimal(billed_video_seconds(p.video_seconds, clip_seconds))
        rerolls = (secs * prices.video_reroll_rate).quantize(Decimal("0.01"))
        kchars = (Decimal(p.voice_chars) / 1000).quantize(Decimal("0.001"))
        mtok = (Decimal(p.llm_tokens) / 1_000_000).quantize(Decimal("0.000001"))
        est.lines += [
            LineItem(p.ad_id, "video", tier, secs, "second", v_unit, money(secs * v_unit)),
            LineItem(p.ad_id, "video re-rolls", tier, rerolls, "second", v_unit, money(rerolls * v_unit)),
            LineItem(p.ad_id, "first-frame still", "thumb-image", Decimal(p.images), "image", i_unit, money(p.images * i_unit)),
            LineItem(p.ad_id, "voiceover", "voice", kchars, "1k_chars", t_unit, money(kchars * t_unit)),
            LineItem(p.ad_id, "script LLM", "llm-text" if llm_paid else "offline", mtok, "1m_tokens", l_unit, money(mtok * l_unit)),
        ]
    return est


def parse_amount(text: str) -> Decimal:
    try:
        return money(text.replace("$", "").replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise SpendMismatch(f"--approve-spend {text!r} is not an amount like 97.42") from None


@dataclass
class Authorization:
    approved: Decimal
    spent: Decimal = Decimal("0.00")

    @property
    def remaining(self) -> Decimal:
        return money(self.approved - self.spent)

    def charge(self, amount: Decimal, what: str) -> None:
        amount = money(amount)
        if self.spent + amount > self.approved:
            raise BudgetExceeded(f"{what} would cost ${amount}; only ${self.remaining} of ${self.approved} approved is left. Stopping.")
        self.spent = money(self.spent + amount)


def authorize(est: Estimate, approve_spend: str | None, *, paid: bool) -> Authorization | None:
    """Return None for free backends. For paid ones, return an Authorization or raise."""
    if not paid:
        return None
    if not est.prices_verified:
        raise PricesUnverified(
            "config/prices.toml is marked verified = false. These are placeholder prices. "
            "Check each price at its source, update the file and set verified = true first."
        )
    if est.total > est.cap:
        raise SpendOverCap(f"estimate ${est.total} is over the hard cap ${est.cap} in config/prices.toml")
    if approve_spend is None:
        raise SpendNotApproved(f"paid run needs --approve-spend {est.total} (the exact estimate). Nothing was spent.")
    amt = parse_amount(approve_spend)
    if amt != est.total:
        raise SpendMismatch(f"--approve-spend {amt} does not match the estimate ${est.total}. Re-read the estimate and approve that exact amount.")
    return Authorization(approved=amt)


def render_estimate_md(est: Estimate, *, title: str) -> str:
    d = est.to_dict()
    lines = [f"# {title}", ""]
    if not est.prices_verified:
        lines += ["> **PLACEHOLDER PRICES. VERIFY BEFORE USE.** Every unit price comes from `config/prices.toml`, which is marked `verified = false`. No price here was checked against a live price list.", ""]
    lines += [
        f"- Video tier: `{est.tier}`, billed in {est.clip_seconds}s clips",
        f"- Ads: {d['ads']}",
        f"- Per ad (average): ${d['per_ad_avg_usd']}",
        f"- Subtotal: ${d['subtotal_usd']}",
        f"- Buffer ({d['buffer_pct']}%): ${d['buffer_usd']}",
        f"- **Cohort total: ${d['total_usd']}** (hard cap ${d['cap_usd']})",
        "",
        "| Item | USD |",
        "|---|---:|",
    ]
    lines += [f"| {k} | {v} |" for k, v in d["by_item_usd"].items()]
    lines += ["", "| Ad | USD |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in d["per_ad_usd"].items()]
    return "\n".join(lines) + "\n"
