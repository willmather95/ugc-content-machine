"""Command line: python -m ugcm <command>

  run       build one cohort (hooks, scripts, gate, estimate, generation, ledger, sheet, calendar)
  simulate  write a SIMULATED performance CSV for a cohort (demo only)
  feedback  rank hooks/angles from a performance CSV and write the next cohort's brief
  verify    re-hash every asset against the ledger and re-check the fingerprint
  demo      run cohort 1 -> simulated feedback -> cohort 2, end to end, offline
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

from .brief import BriefError, load_brief, parse_brief
from .cost import SpendRefused, load_prices
from .feedback import FeedbackError, feedback_round, load_performance, render_report_md
from .gate import load_policy
from .generation import DryRunBackend, TregBackend
from .ledger import Ledger
from .pipeline import run_cohort
from .providers import ProviderRefused, make_provider
from .simulate import simulate_performance, write_performance_csv
from .util import pretty_json, write_text

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--policy", type=Path, default=CONFIG / "policy.toml")
    p.add_argument("--prices", type=Path, default=CONFIG / "prices.toml")


def cmd_run(a: argparse.Namespace) -> int:
    brief = load_brief(a.brief)
    policy, prices = load_policy(a.policy), load_prices(a.prices)
    provider = make_provider(a.provider) if a.provider == "offline" else make_provider(a.provider, allow_network=a.allow_network)
    backend = DryRunBackend() if a.backend == "dryrun" else TregBackend(prices)
    out = a.out / brief.cohort_id
    r = run_cohort(
        brief=brief, policy=policy, prices=prices, provider=provider, backend=backend, out_dir=out,
        brief_path=a.brief, policy_path=a.policy, prices_path=a.prices, approve_spend=a.approve_spend, tier=a.tier,
    )
    print(f"\n{r.summary['passed']} of {r.summary['ads_written']} passed the gate. Output: {out}")
    return 0


def _ledger_ads(cohort: Path) -> list[dict]:
    with Ledger(cohort / "ledger.sqlite") as led:
        return led.rows("SELECT ad_id, format_id, angle_id, emotion, hook, seconds, status, final_score FROM ads ORDER BY ad_id")


def _read_calendar(cohort: Path) -> list[dict]:
    with open(cohort / "calendar.DRAFT.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def cmd_simulate(a: argparse.Namespace) -> int:
    ads = _ledger_ads(a.cohort)
    rows = simulate_performance(ads, _read_calendar(a.cohort), seed=a.seed)
    path = write_performance_csv(rows, a.cohort / "performance.SIMULATED.csv")
    print(f"SIMULATED performance for {len(rows)} draft posts -> {path}  (source=SIMULATED on every row)")
    return 0


def cmd_feedback(a: argparse.Namespace) -> int:
    brief = parse_brief(_brief_from_cohort(a.cohort))
    ads = _ledger_ads(a.cohort)
    perf = load_performance(a.perf)
    fb = feedback_round(brief, ads, perf, seed=brief.seed)
    write_text(a.cohort / "feedback.json", pretty_json(fb))
    write_text(a.cohort / "feedback-report.md", render_report_md(fb, brief))
    next_path = a.next_brief or (a.cohort.parent / f"{fb['next_brief']['cohort_id']}.brief.json")
    write_text(next_path, pretty_json(fb["next_brief"]))
    with Ledger(a.cohort / "ledger.sqlite") as led:
        run_id = led.rows("SELECT run_id FROM runs")[0]["run_id"]
        led.db.execute("DELETE FROM feedback WHERE run_id = ?", (run_id,))
        rows = []
        for dim, old, new in (("angle", fb["angle_weights_old"], fb["angle_weights_new"]), ("format", fb["format_weights_old"], fb["format_weights_new"])):
            for g in fb[dim + "s"]:
                rows.append({"run_id": run_id, "source": fb["source"], "dimension": dim, "key": g["key"], "posts": g["posts"], "views": g["views"], "conversions": g["conversions"], "posterior_per_1k": g["posterior_per_1k"], "p_best": g["p_best"], "old_weight": old.get(g["key"], 0.0), "new_weight": new.get(g["key"], 0.0), "verdict": g["verdict"]})
        led.add_feedback(rows)
    label = " [SIMULATED]" if fb["source"] == "SIMULATED" else ""
    print(f"feedback{label}: {fb['posts']} posts, {fb['conversions_per_1k']} conv/1k views (target {fb['target_per_1k']})")
    print("angle weights: " + ", ".join(f"{k} {fb['angle_weights_old'][k]}->{v}" for k, v in fb["angle_weights_new"].items()))
    print(f"next brief -> {next_path}")
    return 0


def _brief_from_cohort(cohort: Path):
    """The cohort folder keeps the source brief (with raw fields) next to the ledger."""
    src = cohort / "brief.source.json"
    if src.exists():
        return json.loads(src.read_text(encoding="utf-8"))
    raise BriefError(f"{src} not found; run `ugcm run` first")


def cmd_verify(a: argparse.Namespace) -> int:
    summary = json.loads((a.cohort / "summary.json").read_text(encoding="utf-8"))
    with Ledger(a.cohort / "ledger.sqlite") as led:
        problems = led.verify_assets(a.cohort)
        fp = led.fingerprint()
        n = led.counts()["assets"]
    if fp != summary["ledger_fingerprint"]:
        problems.append(f"fingerprint changed: ledger {fp[:16]} vs summary {summary['ledger_fingerprint'][:16]}")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"ok: {n} assets re-hashed, fingerprint {fp[:16]} matches")
    return 0


def cmd_demo(a: argparse.Namespace) -> int:
    out: Path = a.out
    if out.exists() and a.clean:
        shutil.rmtree(out)
    print("== cohort 1: offline provider + dry-run backend (no network, no spend)")
    rc = cmd_run(argparse.Namespace(brief=a.brief, out=out, provider="offline", backend="dryrun", approve_spend=None, allow_network=False, tier="video-draft", policy=a.policy, prices=a.prices))
    first = json.loads(a.brief.read_text(encoding="utf-8"))
    c1 = out / first["cohort_id"]
    print("\n== SIMULATED feedback round (made-up performance, labeled SIMULATED)")
    cmd_simulate(argparse.Namespace(cohort=c1, seed=first.get("seed", 0)))
    cmd_feedback(argparse.Namespace(cohort=c1, perf=c1 / "performance.SIMULATED.csv", next_brief=None))
    fb = json.loads((c1 / "feedback.json").read_text(encoding="utf-8"))
    nb = out / f"{fb['next_brief']['cohort_id']}.brief.json"
    print(f"\n== cohort 2 from the reweighted brief ({nb.name})")
    cmd_run(argparse.Namespace(brief=nb, out=out, provider="offline", backend="dryrun", approve_spend=None, allow_network=False, tier="video-draft", policy=a.policy, prices=a.prices))
    c2 = out / fb["next_brief"]["cohort_id"]
    s1 = json.loads((c1 / "summary.json").read_text(encoding="utf-8"))
    s2 = json.loads((c2 / "summary.json").read_text(encoding="utf-8"))
    demo = {
        "note": "Numbers from `make demo`: offline templated provider, dry-run backend, SIMULATED feedback. Fernway is a made-up brand.",
        "cohort_1": s1,
        "feedback": {k: fb[k] for k in ("source", "posts", "views", "conversions", "conversions_per_1k", "target_per_1k", "angle_weights_old", "angle_weights_new", "format_weights_new", "angles", "formats", "top_hooks")},
        "cohort_2": s2,
    }
    write_text(out / "demo-summary.json", pretty_json(demo))
    print("\n== verify ledgers")
    rc |= cmd_verify(argparse.Namespace(cohort=c1)) | cmd_verify(argparse.Namespace(cohort=c2))
    print(f"\ndemo done -> {out}/  (contact sheets: {c1.name}/contact-sheet.png, {c2.name}/contact-sheet.png)")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ugcm", description="30-ad UGC cohort pipeline. Offline and spend-safe by default.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="build one cohort")
    p.add_argument("--brief", type=Path, default=CONFIG / "brief.json")
    p.add_argument("--out", type=Path, default=ROOT / "out")
    p.add_argument("--provider", choices=["offline", "anthropic", "openai"], default="offline")
    p.add_argument("--backend", choices=["dryrun", "treg"], default="dryrun")
    p.add_argument("--tier", choices=["video-draft", "video-quality"], default="video-draft")
    p.add_argument("--approve-spend", dest="approve_spend", default=None, help="exact cohort estimate in USD; required for paid backends")
    p.add_argument("--allow-network", dest="allow_network", action="store_true", help="let a real LLM provider call its API (needs its key)")
    _common(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("simulate", help="write SIMULATED performance for a cohort (demo only)")
    p.add_argument("--cohort", type=Path, required=True)
    p.add_argument("--seed", type=int, default=95)
    p.set_defaults(fn=cmd_simulate)

    p = sub.add_parser("feedback", help="rank hooks/angles from a performance CSV, write next brief")
    p.add_argument("--cohort", type=Path, required=True)
    p.add_argument("--perf", type=Path, required=True)
    p.add_argument("--next-brief", dest="next_brief", type=Path, default=None)
    p.set_defaults(fn=cmd_feedback)

    p = sub.add_parser("verify", help="re-hash assets and check the ledger fingerprint")
    p.add_argument("--cohort", type=Path, required=True)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("demo", help="cohort 1 -> SIMULATED feedback -> cohort 2")
    p.add_argument("--brief", type=Path, default=CONFIG / "brief.json")
    p.add_argument("--out", type=Path, default=ROOT / "out")
    p.add_argument("--no-clean", dest="clean", action="store_false")
    _common(p)
    p.set_defaults(fn=cmd_demo)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except SpendRefused as e:
        print(f"\nREFUSED (no spend): {e}", file=sys.stderr)
        return 3
    except ProviderRefused as e:
        print(f"\nREFUSED (no network): {e}", file=sys.stderr)
        return 3
    except (BriefError, FeedbackError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
