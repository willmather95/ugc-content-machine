import unittest
from decimal import Decimal

from ugcm.cost import (
    AssetPlan,
    Authorization,
    BudgetExceeded,
    PricesUnverified,
    SpendMismatch,
    SpendNotApproved,
    SpendOverCap,
    authorize,
    billed_video_seconds,
    estimate,
    parse_amount,
    render_estimate_md,
)

from .helpers import prices, verified_prices


class BilledSecondsTest(unittest.TestCase):
    def test_rounds_up_to_whole_clips(self):
        self.assertEqual(billed_video_seconds(25.4, 8), 32)
        self.assertEqual(billed_video_seconds(24.0, 8), 24)
        self.assertEqual(billed_video_seconds(0.1, 8), 8)
        self.assertEqual(billed_video_seconds(0, 8), 0)


class EstimateTest(unittest.TestCase):
    def test_hand_checked_single_ad(self):
        # 25.4s -> 32 billed s * 0.15 = 4.80; re-rolls 32*0.35 = 11.20 s * 0.15 = 1.68
        # still 0.04; voice 1000 chars = 1.000k * 0.30 = 0.30; llm 10,000 tok = 0.01M * 6 = 0.06
        est = estimate([AssetPlan("AD-001", 25.4, 1000, 10_000)], prices(), llm_paid=True)
        items = {l.item: l.usd for l in est.lines}
        self.assertEqual(items["video"], Decimal("4.80"))
        self.assertEqual(items["video re-rolls"], Decimal("1.68"))
        self.assertEqual(items["first-frame still"], Decimal("0.04"))
        self.assertEqual(items["voiceover"], Decimal("0.30"))
        self.assertEqual(items["script LLM"], Decimal("0.06"))
        self.assertEqual(est.subtotal, Decimal("6.88"))
        self.assertEqual(est.buffer, Decimal("0.69"))  # 10%, half-up
        self.assertEqual(est.total, Decimal("7.57"))
        self.assertEqual(est.per_ad()["AD-001"], Decimal("6.88"))

    def test_offline_llm_costs_nothing(self):
        est = estimate([AssetPlan("AD-001", 20, 500, 50_000)], prices(), llm_paid=False)
        self.assertEqual({l.item: l.usd for l in est.lines}["script LLM"], Decimal("0.00"))

    def test_quality_tier_costs_more(self):
        plans = [AssetPlan(f"AD-{i:03d}", 26, 400, 3000) for i in range(30)]
        self.assertGreater(estimate(plans, prices(), tier="video-quality").total, estimate(plans, prices()).total)

    def test_unknown_tier(self):
        with self.assertRaises(KeyError):
            estimate([], prices(), tier="video-ultra")

    def test_markdown_warns_on_placeholder_prices(self):
        md = render_estimate_md(estimate([AssetPlan("AD-001", 20, 500, 0)], prices()), title="x")
        self.assertIn("PLACEHOLDER PRICES", md)
        md2 = render_estimate_md(estimate([AssetPlan("AD-001", 20, 500, 0)], verified_prices()), title="x")
        self.assertNotIn("PLACEHOLDER PRICES", md2)


class SpendGuardTest(unittest.TestCase):
    def setUp(self):
        self.plans = [AssetPlan(f"AD-{i:03d}", 26, 400, 3000) for i in range(30)]
        self.est = estimate(self.plans, verified_prices())

    def test_free_backend_needs_no_approval(self):
        self.assertIsNone(authorize(self.est, None, paid=False))

    def test_unverified_prices_refused_even_with_matching_amount(self):
        est = estimate(self.plans, prices())
        with self.assertRaises(PricesUnverified):
            authorize(est, str(est.total), paid=True)

    def test_no_approval_refused(self):
        with self.assertRaises(SpendNotApproved) as cm:
            authorize(self.est, None, paid=True)
        self.assertIn(str(self.est.total), str(cm.exception))

    def test_mismatch_refused(self):
        wrong = str(self.est.total + Decimal("0.01"))
        with self.assertRaises(SpendMismatch):
            authorize(self.est, wrong, paid=True)
        with self.assertRaises(SpendMismatch):
            authorize(self.est, "lots", paid=True)

    def test_over_cap_refused(self):
        plans = [AssetPlan(f"AD-{i:03d}", 34, 600, 3000) for i in range(30)]
        est = estimate(plans, verified_prices(), tier="video-quality")
        self.assertGreater(est.total, est.cap)
        with self.assertRaises(SpendOverCap):
            authorize(est, str(est.total), paid=True)

    def test_exact_match_authorizes(self):
        auth = authorize(self.est, f"${self.est.total}", paid=True)
        self.assertEqual(auth.approved, self.est.total)

    def test_parse_amount(self):
        self.assertEqual(parse_amount("$1,234.5"), Decimal("1234.50"))

    def test_budget_meter_stops_at_the_line(self):
        a = Authorization(approved=Decimal("1.00"))
        a.charge(Decimal("0.60"), "one")
        with self.assertRaises(BudgetExceeded):
            a.charge(Decimal("0.41"), "two")
        self.assertEqual(a.spent, Decimal("0.60"))
        a.charge(Decimal("0.40"), "three")
        self.assertEqual(a.remaining, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
