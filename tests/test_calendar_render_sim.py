import json
import unittest
from datetime import date

from ugcm.calendar import build_calendar
from ugcm.render import contact_sheet_svg, placeholder_frame_svg
from ugcm.simulate import FIELDS, simulate_performance

from .helpers import FIXTURES

ADS = json.loads((FIXTURES / "ads-sample.json").read_text())


class CalendarTest(unittest.TestCase):
    def test_caps_and_drafts(self):
        ids = [f"AD-{i:03d}" for i in range(1, 31)]
        rows = build_calendar(ids, ("tiktok", "reels"), date(2026, 11, 13))
        self.assertEqual(len(rows), 2 * 28)  # 14 days x 2 windows per account
        self.assertEqual({r["status"] for r in rows}, {"draft"})
        self.assertEqual(rows[0]["date"], "2026-11-13")
        self.assertEqual(max(r["date"] for r in rows), "2026-11-26")

    def test_networks_get_different_order(self):
        ids = [f"AD-{i:03d}" for i in range(1, 11)]
        rows = build_calendar(ids, ("tiktok", "reels", "shorts"), date(2026, 11, 13))
        first = {r["platform"]: r["ad_id"] for r in rows if r["date"] == "2026-11-13" and r["window"] == "11:30"}
        self.assertEqual(len(set(first.values())), 3)

    def test_rejects_more_than_two_a_day(self):
        with self.assertRaises(ValueError):
            build_calendar(["AD-001"], ("tiktok",), date(2026, 11, 13), per_day=3)

    def test_empty(self):
        self.assertEqual(build_calendar([], ("tiktok",), date(2026, 11, 13)), [])


class RenderTest(unittest.TestCase):
    ctx = {"ad_id": "AD-007", "hook": "Why is summit water <always> warm?", "first_frame": "bottle & pack", "format_name": "Myth vs. fact", "angle_name": "Still cold", "angle_index": 1, "score": 88}

    def test_frame_is_labeled_and_escaped(self):
        svg = placeholder_frame_svg(self.ctx)
        self.assertIn("PLACEHOLDER FRAME", svg)
        self.assertIn("AI-generated", svg)
        self.assertIn("&lt;always&gt;", svg)
        self.assertIn("bottle &amp; pack", svg)

    def test_contact_sheet_marks_held(self):
        items = [dict(self.ctx, status="passed"), dict(self.ctx, ad_id="AD-008", status="held", codes=["CTA_MISSING"])]
        svg = contact_sheet_svg(items, title="t", subtitle="s")
        self.assertEqual(svg.count("HELD"), 1)
        self.assertIn("AD-008", svg)


class SimulateTest(unittest.TestCase):
    def test_labeled_deterministic_and_sane(self):
        cal = [{"ad_id": a["ad_id"], "platform": p, "date": "2026-11-13"} for a in ADS for p in ("tiktok", "reels")]
        a = simulate_performance(ADS, cal, seed=1)
        b = simulate_performance(ADS, cal, seed=1)
        self.assertEqual(a, b)
        self.assertEqual(set(a[0]), set(FIELDS))
        self.assertEqual({r["source"] for r in a}, {"SIMULATED"})
        for r in a:
            self.assertLessEqual(r["conversions"], r["link_clicks"])
            self.assertLessEqual(r["conversions"], r["views"])
        self.assertNotEqual(a, simulate_performance(ADS, cal, seed=2))


if __name__ == "__main__":
    unittest.main()
