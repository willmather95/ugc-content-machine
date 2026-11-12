"""Draft posting calendar. Everything is a DRAFT until the owner approves it.

Rules from the playbook: one account per network, at most 2 posts a day per account, the same
post windows every day, hold 14 days before judging. Each network gets the passed ads in a
different order, so the same cut is not live everywhere on the same day.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any

WINDOWS = ["11:30", "19:00"]  # local time, same every day


def build_calendar(ad_ids: list[str], platforms: tuple[str, ...], start: date, *, days: int = 14, per_day: int = 2) -> list[dict[str, Any]]:
    if per_day > len(WINDOWS):
        raise ValueError(f"at most {len(WINDOWS)} posts a day per account")
    rows: list[dict[str, Any]] = []
    if not ad_ids:
        return rows
    slots = days * per_day
    for p_i, plat in enumerate(platforms):
        offset = (p_i * len(ad_ids)) // max(1, len(platforms))
        order = ad_ids[offset:] + ad_ids[:offset]
        for n in range(min(slots, len(order))):
            day, w = divmod(n, per_day)
            rows.append({
                "date": (start + timedelta(days=day)).isoformat(),
                "window": WINDOWS[w],
                "platform": plat,
                "ad_id": order[n],
                "status": "draft",
                "needs": "owner approval",
            })
    rows.sort(key=lambda r: (r["date"], r["window"], r["platform"]))
    return rows


def write_calendar(rows: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "window", "platform", "ad_id", "status", "needs"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path
