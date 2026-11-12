import copy
import unittest

from ugcm.gate import QualityGate

from .helpers import brief, brief_data, clean_hook, clean_script, policy
from ugcm.brief import parse_brief


def check(hook=None, script=None, b=None, platforms=None):
    g = QualityGate(b or brief(), policy())
    return g.check(hook or clean_hook(), script or clean_script(), platforms)


def with_say(i, text, append=True):
    s = clean_script()
    s["beats"][i]["say"] = (s["beats"][i]["say"] + " " + text) if append else text
    return s


class CleanAdTest(unittest.TestCase):
    def test_clean_ad_passes_with_full_score(self):
        r = check()
        self.assertTrue(r.passed, r.failures)
        self.assertEqual(r.score, 100)
        self.assertEqual(r.codes, [])


class HookRulesTest(unittest.TestCase):
    def test_long_hook_fails(self):
        h = clean_hook()
        h["line"] = "Why is summit water always warm, and why does nobody ever fix it?"
        s = clean_script()
        s["beats"][0]["say"] = h["line"]
        r = check(h, s)
        self.assertIn("HOOK_TOO_LONG", r.codes)
        self.assertEqual([f.stage for f in r.failures if f.code == "HOOK_TOO_LONG"], ["hook"])

    def test_short_hook_fails(self):
        h = clean_hook()
        h["line"] = "Look."
        s = clean_script()
        s["beats"][0]["say"] = "Look."
        self.assertIn("HOOK_TOO_SHORT", check(h, s).codes)

    def test_blank_first_frame_fails(self):
        h = clean_hook()
        h["first_frame"] = "black screen, then fade in on the logo"
        self.assertIn("BLANK_FIRST_FRAME", check(h).codes)

    def test_first_frame_without_product_or_face_fails(self):
        h = clean_hook()
        h["first_frame"] = "a sunset over a lake"
        self.assertIn("BLANK_FIRST_FRAME", check(h).codes)

    def test_hook_must_be_said_first(self):
        s = with_say(0, "Something else entirely.", append=False)
        self.assertIn("HOOK_NOT_FIRST", check(script=s).codes)

    def test_check_hook_prescreen_only_returns_hook_failures(self):
        g = QualityGate(brief(), policy())
        self.assertEqual(g.check_hook(clean_hook()), [])
        h = clean_hook()
        h["first_frame"] = "blank"
        self.assertEqual([f.code for f in g.check_hook(h)], ["BLANK_FIRST_FRAME"])


class ClaimRulesTest(unittest.TestCase):
    def test_banned_claim(self):
        r = check(script=with_say(2, "Honestly, it is the best bottle ever."))
        self.assertIn("BANNED_CLAIM", r.codes)
        self.assertFalse(r.passed)

    def test_banned_claim_in_caption(self):
        s = clean_script()
        s["caption"] += " Guaranteed."
        self.assertIn("BANNED_CLAIM", check(script=s).codes)

    def test_health_claims_banned(self):
        for phrase in ["It helps you detox.", "Burns fat on the trail.", "Doctors recommend it.", "Lose 10 lbs this summer."]:
            with self.subTest(phrase=phrase):
                self.assertIn("BANNED_CLAIM", check(script=with_say(2, phrase)).codes)

    def test_unapproved_number_claim(self):
        r = check(script=with_say(1, "Keeps water cold for 48 hours."))
        self.assertIn("UNAPPROVED_CLAIM", r.codes)
        self.assertIn("48 hours", " ".join(f.reason for f in r.failures))

    def test_approved_claim_word_for_word_passes(self):
        self.assertTrue(check(script=with_say(2, "Free returns for 30 days.")).passed)

    def test_reworded_approved_claim_fails(self):
        self.assertIn("UNAPPROVED_CLAIM", check(script=with_say(2, "Keeps your water cold for 24 hours.")).codes)

    def test_testimonial_implied(self):
        self.assertIn("TESTIMONIAL_IMPLIED", check(script=with_say(2, "Real results from a real customer.")).codes)


