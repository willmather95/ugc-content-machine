import json
import unittest

from ugcm.feedback import (
    FeedbackError,
    apply_kill_rules,
    feedback_round,
    load_performance,
    new_weights,
    next_cohort_id,
    rank,
    render_report_md,
)
from ugcm.brief import parse_brief

from .helpers import FIXTURES, brief, tmpdir

ADS = json.loads((FIXTURES / "ads-sample.json").read_text())
PERF_PATH = FIXTURES / "performance-sample.csv"


class LoadTest(unittest.TestCase):
    def test_loads_and_types(self):
        rows = load_performance(PERF_PATH)
        self.assertEqual(len(rows), 34)
        self.assertIsInstance(rows[0]["views"], int)
        self.assertIsInstance(rows[0]["hold_3s_pct"], float)

    def _bad(self, text, msg):
        p = tmpdir() / "p.csv"
        p.write_text(text)
        with self.assertRaisesRegex(FeedbackError, msg):
            load_performance(p)

    def test_rejects_bad_files(self):
        self._bad("ad_id,views\nAD-001,10\n", "missing columns")
        self._bad("ad_id,platform,views,conversions\nAD-001,tiktok,ten,0\n", "not a whole number")
        self._bad("ad_id,platform,views,conversions\nAD-001,tiktok,10,-1\n", "negative")
        self._bad("ad_id,platform,views,conversions\nAD-001,tiktok,10,11\n", "more conversions than views")
        self._bad("ad_id,platform,views,conversions\nAD-001,tiktok,10,0\nAD-001,tiktok,10,0\n", "duplicate")


class RankTest(unittest.TestCase):
    def setUp(self):
        self.perf = load_performance(PERF_PATH)

    def test_angle_order_follows_conversion_rate(self):
        stats = rank(ADS, self.perf, "angle", seed=1)
        self.assertEqual(stats[0].key, "glow")
        self.assertIn(stats[-1].key, {"light", "returns"})  # light also carries a zero-conversion format
        self.assertAlmostEqual(sum(s.p_best for s in stats), 1.0, places=3)
        self.assertGreater(stats[0].p_best, 0.5)

    def test_shrinkage_pulls_toward_the_pooled_rate(self):
        stats = rank(ADS, self.perf, "angle", seed=1)
        pooled = 1000 * sum(p["conversions"] for p in self.perf) / sum(p["views"] for p in self.perf)
        for s in stats:
            # posterior sits between the raw rate and the pooled rate
            lo, hi = sorted([s.raw_per_1k, pooled])
            self.assertTrue(lo - 1e-9 <= s.posterior_per_1k <= hi + 1e-9, (s.key, s.raw_per_1k, s.posterior_per_1k, pooled))
            self.assertLessEqual(s.lo90_per_1k, s.posterior_per_1k)
            self.assertGreaterEqual(s.hi90_per_1k, s.posterior_per_1k)

    def test_seeded_and_reproducible(self):
        a = [s.p_best for s in rank(ADS, self.perf, "angle", seed=3)]
        b = [s.p_best for s in rank(ADS, self.perf, "angle", seed=3)]
        self.assertEqual(a, b)

    def test_rows_without_ledger_ads_are_ignored_and_none_raise(self):
        with self.assertRaises(FeedbackError):
            rank(ADS, [{"ad_id": "AD-999", "platform": "x", "views": 10, "conversions": 0}], "angle")


class RulesAndWeightsTest(unittest.TestCase):
    def setUp(self):
        self.perf = load_performance(PERF_PATH)

    def test_kill_rule_needs_posts_low_views_and_zero_conversions(self):
        stats = rank(ADS, self.perf, "format", seed=1)
        apply_kill_rules(stats, brief())
        verdicts = {s.key: s.verdict for s in stats}
        self.assertEqual(verdicts["three-things"], "kill")  # 10 posts, median < 900, 0 conversions
        self.assertEqual(verdicts["pov-fix"], "keep")

    def test_too_early_before_min_posts(self):
        perf = [p for p in self.perf if p["platform"] == "tiktok"]  # 5 three-things posts only
        stats = rank(ADS, perf, "format", seed=1)
        apply_kill_rules(stats, brief())
        self.assertEqual({s.key: s.verdict for s in stats}["three-things"], "too early")

    def test_new_weights_floor_and_mean_one(self):
        stats = rank(ADS, self.perf, "angle", seed=1)
        w = new_weights(stats)
        self.assertAlmostEqual(sum(w.values()) / len(w), 1.0, places=2)
        self.assertTrue(all(v >= 0.39 for v in w.values()))  # exploration floor
        self.assertEqual(max(w, key=w.get), "glow")

    def test_killed_group_gets_zero(self):
        stats = rank(ADS, self.perf, "format", seed=1)
        apply_kill_rules(stats, brief())
        w = new_weights(stats)
        self.assertEqual(w["three-things"], 0.0)

    def test_all_killed_raises(self):
        stats = rank(ADS, self.perf, "format", seed=1)
        for s in stats:
            s.verdict = "kill"
        with self.assertRaises(FeedbackError):
            new_weights(stats)


class RoundTest(unittest.TestCase):
    def test_round_writes_a_valid_next_brief(self):
        b = brief()
        fb = feedback_round(b, ADS, load_performance(PERF_PATH), seed=1)
        nb = parse_brief(fb["next_brief"])  # it must validate
        self.assertEqual(nb.cohort_id, "cohort-002")
        self.assertEqual(nb.parent_cohort, "cohort-001")
        self.assertEqual(nb.format_by_id("three-things").weight, 0.0)
        self.assertGreater(nb.angle_by_id("glow").weight, nb.angle_by_id("returns").weight)
        self.assertEqual(fb["source"], "FIXTURE")
        md = render_report_md(fb, b)
        self.assertIn("kill", md)
        self.assertNotIn("SIMULATED DATA", md)

    def test_simulated_source_is_labeled(self):
        perf = load_performance(PERF_PATH)
        for p in perf:
            p["source"] = "SIMULATED"
        fb = feedback_round(brief(), ADS, perf, seed=1)
        self.assertIn("SIMULATED", fb["next_brief"]["feedback_note"])
        self.assertIn("SIMULATED DATA", render_report_md(fb, brief()))

    def test_next_cohort_id(self):
        self.assertEqual(next_cohort_id("cohort-001"), "cohort-002")
        self.assertEqual(next_cohort_id("cohort-099"), "cohort-100")
        self.assertEqual(next_cohort_id("spring"), "spring-002")


if __name__ == "__main__":
    unittest.main()
