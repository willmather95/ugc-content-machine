"""OfflineProvider: a deterministic, templated, seeded stand-in for an LLM.

It is NOT a language model. It fills phrase banks for a made-up brand and returns the same JSON
shape a real model is asked for. Same prompt + same seed = same bytes, which is what makes the
demo and the ledger reproducible.

To exercise the quality gate honestly, it makes the mistakes real models and real creators make
(too-long hooks, unapproved numbers, banned superlatives, a missing CTA, a competitor name, a
misspelled brand, stiff copy, a missing AI label). Mistakes are seeded, not hand-placed. When the
gate sends failure codes back in `avoid_codes`, the provider stops making those mistakes, the way
a model follows "avoid X" feedback, though it can still slip on a different one.
"""

from __future__ import annotations

import json

from ..prompts import PromptSpec
from ..util import rng
from .base import Completion, ProviderError, estimate_tokens

# Per-angle phrase bank. Keys mirror the angles in config/brief.json.
ANGLES: dict[str, dict] = {
    "forget": {
        "question2": "Do you forget to drink on hikes?",
        "stop2": "Your phone alarm is not a hydration plan.",
        "topic": "drinking on the trail",
        "tease": "Number 3 reminds me.",
        "pov": "mile six and you have not had one sip",
        "question": "Why do I only drink water once I get home?",
        "stop": "Stop finishing hikes with a full bottle.",
        "myth": "you will drink when you are thirsty",
        "fact": "By the time you feel thirsty, you are already behind",
        "item": "the bottle that tells me to drink",
        "frame": "close-up of a full bottle on a trail map, hand reaching in",
        "proof": "I used to finish every hike with a full bottle.",
        "details": [
            ["Now I take a sip each time the cap lights up.", "No app. No alarm on my phone.", "I get back to the car and it is empty, which is the point."],
            ["It sits in my side pocket where I can see it.", "When it glows, I drink.", "That is the whole system, and it works."],
        ],
        "claims": ["The cap glows when it is time to drink.", "Holds 750 ml."],
    },
    "cold": {
        "question2": "Ever sip hot water at the top?",
        "stop2": "Warm water ruins the best view.",
        "topic": "summit water",
        "tease": "Number 3 beats the heat.",
        "pov": "you reach the top and your water is warm",
        "question": "Why is summit water always warm?",
        "stop": "Stop drinking warm water at the top.",
        "myth": "cold water turns warm by lunch",
        "fact": "Good insulation beats a hot afternoon",
        "item": "the bottle that is still cold at the top",
        "frame": "hand pulls the bottle from the pack at the summit, cold drops on the side",
        "proof": "I filled it with ice water at six this morning.",
        "details": [
            ["It rode in the sun on my pack all day.", "At the top, I poured it out and it was still cold.", "Ice still clinking."],
            ["Hot car, hot trail, hot rocks.", "First sip at the summit felt like a fridge.", "My friend asked to borrow it."],
        ],
        "claims": ["Keeps water cold for 24 hours.", "Holds 750 ml."],
    },
    "light": {
        "question2": "Is your bottle the heaviest thing you carry?",
        "stop2": "Your shoulders do not need a brick.",
        "topic": "pack weight",
        "tease": "Number 3 saved my shoulders.",
        "pov": "your pack feels like bricks by mile three",
        "question": "Why does my pack feel so heavy?",
        "stop": "Stop hauling a heavy bottle up every hill.",
        "myth": "a cold bottle has to be heavy",
        "fact": "Good steel walls do the job without the bulk",
        "item": "the bottle I forget I am carrying",
        "frame": "the bottle on a kitchen scale next to a trail pack",
        "proof": "I put it on the scale before my last hike.",
        "details": [
            ["My old one felt like a brick.", "This one clips on and I stop thinking about it.", "My shoulders noticed first."],
            ["Less weight means more snacks.", "That is the math I care about.", "It fits the side pocket too."],
        ],
        "claims": ["Weighs 310 grams empty.", "Keeps water cold for 24 hours."],
    },
    "glow": {
        "question2": "Have you seen a bottle cap glow?",
        "stop2": "Your bottle can remind you to drink.",
        "topic": "drink reminders",
        "tease": "Number 3 lights up.",
        "pov": "your water bottle lights up halfway up the hill",
        "question": "Wait, why is my water bottle glowing?",
        "stop": "Stop setting phone alarms to drink water.",
        "myth": "you need an app to remember to drink",
        "fact": "A light you can see works better than a buzz you ignore",
        "item": "the cap that reminds me to drink",
        "frame": "the cap glowing in a hand on a shady trail",
        "proof": "The first time it lit up, I laughed.",
        "details": [
            ["Then I took a sip.", "Now it is a habit.", "My phone stays in my pocket and I still drink."],
            ["It is not loud and it does not buzz.", "It just glows in the side pocket.", "You notice it and you drink."],
        ],
        "claims": ["The cap glows when it is time to drink.", "Holds 750 ml."],
    },
    "returns": {
        "question2": "Scared to try new trail gear?",
        "stop2": "Hate it? Send it back.",
        "topic": "buying new gear",
        "tease": "Number 3 almost went back.",
        "pov": "you buy new gear and regret it after one hike",
        "question": "What if your new bottle could just go back?",
        "stop": "Stop keeping gear you do not love.",
        "myth": "trying new gear is a gamble",
        "fact": "Not when you can send it back",
        "item": "the bottle I could send back for free",
        "frame": "face of a hiker at a trailhead, holding the bottle up to camera",
        "proof": "I almost did not buy it.",
        "details": [
            ["Then I saw I could return it.", "So I took it on three hikes to test it.", "It is still in my pack."],
            ["I gave it a real test on my longest loop.", "If I hated it, back it went.", "I did not send it back."],
        ],
        "claims": ["Free returns for 30 days.", "Keeps water cold for 24 hours."],
    },
}

