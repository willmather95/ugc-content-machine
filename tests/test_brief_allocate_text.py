import unittest

from ugcm.allocate import allocate, largest_remainder
from ugcm.brief import BriefError, parse_brief
from ugcm.textstats import fk_grade, sentences, speak_seconds, syllables, word_count

from .helpers import brief, brief_data


class BriefTest(unittest.TestCase):
    def test_loads_default_brief(self):
        b = brief()
        self.assertEqual(b.cohort_size, 30)
        self.assertEqual(len(b.formats), 3)
        self.assertEqual(len(b.angles), 5)
        self.assertGreaterEqual(b.fit_score(), 3)

    def test_fit_check_stops_the_pipeline(self):
        d = brief_data()
        for k in list(d["fit"])[:3]:
            d["fit"][k] = False
        with self.assertRaisesRegex(BriefError, "not a fit"):
            parse_brief(d)

    def test_empty_claim_list_stops(self):
        d = brief_data()
        d["approved_claims"] = []
        with self.assertRaisesRegex(BriefError, "approved claim list"):
            parse_brief(d)

    def test_missing_field(self):
        d = brief_data()
        del d["cta"]
        with self.assertRaisesRegex(BriefError, "cta"):
            parse_brief(d)

    def test_negative_or_all_zero_weights(self):
        d = brief_data()
        d["angles"][0]["weight"] = -1
        with self.assertRaises(BriefError):
            parse_brief(d)
        d = brief_data()
        for f in d["formats"]:
            f["weight"] = 0
        with self.assertRaises(BriefError):
            parse_brief(d)


class AllocateTest(unittest.TestCase):
    def test_largest_remainder_sums_exactly(self):
        for total in (0, 1, 7, 30, 31, 100):
            c = largest_remainder({"a": 1, "b": 1, "c": 1}, total)
            self.assertEqual(sum(c.values()), total)

    def test_largest_remainder_known_case(self):
        # quotas 30*[.5,.3,.2] = 15, 9, 6
        self.assertEqual(largest_remainder({"a": 5, "b": 3, "c": 2}, 30), {"a": 15, "b": 9, "c": 6})
        # quotas 10*[1/3]*3 = 3.33 each -> one extra to the first by order
        self.assertEqual(largest_remainder({"a": 1, "b": 1, "c": 1}, 10), {"a": 4, "b": 3, "c": 3})

    def test_equal_weights_give_two_per_cell(self):
        slots = allocate(brief())
        self.assertEqual(len(slots), 30)
        cells = {}
        for s in slots:
            cells[(s.format_id, s.angle_id)] = cells.get((s.format_id, s.angle_id), 0) + 1
        self.assertEqual(set(cells.values()), {2})
        self.assertEqual([s.ad_id for s in slots][:3], ["AD-001", "AD-002", "AD-003"])

    def test_weights_shift_allocation_and_zero_removes(self):
        d = brief_data()
        d["angles"][0]["weight"] = 3.0
        d["angles"][4]["weight"] = 0.0
        d["formats"][2]["weight"] = 0.0
        slots = allocate(parse_brief(d))
        self.assertEqual(len(slots), 30)
        angles = [s.angle_id for s in slots]
        self.assertNotIn(d["angles"][4]["id"], angles)
        self.assertNotIn(d["formats"][2]["id"], [s.format_id for s in slots])
        self.assertEqual(angles.count(d["angles"][0]["id"]), 15)  # 3 of 6 weight units

    def test_interleaved_not_blocked(self):
        slots = allocate(brief())
        first15 = {(s.format_id, s.angle_id) for s in slots[:15]}
        self.assertEqual(len(first15), 15)


class TextStatsTest(unittest.TestCase):
    def test_words_and_sentences(self):
        self.assertEqual(word_count("POV: mile six and you've not had one sip."), 9)
        self.assertEqual(len(sentences("One. Two! Three?")), 3)

    def test_syllables(self):
        for w, n in [("cat", 1), ("water", 2), ("bottle", 2), ("summit", 2), ("insulation", 4), ("the", 1)]:
            with self.subTest(w=w):
                self.assertEqual(syllables(w), n)

    def test_grade_orders_simple_below_complex(self):
        simple = "I drink. It is cold. I like it."
        hard = "Its vacuum-insulated stainless construction maintains exceptional temperature consistency throughout prolonged recreational excursions."
        self.assertLess(fk_grade(simple), 3)
        self.assertGreater(fk_grade(hard), 12)
        self.assertEqual(fk_grade(""), 0.0)

    def test_speak_seconds(self):
        self.assertEqual(speak_seconds("one two three four five", 2.5), 2.0)


if __name__ == "__main__":
    unittest.main()
