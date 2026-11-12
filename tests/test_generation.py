import json
import unittest
from decimal import Decimal

from ugcm.cost import Authorization, BudgetExceeded, SpendNotApproved
from ugcm.generation import REFERENCE_IMAGE, DryRunBackend, NetworkDisabled, TregBackend, build_requests

from .helpers import clean_hook, clean_script, prices, tmpdir

CTX = {"ad_id": "AD-001", "hook": "Why is summit water always warm?", "first_frame": "bottle at the summit", "format_name": "POV problem, then fix", "angle_name": "Still cold at the top", "angle_index": 1, "score": 100}


def reqs(seconds=25.4):
    return build_requests(ad_id="AD-001", script=clean_script(), hook=clean_hook(), seconds=seconds, seed=7, prices=prices(), tier="video-draft", clip_seconds=8, aspect="9:16")


class RequestTest(unittest.TestCase):
    def test_three_requests_per_ad(self):
        r = reqs()
        self.assertEqual([x.kind for x in r], ["video", "image", "voice"])
        v = r[0]
        self.assertEqual(v.params["duration_s"], 32)
        self.assertEqual(v.params["clips"], 4)
        self.assertEqual(v.params["aspect_ratio"], "9:16")
        self.assertEqual(v.params["reference_image"], REFERENCE_IMAGE)
        self.assertEqual(r[1].params["reference_image"], REFERENCE_IMAGE)
        self.assertEqual(v.params["chain"], "continue_from_last_frame")
        self.assertIn("Tap the link to get yours.", r[2].params["text"])

    def test_payload_hash_is_stable(self):
        self.assertEqual(reqs()[0].payload_sha, reqs()[0].payload_sha)
        self.assertNotEqual(reqs(25.4)[0].payload_sha, reqs(33)[0].payload_sha)


class DryRunTest(unittest.TestCase):
    def test_writes_exact_payload_and_placeholder(self):
        out = tmpdir()
        be = DryRunBackend()
        self.assertFalse(be.is_paid)
        results = [be.submit(r, out, None, CTX) for r in reqs()]
        for r, res in zip(reqs(), results):
            self.assertEqual(json.loads(res.request_path.read_text()), r.payload())
            self.assertEqual(res.status, "dry-run")
        frame = (out / "frame.svg").read_text()
        self.assertIn("PLACEHOLDER FRAME", frame)
        self.assertIn('width="540" height="960"', frame)
        image_assets = results[1].assets
        self.assertTrue(any(p.name == "frame.svg" for p in image_assets))


class TregTest(unittest.TestCase):
    def test_refuses_without_authorization(self):
        out = tmpdir()
        with self.assertRaises(SpendNotApproved):
            TregBackend(prices()).submit(reqs()[0], out, None, CTX)
        self.assertFalse((out / "requests").exists())

    def test_default_transport_never_touches_network(self):
        auth = Authorization(approved=Decimal("100.00"))
        with self.assertRaises(NetworkDisabled):
            TregBackend(prices()).submit(reqs()[0], tmpdir(), auth, CTX)

    def test_catalog_body_and_metering(self):
        calls = []
        be = TregBackend(prices(), transport=lambda m, b: calls.append((m, b)) or {"job_id": "job-1"})
        auth = Authorization(approved=Decimal("5.00"))
        res = be.submit(reqs()[0], tmpdir(), auth, CTX)
        self.assertEqual(res.status, "submitted")
        self.assertEqual(calls[0][0], "call")
        body = calls[0][1]
        self.assertEqual(body["tool_id"], "video.veo-3-1-fast")
        self.assertEqual(body["max_cost_usd"], "4.80")
        self.assertEqual(len(body["idempotency_key"]), 32)
        self.assertEqual(auth.spent, Decimal("4.80"))
        # The next video would cross the approved line: charged before sending, so nothing goes out.
        with self.assertRaises(BudgetExceeded):
            be.submit(reqs()[0], tmpdir(), auth, CTX)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
