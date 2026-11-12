import csv
import json
import sqlite3
import unittest
from decimal import Decimal

from ugcm.cost import PricesUnverified, SpendMismatch, SpendNotApproved
from ugcm.generation import DryRunBackend, TregBackend
from ugcm.ledger import Ledger
from ugcm.pipeline import run_cohort
from ugcm.providers import OfflineProvider

from .helpers import CONFIG, brief, brief_data, policy, prices, tmpdir, verified_prices
from ugcm.brief import parse_brief


def run(out=None, b=None, backend=None, prices_=None, approve=None):
    out = out or tmpdir()
    return run_cohort(
        brief=b or brief(), policy=policy(), prices=prices_ or prices(), provider=OfflineProvider(),
        backend=backend or DryRunBackend(), out_dir=out, policy_path=CONFIG / "policy.toml",
        prices_path=CONFIG / "prices.toml", approve_spend=approve, log=lambda m: None,
    )


class CohortTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run()
        cls.s = cls.r.summary

    def test_thirty_ads_accounted_for(self):
        s = self.s
        self.assertEqual(s["ads_written"], 30)
        self.assertEqual(s["passed_first_try"] + s["passed_after_regen"] + s["held"], 30)
        self.assertGreater(s["passed"], 20)  # gate is strict but the loop recovers most ads

    def test_gate_did_real_work(self):
        self.assertGreater(sum(self.s["first_attempt_failure_codes"].values()), 0)
        self.assertGreater(self.s["regenerations"], 0)

    def test_regenerations_capped(self):
        for a in self.r.ads:
            self.assertLessEqual(a.regenerations, policy()["rubric"]["max_regenerations"])

    def test_held_ads_carry_reasons(self):
        for h in self.s["held_ads"]:
            self.assertTrue(h["codes"])
            self.assertTrue(h["reasons"])

    def test_every_passed_ad_really_passes(self):
        for a in self.r.ads:
            if a.status == "passed":
                self.assertEqual(a.gate.codes, [])
                self.assertGreaterEqual(a.gate.score, policy()["rubric"]["pass_score"])

    def test_no_duplicate_hook_lines(self):
        self.assertEqual(self.s["unique_hooks"], 30)

    def test_output_files(self):
        out = self.r.out_dir
        for name in ["ledger.sqlite", "summary.json", "cost-estimate.json", "cost-estimate.md", "contact-sheet.svg", "calendar.DRAFT.csv", "hook-bank.json", "owner-summary.md", "brief.source.json"]:
            self.assertTrue((out / name).exists(), name)
        self.assertEqual(len(list((out / "ads").iterdir())), 30)
        passed = next(a for a in self.r.ads if a.status == "passed")
        d = out / "ads" / passed.slot.ad_id
        for name in ["script.md", "script.json", "gate.json", "frame.svg", "requests/video.json", "requests/image.json", "requests/voice.json"]:
            self.assertTrue((d / name).exists(), name)

    def test_held_ads_get_no_generation_requests(self):
        for a in self.r.ads:
            if a.status != "passed":
                self.assertFalse((self.r.out_dir / "ads" / a.slot.ad_id / "requests").exists())

    def test_calendar_is_all_drafts_and_capped(self):
        with open(self.r.out_dir / "calendar.DRAFT.csv") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue(rows)
        self.assertEqual({r["status"] for r in rows}, {"draft"})
        per_day = {}
        for r in rows:
            per_day[(r["date"], r["platform"])] = per_day.get((r["date"], r["platform"]), 0) + 1
        self.assertLessEqual(max(per_day.values()), 2)
        self.assertLessEqual(len({r["date"] for r in rows}), 14)

    def test_dry_run_spends_nothing_and_estimate_is_labeled(self):
        self.assertEqual(self.s["spent_usd"], "0.00")
        est = json.loads((self.r.out_dir / "cost-estimate.json").read_text())
        self.assertEqual(est["if_paid"]["warning"], "PLACEHOLDER PRICES. Verify before use.")
        self.assertEqual(Decimal(est["if_paid"]["total_usd"]), self.r.estimate_paid.total)

    def test_ledger_rows(self):
        n = self.s["ledger_rows"]
        self.assertEqual(n["runs"], 1)
        self.assertEqual(n["ads"], 30)
        self.assertEqual(n["gate_results"], 30 + self.s["regenerations"])
        self.assertEqual(n["cost_estimates"], 5 * self.s["passed"])
        db = sqlite3.connect(self.r.out_dir / "ledger.sqlite")
        stages = dict(db.execute("SELECT stage, COUNT(*) FROM prompts GROUP BY stage").fetchall())
        self.assertEqual(stages["script"], n["gate_results"])
        self.assertGreaterEqual(stages["hooks"], 15)
        row = db.execute("SELECT template_id, template_sha, seed, prompt_text, response_sha FROM prompts LIMIT 1").fetchone()
        db.close()
        self.assertTrue(row[0].startswith("hooks@"))
        self.assertEqual(len(row[1]), 16)
        self.assertTrue(row[3])


