"""The cohort pipeline:

    brief -> hook bank -> allocation -> scripts -> quality gate (with regeneration)
          -> cost estimate (printed first) -> spend authorization -> generation
          -> ledger -> contact sheet -> draft calendar -> summary

Nothing here posts, spends or calls the network unless a paid provider/backend is passed in
explicitly AND authorized.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .allocate import Slot, allocate
from .brief import Brief
from .calendar import build_calendar, write_calendar
from .cost import AssetPlan, Estimate, PriceBook, authorize, estimate, render_estimate_md
from .gate import Failure, GateResult, QualityGate
from .generation import GenerationBackend, build_requests
from .ledger import Ledger
from .prompts import hooks_prompt, script_prompt
from .providers import LLMProvider, ProviderError
from .providers.base import Completion
from .render import contact_sheet_png, contact_sheet_svg
from .util import canonical_json, derive_seed, pretty_json, sha256_text, write_text

HOOKS_PER_CELL = 4


@dataclass
class AdRecord:
    slot: Slot
    hook: dict
    script: dict | None
    gate: GateResult | None
    attempts: list[GateResult] = field(default_factory=list)
    tokens: int = 0

    @property
    def status(self) -> str:
        return "passed" if self.gate and self.gate.passed else "held"

    @property
    def regenerations(self) -> int:
        return max(0, len(self.attempts) - 1)


@dataclass
class CohortResult:
    out_dir: Path
    run_id: str
    ads: list[AdRecord]
    estimate_run: Estimate
    estimate_paid: Estimate
    fingerprint: str
    summary: dict[str, Any]


def _sha_file_text(path: Path) -> str:
    return sha256_text(Path(path).read_text(encoding="utf-8"))[:16]


def run_cohort(
    *,
    brief: Brief,
    policy: dict[str, Any],
    prices: PriceBook,
    provider: LLMProvider,
    backend: GenerationBackend,
    out_dir: Path,
    brief_path: Path | None = None,
    policy_path: Path | None = None,
    prices_path: Path | None = None,
    approve_spend: str | None = None,
    tier: str = "video-draft",
    clip_seconds: int = 8,
    log: Callable[[str], None] = print,
) -> CohortResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gate = QualityGate(brief, policy)
    max_regen = int(policy["rubric"]["max_regenerations"])

    brief_sha = sha256_text(canonical_json(brief.to_dict()))[:16]
    policy_sha = _sha_file_text(policy_path) if policy_path else sha256_text(canonical_json(policy))[:16]
    prices_sha = _sha_file_text(prices_path) if prices_path else "inline"
    run_id = f"{brief.cohort_id}-{sha256_text(canonical_json([brief_sha, policy_sha, provider.name, provider.model, brief.seed]))[:8]}"

    ledger = Ledger(out_dir / "ledger.sqlite", fresh=True)
    try:
        ledger.add_run(
            run_id=run_id, cohort_id=brief.cohort_id, created_at=brief.as_of, provider=provider.name,
            model=provider.model, provider_deterministic=int(provider.deterministic), backend=backend.name,
            policy_version=policy.get("version", "?"), brief_sha=brief_sha, policy_sha=policy_sha,
            prices_sha=prices_sha, seed=brief.seed,
        )
        write_text(out_dir / "brief.json", pretty_json(brief.to_dict()))
        write_text(out_dir / "brief.source.json", pretty_json(brief.raw))

        def call(spec, *, ad_id: str | None, stage: str, attempt: int) -> tuple[Completion, Any]:
            comp = provider.complete(spec)
            ledger.add_prompt(run_id=run_id, ad_id=ad_id, stage=stage, attempt=attempt, spec=spec, completion=comp)
            return comp, comp.json()

        # ---- 1. allocation: how many ads each format x angle cell gets ------------------------
        slots = allocate(brief)
        need: dict[tuple[str, str], int] = {}
        for sl in slots:
            need[(sl.format_id, sl.angle_id)] = need.get((sl.format_id, sl.angle_id), 0) + 1
        log(f"[1/7] allocation: {len(slots)} ads across {len(need)} format x angle cells")

        # ---- 2. hook bank: at least 4 per cell (20 per format), more where the cell needs more --
        log(f"[2/7] hook bank: {len(brief.formats)} formats x {len(brief.angles)} angles, >= {HOOKS_PER_CELL} hooks per cell")
        bank: dict[tuple[str, str], list[dict]] = {}
        bank_rows = []
        hook_tokens: dict[tuple[str, str], int] = {}
        for fmt in brief.formats:
            for angle in brief.angles:
                cell = (fmt.id, angle.id)
                avoid: list[str] = []
                kept: list[dict] = []
                hook_tokens[cell] = 0
                for attempt in range(2):
                    n_hooks = max(HOOKS_PER_CELL, need.get(cell, 0) + 2)
                    spec = hooks_prompt(brief=brief, fmt=fmt, angle=angle, n=n_hooks, seed=derive_seed(brief.seed, "hooks", fmt.id, angle.id, attempt), avoid=avoid)
                    comp, data = call(spec, ad_id=None, stage="hooks", attempt=attempt)
                    hook_tokens[cell] += comp.input_tokens + comp.output_tokens
                    seen = {h["line"] for h in kept}
                    codes: list[str] = []
                    for h in data.get("hooks", []):
                        fails = gate.check_hook(h)
                        bank_rows.append({"format": fmt.id, "angle": angle.id, "attempt": attempt, **h, "prescreen": "pass" if not fails else ",".join(f.code for f in fails)})
                        if not fails and h["line"] not in seen:
                            kept.append(h)
                            seen.add(h["line"])
                        codes += [f.code for f in fails]
                    if len(kept) >= max(2, need.get(cell, 0)):
                        break
                    avoid = sorted(set(codes))
                kept.sort(key=lambda h: -gate.hook_score(h))
                bank[cell] = kept
        write_text(out_dir / "hook-bank.json", pretty_json(bank_rows))


        # ---- 3. scripts + gate + regeneration --------------------------------------------------
        log(f"[3/7] scripts + quality gate (max {max_regen} regenerations per ad)")
        used: dict[tuple[str, str], int] = {}
        lines_used: set[str] = set()  # no two ads in a cohort open on the same line (near-copies lose reach)
        ads: list[AdRecord] = []
        for slot in slots:
            cell = (slot.format_id, slot.angle_id)
            candidates = bank.get(cell, [])
            fmt, angle = brief.format_by_id(slot.format_id), brief.angle_by_id(slot.angle_id)
            if not candidates:
                rec = AdRecord(slot, {"line": "(no hook passed pre-screen)", "first_frame": "", "emotion": ""}, None, None)
                ads.append(rec)
                continue
            hook = _pick_hook(candidates, used, cell, lines_used)
            lines_used.add(hook["line"])
            rec = AdRecord(slot, hook, None, None, tokens=hook_tokens[cell] // max(1, need[cell]))
            avoid = []
            for attempt in range(max_regen + 1):
                spec = script_prompt(brief=brief, policy=policy, fmt=fmt, angle=angle, hook=rec.hook, seed=derive_seed(brief.seed, "script", slot.ad_id, attempt), avoid=avoid)
                try:
                    comp, script = call(spec, ad_id=slot.ad_id, stage="script", attempt=attempt)
                    rec.tokens += comp.input_tokens + comp.output_tokens
                    result = gate.check(rec.hook, script, brief.platforms)
                except (ProviderError, KeyError, TypeError) as exc:
                    script = {"beats": [], "caption": ""}
                    result = GateResult(False, 0, [Failure("BAD_REPLY", f"reply did not match the schema: {exc}", "script")])
                ledger.add_gate(run_id=run_id, ad_id=slot.ad_id, attempt=attempt, result=result)
                rec.attempts.append(result)
                rec.script, rec.gate = script, result
                if result.passed:
                    break
                if any(f.stage == "hook" for f in result.failures):
                    rec.hook = _pick_hook(candidates, used, cell, lines_used)
                    lines_used.add(rec.hook["line"])
                avoid = sorted({f.code for f in result.failures if f.stage == "script"})
            ads.append(rec)
            _write_ad_files(out_dir / "ads" / slot.ad_id, rec, brief, provider.name)

        passed = [a for a in ads if a.status == "passed"]
        for a in ads:
            secs = a.gate.metrics.get("seconds", 0.0) if a.gate else 0.0
            ledger.add_ad(
                run_id=run_id, ad_id=a.slot.ad_id, format_id=a.slot.format_id, angle_id=a.slot.angle_id,
                emotion=a.hook.get("emotion", ""), hook=a.hook["line"], first_frame=a.hook.get("first_frame", ""),
                script_json=canonical_json(a.script or {}), script_sha=sha256_text(canonical_json(a.script or {}))[:16],
                seconds=secs, status=a.status, regenerations=a.regenerations, final_score=a.gate.score if a.gate else 0,
            )
        log(f"      passed {len(passed)} of {len(ads)}; held {len(ads) - len(passed)}")

        # ---- 4. cost estimate, printed BEFORE anything can spend -------------------------------
        plans = [
            AssetPlan(a.slot.ad_id, a.gate.metrics["seconds"], len(" ".join(b["say"] for b in a.script["beats"])), a.tokens)
            for a in passed
        ]
        est_run = estimate(plans, prices, tier=tier, clip_seconds=clip_seconds, llm_paid=provider.is_paid)
        est_paid = estimate(plans, prices, tier=tier, clip_seconds=clip_seconds, llm_paid=True)
        ledger.add_costs(run_id, est_run)
        write_text(out_dir / "cost-estimate.json", pretty_json({"this_run": est_run.to_dict(), "if_paid": est_paid.to_dict(), "backend": backend.name}))
        write_text(
            out_dir / "cost-estimate.md",
            render_estimate_md(est_paid, title=f"Cost estimate: {brief.cohort_id} as a PAID run ({len(plans)} ads, {tier})")
            + f"\nThis run used backend `{backend.name}` and provider `{provider.name}`. "
            + ("Nothing was spent." if not backend.is_paid and not provider.is_paid else "Paid backend: see authorization.")
            + "\n",
        )
        log(f"[4/7] cost estimate (before any paid step): paid-route total ${est_paid.total} for {len(plans)} ads"
            + ("" if prices.verified else "  [PLACEHOLDER PRICES, verify before use]"))
        log(f"      this run: backend={backend.name} provider={provider.name} -> ${est_run.total if backend.is_paid else '0.00'} spent")

        # ---- 5. spend authorization (raises for paid backends without the exact approval) -------
        auth = authorize(est_run, approve_spend, paid=backend.is_paid)

        # ---- 6. generation --------------------------------------------------------------------
        log(f"[5/7] generation via {backend.name}")
        angle_index = {a.id: i for i, a in enumerate(brief.angles)}
        for a in passed:
            ad_dir = out_dir / "ads" / a.slot.ad_id
            reqs = build_requests(
                ad_id=a.slot.ad_id, script=a.script, hook=a.hook, seconds=a.gate.metrics["seconds"],
                seed=derive_seed(brief.seed, "gen", a.slot.ad_id), prices=prices, tier=tier,
                clip_seconds=clip_seconds, aspect=brief.aspect,
            )
            ctx = _ctx(a, brief, angle_index)
            for req in reqs:
                res = backend.submit(req, ad_dir, auth, ctx)
                ledger.add_asset(run_id=run_id, ad_id=a.slot.ad_id, kind=f"request:{req.kind}", path=res.request_path, root=out_dir, backend=backend.name, payload_sha=res.payload_sha, status=res.status)
                for asset in res.assets:
                    ledger.add_asset(run_id=run_id, ad_id=a.slot.ad_id, kind=f"placeholder:{asset.suffix.lstrip('.')}", path=asset, root=out_dir, backend=backend.name, payload_sha=res.payload_sha, status=res.status)

        # ---- 7. contact sheet, calendar, summary -----------------------------------------------
        items = [{**_ctx(a, brief, angle_index), "status": a.status, "codes": a.gate.codes if a.gate else ["NO_HOOK"], "regenerations": a.regenerations} for a in ads]
        title = f"{brief.cohort_id}: {len(passed)} of {len(ads)} ads passed the gate"
        sub = f"{brief.brand.name} (made-up brand) · offline provider · dry-run placeholders, not generated video · {brief.as_of}"
        write_text(out_dir / "contact-sheet.svg", contact_sheet_svg(items, title=title, subtitle=sub))
        sheet = contact_sheet_png(items, out_dir / "contact-sheet.png", title=title, subtitle=sub)
        log(f"[6/7] contact sheet: {'contact-sheet.png + ' if sheet else ''}contact-sheet.svg")

        start = date.fromisoformat(brief.as_of) + timedelta(days=1)
        cal = build_calendar([a.slot.ad_id for a in passed], brief.platforms, start)
        write_calendar(cal, out_dir / "calendar.DRAFT.csv")

        first_try = sum(1 for a in ads if a.status == "passed" and a.regenerations == 0)
        first_codes: dict[str, int] = {}
        for a in ads:
            if a.attempts:
                for c in sorted(set(a.attempts[0].codes)):
                    first_codes[c] = first_codes.get(c, 0) + 1
        held = [{"ad_id": a.slot.ad_id, "codes": a.gate.codes if a.gate else ["NO_HOOK"], "reasons": [f.reason for f in a.gate.failures] if a.gate else []} for a in ads if a.status != "passed"]
        ledger.commit()
        fp = ledger.fingerprint()
        summary = {
            "cohort_id": brief.cohort_id,
            "run_id": run_id,
            "provider": provider.name,
            "provider_label": "OFFLINE deterministic templated provider" if provider.name == "offline" else provider.name,
            "backend": backend.name,
            "ads_written": len(ads),
            "passed_first_try": first_try,
            "passed_after_regen": len(passed) - first_try,
            "passed": len(passed),
            "held": len(ads) - len(passed),
            "regenerations": sum(a.regenerations for a in ads),
            "unique_hooks": len({a.hook["line"] for a in ads}),
            "first_attempt_failure_codes": dict(sorted(first_codes.items(), key=lambda kv: (-kv[1], kv[0]))),
            "held_ads": held,
            "allocation": _allocation(slots),
            "angle_weights": {a.id: a.weight for a in brief.angles},
            "format_weights": {f.id: f.weight for f in brief.formats},
            "median_score_passed": _median([a.gate.score for a in passed]),
            "estimate_paid_total_usd": str(est_paid.total),
            "estimate_paid_per_ad_usd": est_paid.to_dict()["per_ad_avg_usd"],
            "prices_verified": prices.verified,
            "spent_usd": "0.00" if not backend.is_paid else str(auth.spent if auth else "0.00"),
            "calendar_drafts": len(cal),
            "ledger_fingerprint": fp,
            "ledger_rows": ledger.counts(),
        }
        write_text(out_dir / "summary.json", pretty_json(summary))
        write_text(out_dir / "owner-summary.md", _owner_summary(summary, brief, est_paid))
        ledger.close()
        log(f"[7/7] ledger: {out_dir / 'ledger.sqlite'}  fingerprint {fp[:16]}")
        return CohortResult(out_dir, run_id, ads, est_run, est_paid, fp, summary)
    except BaseException:
        ledger.close()  # keep the partial ledger (estimate included) and release the file
        raise


# ------------------------------------------------------------------------------------------
def _ctx(a: AdRecord, brief: Brief, angle_index: dict[str, int]) -> dict[str, Any]:
    return {
        "ad_id": a.slot.ad_id,
        "hook": a.hook["line"],
        "first_frame": a.hook.get("first_frame", ""),
        "format_name": brief.format_by_id(a.slot.format_id).name,
        "angle_name": brief.angle_by_id(a.slot.angle_id).name,
        "angle_index": angle_index[a.slot.angle_id],
        "score": a.gate.score if a.gate else 0,
    }


def _pick_hook(candidates: list[dict], used: dict, cell: tuple[str, str], lines_used: set[str]) -> dict:
    """Next best hook for the cell, skipping lines already used anywhere in the cohort.
    Falls back to reuse only when the cell has no fresh line left."""
    start = used.get(cell, 0)
    n = len(candidates)
    for k in range(n):
        h = candidates[(start + k) % n]
        if h["line"] not in lines_used:
            used[cell] = start + k + 1
            return h
    used[cell] = start + 1
    return candidates[start % n]


def _allocation(slots: list[Slot]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for s in slots:
        out.setdefault(s.angle_id, {})
        out[s.angle_id][s.format_id] = out[s.angle_id].get(s.format_id, 0) + 1
    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _write_ad_files(ad_dir: Path, rec: AdRecord, brief: Brief, provider_name: str) -> None:
    s = rec.script or {"beats": [], "caption": ""}
    write_text(ad_dir / "script.json", pretty_json({"ad_id": rec.slot.ad_id, "format": rec.slot.format_id, "angle": rec.slot.angle_id, "hook": rec.hook, **s}))
    write_text(ad_dir / "gate.json", pretty_json({"final": rec.gate.to_dict() if rec.gate else None, "attempts": [g.to_dict() for g in rec.attempts]}))
    lines = [
        f"# {rec.slot.ad_id} · {brief.format_by_id(rec.slot.format_id).name} · {brief.angle_by_id(rec.slot.angle_id).name}",
        "",
        f"**Status:** {rec.status.upper()}" + (f" (score {rec.gate.score}, {rec.regenerations} regenerations)" if rec.gate else ""),
        f"**Hook ({rec.hook.get('emotion', '')}):** {rec.hook['line']}",
        f"**First frame:** {rec.hook.get('first_frame', '')}",
        "",
        "| Time | Say | Show |",
        "|---|---|---|",
    ]
    lines += [f"| {b['t']} | {b['say']} | {b['show']} |" for b in s.get("beats", [])]
    lines += ["", f"**Caption:** {s.get('caption', '')}", ""]
    if rec.gate and not rec.gate.passed:
        lines += ["**Held because:**", *[f"- `{f.code}` {f.reason}" for f in rec.gate.failures], ""]
    lines += [f"_Written by provider `{provider_name}`. Nothing was generated or posted from this file._"]
    write_text(ad_dir / "script.md", "\n".join(lines) + "\n")


def _owner_summary(s: dict[str, Any], brief: Brief, est: Estimate) -> str:
    held = "\n".join(f"- {h['ad_id']}: {', '.join(h['codes'])}" for h in s["held_ads"]) or "- none"
    return f"""# Owner summary: {s['cohort_id']}

Nothing has been posted, scheduled or paid for. Every calendar row is a DRAFT.

- Ads written: {s['ads_written']}
- Passed the gate: {s['passed']} ({s['passed_first_try']} first try, {s['passed_after_regen']} after regeneration)
- Held back: {s['held']}
- Draft calendar rows: {s['calendar_drafts']} across {', '.join(brief.platforms)} (max 2 a day per account, 14 days)
- Paid-run estimate if approved: ${s['estimate_paid_total_usd']}{'' if s['prices_verified'] else ' (PLACEHOLDER PRICES, verify before use)'}

## Held back, with reasons
{held}

## Yes/no approvals needed from the owner
1. Approve the draft calendar (`calendar.DRAFT.csv`) before anything flips from draft to scheduled.
2. Approve the paid generation run at exactly ${est.total} (pass `--approve-spend {est.total}` after prices are verified).
3. Confirm the approved claim list is current: {len(brief.approved_claims)} claims in the brief.
4. Confirm AI label use on every AI-made video (brief disclosure.ai_label = {str(brief.disclosure.ai_label).lower()}).
5. Confirm targets written before launch: views floor {brief.targets.views_floor}, {brief.targets.conversions_per_1k_views_target} conversions per 1k views.
"""


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

