"""SIMULATED ad performance. Not real data, not a forecast.

Used only to show one feedback round end to end. Every row it writes has source=SIMULATED and
the file name ends in .SIMULATED.csv. The "true" rates below are invented so the demo has
something for the ranking to find; the ranking code never reads them.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from .util import rng

# Invented ground truth: conversions per 1,000 views by angle, and multipliers.
TRUE_CONV_PER_1K = {"forget": 0.9, "cold": 1.3, "light": 0.5, "glow": 1.7, "returns": 0.6}
FORMAT_VIEWS = {"pov-fix": 1.15, "myth-fact": 1.0, "three-things": 0.55}
EMOTION_LIFT = {"curiosity": 1.1, "relief": 1.0, "humor": 1.05, "status": 0.9, "fomo": 0.95}
PLATFORM_VIEWS = {"tiktok": 1.3, "reels": 1.0, "shorts": 0.8}

FIELDS = ["ad_id", "platform", "posted_on", "views", "hold_3s_pct", "avg_watch_s", "shares", "saves", "profile_visits", "link_clicks", "conversions", "source"]


def _poisson(r, lam: float) -> int:
    """Knuth for small lambda, normal approximation above 30."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(r.gauss(lam, math.sqrt(lam))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= L:
            return k
        k += 1


def simulate_performance(ads: list[dict[str, Any]], calendar: list[dict[str, Any]], *, seed: int) -> list[dict[str, Any]]:
    """ads: rows from the ledger `ads` table. calendar: draft calendar rows (one per post)."""
    by_id = {a["ad_id"]: a for a in ads}
    out = []
    for post in calendar:
        a = by_id[post["ad_id"]]
        r = rng(seed, "sim", post["ad_id"], post["platform"])
        base = 1600 * FORMAT_VIEWS.get(a["format_id"], 1.0) * PLATFORM_VIEWS.get(post["platform"], 1.0) * (0.7 + 0.6 * a["final_score"] / 100)
        views = max(50, int(r.lognormvariate(math.log(base), 0.55)))
        hold = min(0.92, max(0.15, r.gauss(0.42 * EMOTION_LIFT.get(a["emotion"], 1.0), 0.06)))
        rate = TRUE_CONV_PER_1K.get(a["angle_id"], 0.8) * EMOTION_LIFT.get(a["emotion"], 1.0)
        conv = _poisson(r, views * rate / 1000)
        clicks = conv + _poisson(r, views * 0.012)
        out.append({
            "ad_id": post["ad_id"],
            "platform": post["platform"],
            "posted_on": post["date"],
            "views": views,
            "hold_3s_pct": round(hold * 100, 1),
            "avg_watch_s": round(a["seconds"] * hold * 0.9, 1),
            "shares": _poisson(r, views * 0.004),
            "saves": _poisson(r, views * 0.006),
            "profile_visits": _poisson(r, views * 0.01),
            "link_clicks": clicks,
            "conversions": conv,
            "source": "SIMULATED",
        })
    return out


def write_performance_csv(rows: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path
