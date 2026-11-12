"""Performance feedback: rank hooks, angles, formats and emotions, then write the next brief.

Ranking metric: attributed conversions per 1,000 views ("views without conversions are not a
win"). Small samples are noisy, so each group is shrunk toward the cohort-wide rate with a Beta
prior (empirical Bayes) before ranking, and P(best) comes from seeded Thompson draws.

New angle and format weights = an exploration floor + the rest split by P(best). Formats that
hit the kill rule written in the brief before launch get weight 0.
"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .brief import Brief
from .util import rng

REQUIRED = ["ad_id", "platform", "views", "conversions"]
INT_FIELDS = ["views", "shares", "saves", "profile_visits", "link_clicks", "conversions"]
FLOAT_FIELDS = ["hold_3s_pct", "avg_watch_s"]


class FeedbackError(ValueError):
    pass


@dataclass
class GroupStat:
    dimension: str
    key: str
    posts: int
    views: int
    conversions: int
    median_views: float
    hold_3s_pct: float
    raw_per_1k: float
    posterior_per_1k: float
    lo90_per_1k: float
    hi90_per_1k: float
    p_best: float
    verdict: str = "keep"


def load_performance(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise FeedbackError(f"{path}: missing columns {missing}")
        rows, seen = [], set()
        for i, raw in enumerate(reader, start=2):
            row: dict[str, Any] = dict(raw)
            for f in INT_FIELDS:
                if f in row and row[f] not in (None, ""):
                    try:
                        row[f] = int(row[f])
                    except ValueError:
                        raise FeedbackError(f"{path}:{i}: {f}={row[f]!r} is not a whole number") from None
                    if row[f] < 0:
                        raise FeedbackError(f"{path}:{i}: {f} is negative")
            for f in FLOAT_FIELDS:
                if f in row and row[f] not in (None, ""):
                    row[f] = float(row[f])
            if row["conversions"] > row["views"]:
                raise FeedbackError(f"{path}:{i}: more conversions than views")
            k = (row["ad_id"], row["platform"])
            if k in seen:
                raise FeedbackError(f"{path}:{i}: duplicate row for {k}")
            seen.add(k)
            rows.append(row)
    return rows


def _group_key(dimension: str, meta: dict[str, Any]) -> str:
    return {"angle": meta["angle_id"], "format": meta["format_id"], "emotion": meta["emotion"], "hook": meta["hook"]}[dimension]


def rank(
    ads: list[dict[str, Any]],
    perf: list[dict[str, Any]],
    dimension: str,
    *,
    seed: int = 0,
    prior_views: float = 4000.0,
    draws: int = 4000,
    keys: list[str] | None = None,
) -> list[GroupStat]:
    """Shrunk conversions-per-1k ranking for one dimension."""
    meta = {a["ad_id"]: a for a in ads}
    perf = [p for p in perf if p["ad_id"] in meta]
    if not perf:
        raise FeedbackError("no performance rows match ads in the ledger")
    tot_v = sum(p["views"] for p in perf)
    tot_c = sum(p["conversions"] for p in perf)
    p0 = (tot_c + 0.5) / (tot_v + 1.0)
    a0, b0 = p0 * prior_views, (1 - p0) * prior_views

    groups: dict[str, list[dict[str, Any]]] = {k: [] for k in (keys or [])}
    for p in perf:
        groups.setdefault(_group_key(dimension, meta[p["ad_id"]]), []).append(p)

    r = rng(seed, "thompson", dimension)
    params = {}
    for k, rows in groups.items():
        v = sum(x["views"] for x in rows)
        c = sum(x["conversions"] for x in rows)
        params[k] = (a0 + c, b0 + max(0, v - c))
    wins = {k: 0 for k in groups}
    samples: dict[str, list[float]] = {k: [] for k in groups}
    for _ in range(draws):
        best_k, best_x = None, -1.0
        for k, (a, b) in params.items():
            x = r.betavariate(a, b)
            samples[k].append(x)
            if x > best_x:
                best_k, best_x = k, x
        wins[best_k] += 1  # type: ignore[index]

    stats = []
    for k, rows in groups.items():
        v = sum(x["views"] for x in rows)
        c = sum(x["conversions"] for x in rows)
        a, b = params[k]
        s = sorted(samples[k])
        holds = [x["hold_3s_pct"] for x in rows if isinstance(x.get("hold_3s_pct"), float)]
        stats.append(GroupStat(
            dimension=dimension,
            key=k,
            posts=len(rows),
            views=v,
            conversions=c,
            median_views=float(median([x["views"] for x in rows])) if rows else 0.0,
            hold_3s_pct=round(sum(holds) / len(holds), 1) if holds else 0.0,
            raw_per_1k=round(1000 * c / v, 3) if v else 0.0,
            posterior_per_1k=round(1000 * a / (a + b), 3),
            lo90_per_1k=round(1000 * s[int(0.05 * len(s))], 3),
            hi90_per_1k=round(1000 * s[int(0.95 * len(s)) - 1], 3),
            p_best=round(wins[k] / draws, 4),
        ))
    stats.sort(key=lambda g: (-g.posterior_per_1k, g.key))
    return stats


def apply_kill_rules(format_stats: list[GroupStat], brief: Brief) -> None:
    """Skill rule: kill a format after N posts if median views are below the floor AND it drove 0 conversions."""
    t = brief.targets
    for g in format_stats:
        if g.posts < t.min_posts_before_format_kill:
            g.verdict = "too early"
        elif g.median_views < t.views_floor and g.conversions == 0:
            g.verdict = "kill"
        else:
            g.verdict = "keep"


def new_weights(stats: list[GroupStat], *, floor: float = 0.08) -> dict[str, float]:
    """Exploration floor + P(best). Killed groups get 0. Normalized so the mean weight is 1."""
    alive = [g for g in stats if g.verdict != "kill"]
    if not alive:
        raise FeedbackError("every group was killed; stop the test and rethink the brief")
    n = len(alive)
    floor = min(floor, 1.0 / n)
    raw = {g.key: floor + (1 - n * floor) * g.p_best for g in alive}
    s = sum(raw.values())
    w = {g.key: 0.0 for g in stats}
    for k, x in raw.items():
        w[k] = round(x / s * len(alive), 3)
    return w


def next_cohort_id(cohort_id: str) -> str:
    m = re.match(r"^(.*?)(\d+)$", cohort_id)
    if not m:
        return cohort_id + "-002"
    return f"{m.group(1)}{int(m.group(2)) + 1:0{len(m.group(2))}d}"


def build_next_brief(brief: Brief, angle_w: dict[str, float], format_w: dict[str, float], *, source: str) -> dict[str, Any]:
    d = dict(brief.raw)
    d["cohort_id"] = next_cohort_id(brief.cohort_id)
    d["parent_cohort"] = brief.cohort_id
    d["seed"] = brief.seed + 1
    d["angles"] = [{**a, "weight": angle_w.get(a["id"], a.get("weight", 1.0))} for a in d["angles"]]
    d["formats"] = [{**f, "weight": format_w.get(f["id"], f.get("weight", 1.0))} for f in d["formats"]]
    label = "SIMULATED performance (demo only, not real results)" if source == "SIMULATED" else f"{source} performance"
    d["feedback_note"] = f"Angle and format weights set from {label} of {brief.cohort_id}."
    return d


def feedback_round(brief: Brief, ads: list[dict[str, Any]], perf: list[dict[str, Any]], *, seed: int = 0) -> dict[str, Any]:
    sources = sorted({p.get("source", "unknown") for p in perf})
    source = sources[0] if len(sources) == 1 else "MIXED"
    angles = rank(ads, perf, "angle", seed=seed, keys=[a.id for a in brief.angles if a.weight > 0])
    formats = rank(ads, perf, "format", seed=seed, keys=[f.id for f in brief.formats if f.weight > 0])
    emotions = rank(ads, perf, "emotion", seed=seed)
    hooks = rank(ads, perf, "hook", seed=seed)
    apply_kill_rules(formats, brief)
    aw = new_weights(angles)
    fw = new_weights(formats)
    for g in angles:
        g.verdict = "up" if aw[g.key] > 1.05 else ("down" if aw[g.key] < 0.95 else "hold")
    tv = sum(p["views"] for p in perf)
    tc = sum(p["conversions"] for p in perf)
    per_1k = round(1000 * tc / tv, 3) if tv else 0.0
    target = brief.targets.conversions_per_1k_views_target
    return {
        "source": source,
        "posts": len(perf),
        "views": tv,
        "conversions": tc,
        "conversions_per_1k": per_1k,
        "target_per_1k": target,
        "test_verdict": "on or above target" if per_1k >= target else "below target: the stop rule applies if this holds at day 14-30",
        "angles": [asdict(g) for g in angles],
        "formats": [asdict(g) for g in formats],
        "emotions": [asdict(g) for g in emotions],
        "top_hooks": [asdict(g) for g in hooks[:5]],
        "angle_weights_old": {a.id: a.weight for a in brief.angles},
        "angle_weights_new": aw,
        "format_weights_old": {f.id: f.weight for f in brief.formats},
        "format_weights_new": fw,
        "next_brief": build_next_brief(brief, aw, fw, source=source),
    }


def render_report_md(fb: dict[str, Any], brief: Brief) -> str:
    sim = fb["source"] == "SIMULATED"
    out = [f"# Feedback round: {brief.cohort_id} -> {fb['next_brief']['cohort_id']}", ""]
    if sim:
        out += ["> **SIMULATED DATA.** This performance file was made by `ugcm/simulate.py` to show the loop working. It is not a real result and not a forecast.", ""]
    out += [
        f"- Posts: {fb['posts']}, views: {fb['views']:,}, attributed conversions: {fb['conversions']}",
        f"- Conversions per 1,000 views: {fb['conversions_per_1k']} (target written before launch: {fb['target_per_1k']}), {fb['test_verdict']}",
        "",
        "## Angles (ranked by shrunk conversions per 1k views)",
        "",
        "| Angle | Posts | Views | Conv | Raw /1k | Shrunk /1k | 90% band | P(best) | Old w | New w |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for g in fb["angles"]:
        out.append(f"| {g['key']} | {g['posts']} | {g['views']:,} | {g['conversions']} | {g['raw_per_1k']} | {g['posterior_per_1k']} | {g['lo90_per_1k']}-{g['hi90_per_1k']} | {g['p_best']:.2f} | {fb['angle_weights_old'][g['key']]} | {fb['angle_weights_new'][g['key']]} |")
    out += ["", "## Formats (kill rule from the brief)", "", "| Format | Posts | Median views | Conv | Shrunk /1k | Verdict | New w |", "|---|---:|---:|---:|---:|---|---:|"]
    for g in fb["formats"]:
        out.append(f"| {g['key']} | {g['posts']} | {g['median_views']:.0f} | {g['conversions']} | {g['posterior_per_1k']} | {g['verdict']} | {fb['format_weights_new'][g['key']]} |")
    out += ["", "## Emotions", "", "| Emotion | Posts | Shrunk /1k | 3s hold % |", "|---|---:|---:|---:|"]
    out += [f"| {g['key']} | {g['posts']} | {g['posterior_per_1k']} | {g['hold_3s_pct']} |" for g in fb["emotions"]]
    out += ["", "## Top 5 hooks", "", "| Hook | Posts | Conv | Shrunk /1k |", "|---|---:|---:|---:|"]
    out += [f"| {g['key']} | {g['posts']} | {g['conversions']} | {g['posterior_per_1k']} |" for g in fb["top_hooks"]]
    out += ["", "Method: Beta prior centered on the cohort-wide rate (4,000 views of weight), Thompson sampling for P(best) (4,000 seeded draws), new weight = 8% floor + the rest by P(best), mean weight 1.", ""]
    return "\n".join(out)
