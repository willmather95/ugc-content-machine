"""Render docs/hero.png from the demo's own output (out/demo-summary.json).

Every number in the image is read from that file. Style follows the world-class-charts tokens
(light theme): one highlighted series, direct labels, takeaway in the subtitle, source line.
Needs Google Chrome or Chromium for the screenshot; the HTML is written next to the PNG either way.

    python scripts/render_hero.py --demo out/demo-summary.json --out docs/hero.png
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

T = {
    "page": "#f2f2f1", "surface": "#ffffff", "hair": "#e4e4e3", "ink": "#141414", "ink2": "#3f3f3f",
    "muted": "#6b6b6b", "accent": "#0a72e8", "accent_soft": "#d8e8fc", "compare": "#8c8c8c",
    "compare_soft": "#e3e3e3", "pos": "#10935f", "pos_soft": "#e3f6ea", "neg": "#dc383d",
    "neg_soft": "#fde9ea", "warn": "#c27a00", "warn_soft": "#fdf1dc", "track": "#ebebea",
}
ANGLE_NAMES = {"forget": "I forget to drink", "cold": "Still cold at the top", "light": "Light enough to forget", "glow": "The cap that reminds you", "returns": "Try it, return it free"}


def funnel_svg(c1: dict, w: int = 820) -> str:
    total = c1["ads_written"]
    rows = [
        ("Written", total, T["accent"], T["accent_soft"], "30 scripts from the hook bank"),
        ("Passed, first try", c1["passed_first_try"], T["pos"], T["pos_soft"], "no rule broken"),
        ("Passed after regeneration", c1["passed_after_regen"], T["pos"], T["pos_soft"], f"{c1['regenerations']} rewrites, failure codes fed back"),
        ("Held back", c1["held"], T["neg"], T["neg_soft"], "never priced, never generated"),
    ]
    bar_x, row_h = 300, 50
    bar_w = w - bar_x - 60
    out = [f'<svg width="{w}" height="{row_h * len(rows)}" viewBox="0 0 {w} {row_h * len(rows)}" role="img" aria-label="Quality gate funnel">']
    for i, (label, n, col, soft, note) in enumerate(rows):
        y = i * row_h
        bw = max(3, bar_w * n / total)
        out.append(f'<text x="0" y="{y + 20}" font-size="15" fill="{T["ink"]}" font-weight="600">{escape(label)}</text>')
        out.append(f'<text x="0" y="{y + 38}" font-size="12" fill="{T["muted"]}">{escape(note)}</text>')
        out.append(f'<rect x="{bar_x}" y="{y + 8}" width="{bar_w}" height="26" rx="6" fill="{T["track"]}"/>')
        out.append(f'<rect x="{bar_x}" y="{y + 8}" width="{bw:.1f}" height="26" rx="6" fill="{col}"/>')
        out.append(f'<text x="{bar_x + bw + 10:.1f}" y="{y + 27}" font-size="17" font-weight="700" fill="{T["ink"]}">{n}</text>')
    out.append("</svg>")
    return "".join(out)


def weights_svg(fb: dict, c2: dict, w: int = 820) -> str:
    angles = fb["angles"]  # already ranked
    old, new = fb["angle_weights_old"], fb["angle_weights_new"]
    alloc2 = {k: sum(v.values()) for k, v in c2["allocation"].items()}
    vmax = max(max(new.values()), 1.0) * 1.08
    bar_x, row_h = 240, 44
    bar_w = w - bar_x - 220
    h = row_h * len(angles) + 26
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Angle weights before and after the simulated feedback round">']
    # header
    out.append(f'<text x="{w - 130}" y="12" font-size="11" fill="{T["muted"]}" text-anchor="end">conv/1k</text>')
    out.append(f'<text x="{w}" y="12" font-size="11" fill="{T["muted"]}" text-anchor="end">ads in cohort 2</text>')
    for i, g in enumerate(angles):
        y = 22 + i * row_h
        k = g["key"]
        o, n = old[k], new[k]
        up = n >= o
        col = T["accent"] if i == 0 else (T["accent"] if up else T["compare"])
        out.append(f'<text x="0" y="{y + 17}" font-size="14" fill="{T["ink"]}" font-weight="{600 if i == 0 else 500}">{escape(ANGLE_NAMES.get(k, k))}</text>')
        out.append(f'<rect x="{bar_x}" y="{y + 4}" width="{bar_w * o / vmax:.1f}" height="8" rx="3" fill="{T["compare_soft"]}"/>')
        out.append(f'<rect x="{bar_x}" y="{y + 15}" width="{bar_w * n / vmax:.1f}" height="14" rx="4" fill="{col}"/>')
        out.append(f'<text x="{bar_x + bar_w * n / vmax + 8:.1f}" y="{y + 27}" font-size="13" font-weight="700" fill="{T["ink"]}">{n:.2f}</text>')
        out.append(f'<text x="{w - 130}" y="{y + 22}" font-size="13" fill="{T["ink2"]}" text-anchor="end">{g["posterior_per_1k"]:.2f}</text>')
        out.append(f'<text x="{w}" y="{y + 22}" font-size="13" font-weight="600" fill="{T["ink"]}" text-anchor="end">{alloc2.get(k, 0)}</text>')
    # old marker legend
    one_x = bar_x + bar_w * 1.0 / vmax
    out.append(f'<line x1="{one_x:.1f}" x2="{one_x:.1f}" y1="18" y2="{h}" stroke="{T["compare"]}" stroke-dasharray="3 3"/>')
    out.append(f'<text x="{one_x + 4:.1f}" y="12" font-size="11" fill="{T["muted"]}">cohort 1 weight 1.0</text>')
    out.append("</svg>")
    return "".join(out)


def build_html(d: dict) -> str:
    c1, c2, fb = d["cohort_1"], d["cohort_2"], d["feedback"]
    top = fb["angles"][0]
    passed = c1["passed"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0}}
html,body{{width:1600px;height:900px}}
body{{background:{T['page']};color:{T['ink']};font-family:"Geist","Inter",ui-sans-serif,system-ui,sans-serif;display:flex;gap:44px;padding:56px 56px}}
.left{{width:560px;display:flex;flex-direction:column}}
.k{{font:500 17px/1 "Geist Mono",ui-monospace,Menlo,monospace;color:{T['muted']};letter-spacing:.01em}}
h1{{font-size:60px;line-height:1.04;letter-spacing:-.03em;font-weight:700;margin-top:34px}}
p.s{{font-size:22px;line-height:1.42;color:{T['ink2']};margin-top:26px}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:34px}}
.st{{background:{T['surface']};border-radius:16px;padding:16px 18px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -12px rgba(0,0,0,.08)}}
.st b{{display:block;font-size:30px;letter-spacing:-.02em}}
.st span{{font-size:13px;color:{T['muted']}}}
.foot{{margin-top:auto;font-size:14px;line-height:1.5;color:{T['muted']}}}
.right{{flex:1;display:flex;flex-direction:column;gap:20px}}
.card{{background:{T['surface']};border-radius:24px;padding:26px 30px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -12px rgba(0,0,0,.08)}}
.card h2{{font-size:20px;font-weight:600;letter-spacing:-.01em}}
.card .sub{{font-size:15px;color:{T['muted']};margin:6px 0 16px}}
.tag{{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;color:{T['warn']};background:{T['warn_soft']};border-radius:6px;padding:3px 7px;margin-left:8px;vertical-align:3px}}
.src{{font-size:12px;color:{T['muted']};margin-top:10px}}
</style></head><body>
<div class="left">
  <div class="k">github.com/willmather95/ugc-content-machine</div>
  <h1>30 ads a cohort. Only gated ones get a price.</h1>
  <p class="s">Brief, hooks, scripts, a quality gate with reasons, a cost estimate before any paid call, a hashed ledger of every prompt and asset, and a feedback loop that reweights the next cohort.</p>
  <div class="stats">
    <div class="st"><b>{passed} of {c1['ads_written']}</b><span>passed the gate (cohort 1)</span></div>
    <div class="st"><b>${c1['estimate_paid_total_usd']}</b><span>paid-run estimate, placeholder prices</span></div>
    <div class="st"><b>$0.00</b><span>spent: paid backends refuse without an exact yes</span></div>
    <div class="st"><b>{c1['ledger_rows']['prompts']} + {c1['ledger_rows']['assets']}</b><span>prompts + assets hashed in the ledger</span></div>
  </div>
  <div class="foot">All numbers come from <code>make demo</code>: offline templated provider, dry-run backend, Fernway is a made-up brand. The feedback round runs on SIMULATED performance.</div>
</div>
<div class="right">
  <div class="card">
    <h2>Cohort 1 through the quality gate</h2>
    <div class="sub">{passed} of {c1['ads_written']} cleared every hard rule and scored at least 70. Held ads keep their reasons.</div>
    {funnel_svg(c1)}
  </div>
  <div class="card">
    <h2>Angle weights for cohort 2 <span class="tag">SIMULATED DATA</span></h2>
    <div class="sub">"{escape(ANGLE_NAMES.get(top['key'], top['key']))}" ranked first at {top['posterior_per_1k']:.2f} conversions per 1k views (shrunk), so it gets more of the next 30.</div>
    {weights_svg(fb, c2)}
    <div class="src">Source: out/demo-summary.json. Thin bar = cohort 1 weight. Thick bar = new weight (8% floor + P(best) from Thompson draws): blue went up, grey went down.</div>
  </div>
</div>
</body></html>"""


def find_chrome() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", type=Path, default=Path("out/demo-summary.json"))
    ap.add_argument("--out", type=Path, default=Path("docs/hero.png"))
    ap.add_argument("--scale", type=float, default=1.5)
    a = ap.parse_args()
    d = json.loads(a.demo.read_text(encoding="utf-8"))
    html = build_html(d)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    html_path = a.out.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    chrome = find_chrome()
    if not chrome:
        print(f"Chrome not found; wrote {html_path} only", file=sys.stderr)
        return 1
    # Note: a custom --user-data-dir makes headless Chrome hang on some Linux boxes, so none is set.
    subprocess.run(
        [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=5000",
         f"--force-device-scale-factor={a.scale}", "--window-size=1600,900", f"--screenshot={a.out.resolve()}",
         html_path.resolve().as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
    )
    print(f"wrote {a.out} (from {a.demo})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