# Hook shapes per format: (template, emotion).
HOOK_SHAPES: dict[str, list[tuple[str, str]]] = {
    "pov-fix": [("POV: {pov}.", "humor"), ("{question}", "curiosity"), ("{stop}", "relief"), ("POV: {pov}. Not anymore.", "relief"), ("{stop2}", "relief"), ("{question2}", "curiosity")],
    "myth-fact": [("Hiking myth: {myth}.", "curiosity"), ("Myth: {myth}. Wrong.", "curiosity"), ("{question}", "curiosity"), ("Nobody tells you this about {topic}.", "fomo"), ("Myth or fact: {myth}?", "curiosity"), ("Fact check: {myth}.", "curiosity")],
    "three-things": [("3 things in my pack. {tease}", "curiosity"), ("My day pack has 3 things. One is {item}.", "status"), ("{question}", "curiosity"), ("3 things I hike with. {tease}", "status"), ("Packing list, 3 things. {tease}", "status"), ("{stop2}", "relief")],
}

PROOF_OPENERS = {
    "pov-fix": "Here is the fix.",
    "myth-fact": "Fact: {fact}.",
    "three-things": "One, a map. Two, a snack. Three, this.",
}

HASHTAGS = ["#hiking", "#dayhike", "#trailtips", "#hikinggear", "#outdoors", "#hydration"]

# Seeded mistakes. Codes match the gate's rule codes so feedback can name them.
HOOK_FLAWS = ["HOOK_TOO_LONG", "BLANK_FIRST_FRAME"]
SCRIPT_FLAWS = [
    "BANNED_CLAIM",
    "UNAPPROVED_CLAIM",
    "CTA_MISSING",
    "COMPETITOR_NAME",
    "BRAND_SPELLING",
    "FORBIDDEN_WORD",
    "READABILITY",
    "DISCLOSURE_MISSING",
    "TESTIMONIAL_IMPLIED",
    "TOO_SHORT",
]


