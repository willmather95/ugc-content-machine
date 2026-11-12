import contextlib
import csv
import io
import json
import unittest

from ugcm.cli import main

from .helpers import tmpdir


def quiet(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tmpdir()
        cls.rc, cls.stdout, _ = quiet(["demo", "--out", str(cls.out)])

    def test_demo_succeeds_end_to_end(self):
        self.assertEqual(self.rc, 0, self.stdout)
        self.assertIn("ok:", self.stdout)

    def test_two_cohorts_and_feedback(self):
        for p in ["cohort-001/contact-sheet.svg", "cohort-001/performance.SIMULATED.csv", "cohort-001/feedback-report.md", "cohort-002.brief.json", "cohort-002/summary.json", "demo-summary.json"]:
            self.assertTrue((self.out / p).exists(), p)

    def test_simulated_rows_are_labeled(self):
        with open(self.out / "cohort-001" / "performance.SIMULATED.csv") as fh:
            self.assertEqual({r["source"] for r in csv.DictReader(fh)}, {"SIMULATED"})
        self.assertIn("SIMULATED", (self.out / "cohort-002.brief.json").read_text())

    def test_weights_moved_between_cohorts(self):
        d = json.loads((self.out / "demo-summary.json").read_text())
        self.assertEqual(set(d["cohort_1"]["angle_weights"].values()), {1.0})
        self.assertNotEqual(d["cohort_1"]["allocation"], d["cohort_2"]["allocation"])

    def test_feedback_written_to_ledger(self):
        import sqlite3

        db = sqlite3.connect(self.out / "cohort-001" / "ledger.sqlite")
        try:
            self.assertEqual(db.execute("SELECT DISTINCT source FROM feedback").fetchall(), [("SIMULATED",)])
        finally:
            db.close()

    def test_verify_fails_after_tamper(self):
        f = sorted((self.out / "cohort-002" / "ads").glob("*/requests/voice.json"))[0]
        f.write_text(f.read_text() + " ")
        rc, out, _ = quiet(["verify", "--cohort", str(self.out / "cohort-002")])
        self.assertEqual(rc, 1)
        self.assertIn("hash changed", out)


class RefusalExitCodeTest(unittest.TestCase):
    def test_paid_backend_exits_3_and_spends_nothing(self):
        out = tmpdir()
        rc, stdout, stderr = quiet(["run", "--out", str(out), "--backend", "treg", "--approve-spend", "1"])
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED", stderr)
        self.assertIn("cost estimate (before any paid step)", stdout)

    def test_real_provider_without_key_exits_3(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=True):
            rc, _, stderr = quiet(["run", "--out", str(tmpdir()), "--provider", "anthropic", "--allow-network"])
        self.assertEqual(rc, 3)
        self.assertIn("ANTHROPIC_API_KEY", stderr)

    def test_bad_brief_exits_2(self):
        p = tmpdir() / "b.json"
        p.write_text(json.dumps({"cohort_id": "x"}))
        rc, _, stderr = quiet(["run", "--brief", str(p), "--out", str(tmpdir())])
        self.assertEqual(rc, 2)
        self.assertIn("missing", stderr)


if __name__ == "__main__":
    unittest.main()
