# Build receipt: ugc-content-machine

Built Nov 12, 2026 from the specs in the wm95 build folder (COMMON + ugc-content-machine). Local commits only. Nothing was pushed, no GitHub repo was created, `gh` was never run, no paid API was called, and nothing was posted or spent.

## What works

- `python -m ugcm demo` (or `make demo`): cohort 1 (30 ads), a SIMULATED feedback round, then cohort 2 from the reweighted brief. Under 2 seconds, offline, $0.
- Pipeline stages: brief with fit check and claim-list stop, largest-remainder allocation, pre-screened hook bank, scripts through an `LLMProvider` interface, quality gate (18 rule codes + 5-part rubric) with up to 2 rewrites fed by failure codes, cost estimate written before authorization, spend guard, generation through a `GenerationBackend`, SQLite ledger, contact sheet (PNG + SVG), 14-day draft calendar, owner summary with the yes/no approvals.
- Providers: `OfflineProvider` (deterministic, templated, seeded, labeled OFFLINE). `AnthropicProvider` and `OpenAIProvider` are wired and tested against fake transports; they refuse without a key and without `--allow-network`.
- Backends: `DryRunBackend` writes the exact payloads plus placeholder frames. `TregBackend` builds pay-per-call catalog bodies, meters every call against the approved amount before sending, and its default transport refuses to touch the network.
- Spend guard: paid runs refuse unless prices are marked verified, `--approve-spend` equals the estimate to the cent, and the estimate is under the $250 cap. Exit code 3 on refusal.
- Ledger: same inputs give the same fingerprint; `python -m ugcm verify` re-hashes every asset and catches tampering.
- Feedback: CSV validation, Beta shrinkage toward the cohort rate, seeded Thompson P(best), kill rule from the brief, 8% exploration floor, next brief written and validated.
- Hero image `docs/hero.png` (2400x1350) and `docs/contact-sheet.png` rendered from the repo's own demo output by `scripts/render_hero.py` with headless Chrome.

## Tests

- Command: `python -m unittest discover -s tests -t . -v` (or `make test`)
- Result: **128 tests, 128 passed, 0 failed** (also passes with ResourceWarning as an error, and with Pillow blocked).
- CI: `.github/workflows/tests.yml` runs the tests and the demo on Python 3.11, 3.12, 3.13, with and without Pillow. No secrets.

## Demo numbers (from `make demo`, offline provider, dry-run backend)

- Cohort 1: 30 written, 28 passed (15 first try, 13 after a rewrite), 2 held, 19 rewrites, 30 unique hooks, median score 94.
- Ledger cohort 1: 65 prompts, 49 gate results, 140 hashed assets, 140 cost lines.
- SIMULATED feedback: 84 posts, 192,515 views, 183 conversions, 0.951 per 1k (target 0.8). Top angle weight 1.0 to 2.34.
- Cohort 2: 29 of 30 passed, 26 unique hooks (bank ran short on the top angle; reported in the summary and README).

## Scrub results

- `gitleaks dir . --no-banner --redact`: no leaks found.
- `gitleaks git . --no-banner --redact`: no leaks found (all commits).
- `DRY=1 bash publish-private.sh . "x"`: gitleaks clean both ways, scrub grep "clean". DRY=1 stopped before any push.
- No client names, no real leads or contacts, no real tracking IDs, no local machine paths in tracked files. `.env.example` has placeholder names only. The brand (Fernway) and competitor names (RivalFlaskCo, OtherJugCo) are fictional.

## Commit

- HEAD before this receipt: `941156a246c1ad07512145fb0d2b6982901e3dfc`
- This receipt is committed on top of it; run `git rev-parse HEAD` for the final hash.

## What is simulated or placeholder

- Ad copy: offline templated provider, not a language model. It injects realistic mistakes on purpose so the gate is exercised.
- Video, stills, voice: dry run only. Request payloads and placeholder frames, no generated media.
- Unit prices: placeholders in `config/prices.toml`, `verified = false`. Not checked against any provider.
- Performance data in the demo: SIMULATED (`ugcm/simulate.py`), labeled in every row, file name and report.
- `fixtures/prices.verified-test.toml` flips `verified = true` on the same placeholder numbers for tests only.

## Spend ask for Will (not spent, needs your yes)

One real paid 30-ad run on the draft video tier, through the catalog route.

| Item | Assumption | Estimate |
|---|---|---:|
| Video | 30 ads, ~26 s scripts billed as 32 s (4 x 8 s clips), $0.15/s | $144.00 |
| Re-roll allowance | 0.35 extra seconds per billed second | $50.40 |
| Voiceover | ~330 characters per ad, $0.30 per 1k chars | $2.97 |
| First-frame stills | 1 per ad, $0.04 each | $1.20 |
| Script LLM | real model for hooks + scripts + rewrites, ~25k tokens per ad, $6 per 1M blended | $4.50 |
| Buffer | 10% | $20.31 |
| **Total** | | **about $223** |

- **All unit prices above are unverified placeholders.** Before any spend: check each price at its source, update `config/prices.toml`, set `verified = true`, re-run, and approve the exact printed total with `--approve-spend`.
- The quality video tier ($0.40/s placeholder) estimates about $580 for 30 ads (17.57 x 30 + 10%), which is over the $250 hard cap, so the guard would refuse it.
- Cheaper first step I'd suggest: a 5-ad pilot on the draft tier, about $37 at the same placeholder prices.
- Also needed before a live test: a real `TregBackend` transport (or a first-party Gemini/Veo transport), your approved claim list for a real brand, and your yes on the draft calendar. Nothing posts on its own.