class OfflineProvider:
    """Deterministic offline provider. Labeled as such everywhere it shows up."""

    name = "offline"
    model = "offline-templated-v1"
    is_paid = False
    deterministic = True

    def __init__(self, hook_flaw_rate: float = 0.22, script_flaw_rate: float = 0.40, retry_flaw_rate: float = 0.30):
        self.hook_flaw_rate = hook_flaw_rate
        self.script_flaw_rate = script_flaw_rate
        self.retry_flaw_rate = retry_flaw_rate

    # ---------------------------------------------------------------------------------
    def complete(self, spec: PromptSpec) -> Completion:
        if spec.task == "hooks":
            payload = self._hooks(spec)
        elif spec.task == "script":
            payload = self._script(spec)
        else:
            raise ProviderError(f"offline provider has no template for task {spec.task!r}")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=estimate_tokens(spec.system + spec.user),
            output_tokens=estimate_tokens(text),
            meta={"label": "OFFLINE: templated + seeded, not a language model"},
        )

    # ---------------------------------------------------------------------------------
    def _hooks(self, spec: PromptSpec) -> dict:
        v = spec.vars
        bank = ANGLES.get(v["angle_id"])
        shapes = HOOK_SHAPES.get(v["format_id"])
        if bank is None or shapes is None:
            raise ProviderError(f"no phrase bank for format={v['format_id']} angle={v['angle_id']}")
        r = rng(spec.seed, "hooks", v["format_id"], v["angle_id"], v["avoid"])
        avoid = set(v.get("avoid_codes", []))
        rate = self.retry_flaw_rate if avoid else self.hook_flaw_rate
        hooks = []
        order = list(range(len(shapes)))
        r.shuffle(order)
        for i in range(int(v["n"])):
            tmpl, emotion = shapes[order[i % len(shapes)]]
            line = tmpl.format(**bank)
            line = line[0].upper() + line[1:]
            frame = bank["frame"]
            allowed = emotion if emotion in v["emotions"] else v["emotions"].split(", ")[0]
            if r.random() < rate:
                flaw = r.choice([f for f in HOOK_FLAWS if f not in avoid] or [None])
                if flaw == "HOOK_TOO_LONG":
                    line = line.rstrip(".?") + ", and honestly I did not expect this to change my whole hike."
                elif flaw == "BLANK_FIRST_FRAME":
                    frame = "black screen, then fade in on the logo"
            hooks.append({"line": line, "first_frame": frame, "emotion": allowed})
        return {"hooks": hooks}

    # ---------------------------------------------------------------------------------
    def _script(self, spec: PromptSpec) -> dict:
        v = spec.vars
        bank = ANGLES.get(v["angle_id"])
        if bank is None:
            raise ProviderError(f"no phrase bank for angle={v['angle_id']}")
        r = rng(spec.seed, "script", v["format_id"], v["angle_id"], v["hook"], v["avoid"])
        avoid = set(v.get("avoid_codes", []))
        rate = self.retry_flaw_rate if avoid else self.script_flaw_rate

        claims = [c for c in bank["claims"] if c in v["approved_claims"]] or list(v["approved_claims"][:1])
        details = list(r.choice(bank["details"]))
        opener = PROOF_OPENERS.get(v["format_id"], "").format(**bank)
        brand_line = f"It is the {v['product']}."
        beats = [
            {"t": "0-2s", "say": v["hook"], "show": v["first_frame"]},
            {"t": "2-10s", "say": f"{opener} {bank['proof']} {claims[0]}".strip(), "show": f"{v['brand']} bottle in hand, on screen by second 3"},
            {"t": "10-25s", "say": " ".join(details + [brand_line] + claims[1:2]), "show": "trail b-roll, bottle in the side pocket, big burned-in captions"},
            {"t": "last 3s", "say": f"{v['cta']}.", "show": f"bottle on a rock, link sticker {v['url']}"},
        ]
        tags = r.sample(HASHTAGS, 3)
        caption_bits = [f"{bank['item'][0].upper()}{bank['item'][1:]}.", " ".join(tags)]
        if v.get("needs_ai_label"):
            caption_bits.append(v["ai_label_tag"])
        if v.get("needs_paid_tag"):
            caption_bits.append("#ad")
        caption = " ".join(caption_bits)

        if r.random() < rate:
            pool = [f for f in SCRIPT_FLAWS if f not in avoid]
            if pool:
                flaw = r.choice(pool)
                beats, caption = _apply_script_flaw(flaw, beats, caption, v)
        return {"beats": beats, "caption": caption}


def _apply_script_flaw(flaw: str, beats: list[dict], caption: str, v: dict) -> tuple[list[dict], str]:
    b = [dict(x) for x in beats]
    if flaw == "BANNED_CLAIM":
        b[2]["say"] += " Honestly, it is the best bottle ever."
    elif flaw == "UNAPPROVED_CLAIM":
        b[1]["say"] += " Keeps water cold for 48 hours."
    elif flaw == "CTA_MISSING":
        b[3]["say"] = "Check it out."
    elif flaw == "COMPETITOR_NAME":
        b[2]["say"] += " Way better than my old HydroPeak."
    elif flaw == "BRAND_SPELLING":
        b[2]["say"] = b[2]["say"].replace(v["brand"], "FernWay")
    elif flaw == "FORBIDDEN_WORD":
        b[2]["say"] += " Not some cheap knockoff."
    elif flaw == "READABILITY":
        b[2]["say"] = (
            "Its vacuum-insulated stainless construction maintains exceptional temperature "
            "consistency throughout prolonged recreational excursions, regardless of environmental variability."
        )
    elif flaw == "DISCLOSURE_MISSING":
        caption = caption.replace(v["ai_label_tag"], "").strip()
    elif flaw == "TESTIMONIAL_IMPLIED":
        b[2]["say"] += " Real results from a real customer."
    elif flaw == "TOO_SHORT":
        b = [b[0], {"t": "2-10s", "say": "Look.", "show": b[1]["show"]}, b[3]]
    return b, caption