class ReproducibilityTest(unittest.TestCase):
    def test_same_inputs_same_fingerprint_and_bytes(self):
        a, b = run(), run()
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual((a.out_dir / "ads" / "AD-001" / "script.json").read_bytes(), (b.out_dir / "ads" / "AD-001" / "script.json").read_bytes())

    def test_different_seed_different_fingerprint(self):
        d = brief_data()
        d["seed"] = 96
        self.assertNotEqual(run().fingerprint, run(b=parse_brief(d)).fingerprint)

    def test_verify_detects_tampering(self):
        r = run()
        with Ledger(r.out_dir / "ledger.sqlite") as led:
            self.assertEqual(led.verify_assets(r.out_dir), [])
        target = next((r.out_dir / "ads").glob("*/requests/video.json"))
        target.write_text(target.read_text().replace("9:16", "16:9"))
        with Ledger(r.out_dir / "ledger.sqlite") as led:
            problems = led.verify_assets(r.out_dir)
        self.assertEqual(len(problems), 1)
        self.assertIn("hash changed", problems[0])

    def test_rerun_into_same_folder_replaces_ledger(self):
        out = tmpdir()
        a = run(out)
        b = run(out)
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(b.summary["ledger_rows"]["runs"], 1)


class SpendRefusalTest(unittest.TestCase):
    def test_paid_backend_with_placeholder_prices_refuses(self):
        out = tmpdir()
        with self.assertRaises(PricesUnverified):
            run(out, backend=TregBackend(prices()), approve="1.00")
        # The estimate was written BEFORE the refusal; no request went anywhere.
        self.assertTrue((out / "cost-estimate.json").exists())
        self.assertEqual(list(out.glob("ads/*/requests")), [])

    def test_paid_backend_without_approval_refuses(self):
        out = tmpdir()
        with self.assertRaises(SpendNotApproved):
            run(out, backend=TregBackend(verified_prices()), prices_=verified_prices())
        self.assertEqual(list(out.glob("ads/*/requests")), [])

    def test_paid_backend_with_wrong_amount_refuses(self):
        with self.assertRaises(SpendMismatch):
            run(backend=TregBackend(verified_prices()), prices_=verified_prices(), approve="10.00")

    def test_paid_backend_with_exact_amount_runs_against_fake_transport(self):
        dry = run(prices_=verified_prices())
        total = str(dry.estimate_run.total)
        calls = []
        be = TregBackend(verified_prices(), transport=lambda m, body: calls.append(body) or {"job_id": f"job-{len(calls)}"})
        r = run(backend=be, prices_=verified_prices(), approve=total)
        self.assertEqual(len(calls), 3 * r.summary["passed"])
        self.assertLessEqual(Decimal(r.summary["spent_usd"]), Decimal(total))
        self.assertGreater(Decimal(r.summary["spent_usd"]), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