class StructureRulesTest(unittest.TestCase):
    def test_cta_missing(self):
        r = check(script=with_say(3, "Check it out.", append=False))
        self.assertIn("CTA_MISSING", r.codes)
        self.assertEqual(r.subscores["cta"], 0.0)

    def test_too_short(self):
        s = clean_script()
        s["beats"] = [s["beats"][0], {"t": "2-10s", "say": "Look.", "show": "x"}, s["beats"][3]]
        self.assertIn("TOO_SHORT", check(script=s).codes)

    def test_too_long(self):
        s = with_say(2, " ".join(["We walked and walked up the hill."] * 8))
        self.assertIn("TOO_LONG", check(script=s).codes)


class BrandRulesTest(unittest.TestCase):
    def test_misspelled_brand(self):
        s = clean_script()
        s["beats"][2]["say"] = s["beats"][2]["say"].replace("Fernway", "FernWay")
        r = check(script=s)
        self.assertIn("BRAND_SPELLING", r.codes)
        self.assertIn("BRAND_MISSING", r.codes)

    def test_forbidden_word(self):
        self.assertIn("FORBIDDEN_WORD", check(script=with_say(2, "Not some cheap knockoff.")).codes)

    def test_competitor_name(self):
        self.assertIn("COMPETITOR_NAME", check(script=with_say(2, "Way better than my old RivalFlaskCo.")).codes)


class ReadabilityTest(unittest.TestCase):
    def test_one_stiff_beat_fails_even_if_average_is_low(self):
        stiff = (
            "Its vacuum-insulated stainless construction maintains exceptional temperature "
            "consistency throughout prolonged recreational excursions, regardless of environmental variability."
        )
        s = clean_script()
        s["beats"][2]["say"] = stiff + " It is the Fernway Trail Bottle. Holds 750 ml. " + " ".join(["I like it."] * 6)
        r = check(script=s)
        self.assertIn("READABILITY", r.codes)


class PlatformPolicyTest(unittest.TestCase):
    def test_ai_label_required(self):
        s = clean_script()
        s["caption"] = s["caption"].replace("#AIgenerated", "")
        self.assertIn("DISCLOSURE_MISSING", check(script=s).codes)

    def test_paid_partnership_needs_ad_tag(self):
        d = brief_data()
        d["disclosure"]["paid_partnership"] = True
        r = check(b=parse_brief(d))
        self.assertIn("DISCLOSURE_MISSING", r.codes)
        s = clean_script()
        s["caption"] += " #ad"
        self.assertTrue(check(script=s, b=parse_brief(d)).passed)

    def test_affiliate_line_required(self):
        d = brief_data()
        d["disclosure"]["affiliate"] = True
        self.assertIn("DISCLOSURE_MISSING", check(b=parse_brief(d)).codes)

    def test_too_many_hashtags(self):
        s = clean_script()
        s["caption"] += " #a #b #c"
        self.assertIn("TOO_MANY_HASHTAGS", check(script=s).codes)

    def test_shorts_caption_cap_is_per_platform(self):
        s = clean_script()
        s["caption"] = "Cold at the top, every time, on every trail I have tried this year so far, honestly. #hiking #AIgenerated"
        self.assertGreater(len(s["caption"]), 100)
        self.assertIn("CAPTION_TOO_LONG", check(script=s, platforms=("shorts",)).codes)
        self.assertNotIn("CAPTION_TOO_LONG", check(script=s, platforms=("tiktok",)).codes)


class RubricTest(unittest.TestCase):
    def test_low_rubric_fails_even_without_hard_rule(self):
        p = policy()
        p = copy.deepcopy(p)
        p["rubric"]["pass_score"] = 101
        r = QualityGate(brief(), p).check(clean_hook(), clean_script())
        self.assertEqual(r.codes, ["RUBRIC_LOW"])

    def test_weights_sum_to_100(self):
        self.assertEqual(sum(policy()["rubric"]["weights"].values()), 100)

    def test_proof_without_number_scores_lower(self):
        s = clean_script()
        s["beats"][1]["say"] = "Here is the fix. The cap glows when it is time to drink."
        r = check(script=s)
        self.assertAlmostEqual(r.subscores["proof"], 0.7)
        self.assertLess(r.score, 100)

    def test_failures_are_deduplicated(self):
        s = with_say(2, "Best bottle ever. Best bottle ever.")
        r = check(script=s)
        self.assertEqual(r.codes.count("BANNED_CLAIM"), 1)


if __name__ == "__main__":
    unittest.main()
