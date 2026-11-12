"""Quality gate: hard rule checks plus a 0-100 scoring rubric.

An ad passes only if every hard rule passes AND the rubric score clears policy.rubric.pass_score.
Each failure carries a code, a plain-English reason and the stage that must be redone
("hook" means pick a new hook, "script" means rewrite the script with the reason as feedback).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .brief import Brief
from .textstats import fk_grade, sentences, speak_seconds, word_count

HOOK_CODES = {"HOOK_TOO_LONG", "HOOK_TOO_SHORT", "BLANK_FIRST_FRAME"}


@dataclass(frozen=True)
class Failure:
    code: str
    reason: str
    stage: str  # "hook" | "script"


@dataclass
class GateResult:
    passed: bool
    score: int
    failures: list[Failure] = field(default_factory=list)
    subscores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def codes(self) -> list[str]:
        return [f.code for f in self.failures]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "failures": [asdict(f) for f in self.failures],
            "subscores": self.subscores,
            "metrics": self.metrics,
        }


def load_policy(path: Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


class QualityGate:
    def __init__(self, brief: Brief, policy: dict[str, Any]):
        self.brief = brief
        self.policy = policy
        self.wps = float(policy["speech"]["words_per_second"])
        self.hook_wps = float(policy["hook"].get("words_per_second", self.wps))
        self._banned = [re.compile(p, re.I) for p in policy["claims"]["banned_patterns"]]
        self._triggers = [re.compile(p, re.I) for p in policy["claims"]["trigger_patterns"]]
        self._testimonial = [re.compile(p, re.I) for p in policy["disclosure"]["testimonial_patterns"]]
        self._approved = {_norm(c) for c in brief.approved_claims}

    # ---------------------------------------------------------------------------------
    def check_hook(self, hook: dict) -> list[Failure]:
        """Hook-only pre-screen, used to filter the hook bank before any script is written."""
        dummy = {"beats": [{"t": "0-2s", "say": hook["line"], "show": hook.get("first_frame", "")}], "caption": ""}
        return [f for f in self.check(hook, dummy).failures if f.stage == "hook"]

    def hook_score(self, hook: dict) -> float:
        dummy_beats = [{"t": "0-2s", "say": hook["line"], "show": ""}]
        return self._rubric(hook, dummy_beats, hook["line"], 0.0, 20.0)["hook"]

    # ---------------------------------------------------------------------------------
    def check(self, hook: dict, script: dict, platforms: tuple[str, ...] | None = None) -> GateResult:
        p, b = self.policy, self.brief
        platforms = platforms or b.platforms
        fails: list[Failure] = []

        def fail(code: str, reason: str) -> None:
            fails.append(Failure(code, reason, "hook" if code in HOOK_CODES else "script"))

        beats = script.get("beats") or []
        caption = script.get("caption", "")
        spoken = " ".join(x.get("say", "") for x in beats)
        shown = " ".join(x.get("show", "") for x in beats)
        everything = f"{spoken} {shown} {caption}"

        # --- hook ------------------------------------------------------------------
        hook_words = word_count(hook["line"])
        hook_secs = speak_seconds(hook["line"], self.hook_wps)
        if hook_words > p["hook"]["max_words"] or hook_secs > p["hook"]["max_seconds"]:
            fail("HOOK_TOO_LONG", f"hook is {hook_words} words ({hook_secs}s); max {p['hook']['max_words']} words / {p['hook']['max_seconds']}s")
        if hook_words < p["hook"]["min_words"]:
            fail("HOOK_TOO_SHORT", f"hook is {hook_words} words; min {p['hook']['min_words']}")
        ff = hook.get("first_frame", "").lower()
        if any(w in ff for w in p["first_frame"]["blank_words"]):
            fail("BLANK_FIRST_FRAME", f"first frame opens blank: {hook.get('first_frame')!r}")
        elif not any(w in ff for w in p["first_frame"]["must_mention_any"]):
            fail("BLANK_FIRST_FRAME", "first frame does not show the product, a face or the payoff")

        # --- structure -------------------------------------------------------------
        if not beats or _norm(beats[0].get("say", "")) != _norm(hook["line"]):
            fail("HOOK_NOT_FIRST", "the first beat must say the hook word for word")
        secs = speak_seconds(spoken, self.wps)
        lo, hi = p["script"]["min_seconds"], p["script"]["max_seconds"]
        if secs < lo:
            fail("TOO_SHORT", f"runs {secs}s at {self.wps} words/s; min {lo}s")
        if secs > hi:
            fail("TOO_LONG", f"runs {secs}s at {self.wps} words/s; max {hi}s")
        last = beats[-1].get("say", "") if beats else ""
        if _norm(b.cta) not in _norm(last):
            fail("CTA_MISSING", f'last beat must say the CTA word for word: "{b.cta}"')

        # --- claims ----------------------------------------------------------------
        for rx in self._banned:
            m = rx.search(everything)
            if m:
                fail("BANNED_CLAIM", f"banned claim: {m.group(0).strip()!r}")
        for sent in sentences(spoken):
            body = re.sub(r"^(fact|myth|hiking myth)\s*:\s*", "", sent, flags=re.I)
            if any(rx.search(body) for rx in self._triggers) and _norm(body) not in self._approved:
                if not any(rx.search(body) for rx in self._banned):
                    fail("UNAPPROVED_CLAIM", f"claim not on the approved list word for word: {body!r}")
        for rx in self._testimonial:
            m = rx.search(everything)
            if m:
                fail("TESTIMONIAL_IMPLIED", f"implies a real customer, result or review: {m.group(0)!r}")

        # --- brand -----------------------------------------------------------------
        for wrong, right in b.brand.spelling.items():
            if wrong in everything:
                fail("BRAND_SPELLING", f"brand spelled {wrong!r}; write {right!r}")
        for word in b.brand.required_words:
            if word not in spoken and word not in caption:
                fail("BRAND_MISSING", f"brand word {word!r} never appears")
        for word in b.brand.forbidden_words:
            if re.search(rf"\b{re.escape(word)}\b", everything, re.I):
                fail("FORBIDDEN_WORD", f"off-brand word: {word!r}")
        for comp in b.brand.competitors:
            if re.search(rf"\b{re.escape(comp)}\b", everything, re.I):
                fail("COMPETITOR_NAME", f"names a competitor: {comp!r}")

        # --- readability -----------------------------------------------------------
        # Graded per beat, so one stiff beat cannot hide inside an average of short lines.
        grade = fk_grade(spoken)
        beat_grades = [fk_grade(x.get("say", "")) for x in beats] or [0.0]
        worst = max(beat_grades)
        if grade > p["script"]["max_grade"] or worst > p["script"]["max_grade"]:
            fail("READABILITY", f"reads at grade {max(grade, worst)}; max {p['script']['max_grade']}")

        # --- platform policy -------------------------------------------------------
        d = p["disclosure"]
        if b.disclosure.ai_label and d["ai_label_tag"].lower() not in caption.lower():
            fail("DISCLOSURE_MISSING", f"AI-made video needs the AI label {d['ai_label_tag']} in the caption")
        if b.disclosure.paid_partnership and not all(t.lower() in caption.lower() for t in d["paid_tags"]):
            fail("DISCLOSURE_MISSING", "paid partnership needs #ad in the caption")
        if b.disclosure.affiliate and d["affiliate_line"].lower() not in (spoken + " " + caption).lower():
            fail("DISCLOSURE_MISSING", "affiliate link needs the disclosure line")
        tags = re.findall(r"#\w+", caption)
        if len(tags) > p["caption"]["max_hashtags"]:
            fail("TOO_MANY_HASHTAGS", f"{len(tags)} hashtags; max {p['caption']['max_hashtags']}")
        for plat in platforms:
            cap = p["caption"]["max_chars"].get(plat)
            if cap is not None and len(caption) > cap:
                fail("CAPTION_TOO_LONG", f"caption is {len(caption)} chars; {plat} allows {cap}")

        # --- rubric ----------------------------------------------------------------
        sub = self._rubric(hook, beats, spoken, grade, secs)
        weights = p["rubric"]["weights"]
        score = round(sum(sub[k] * weights[k] for k in weights))
        if score < p["rubric"]["pass_score"]:
            fail("RUBRIC_LOW", f"rubric score {score} below {p['rubric']['pass_score']}")

        # Dedupe identical failures (a banned phrase can match twice) while keeping order.
        seen, uniq = set(), []
        for f in fails:
            if (f.code, f.reason) not in seen:
                seen.add((f.code, f.reason))
                uniq.append(f)
        return GateResult(
            passed=not uniq,
            score=score,
            failures=uniq,
            subscores={k: round(v, 3) for k, v in sub.items()},
            metrics={"hook_words": hook_words, "hook_seconds": hook_secs, "seconds": secs, "grade": grade, "worst_beat_grade": worst, "hashtags": len(tags), "caption_chars": len(caption)},
        )

    # ---------------------------------------------------------------------------------
    def _rubric(self, hook: dict, beats: list[dict], spoken: str, grade: float, secs: float) -> dict[str, float]:
        """Each part scores 0..1. Weights live in policy.toml."""
        p, b = self.policy, self.brief
        line = hook["line"].lower()
        hw = word_count(hook["line"])
        # Hook: short (4-8 words is the sweet spot), opens a loop, tagged with an emotion.
        hook_s = 0.5 if 4 <= hw <= 8 else (0.3 if hw <= p["hook"]["max_words"] else 0.0)
        if "?" in line or any(re.search(rf"\b{m}\b", line) for m in p["rubric"]["curiosity_markers"]) or re.search(r"\d", line):
            hook_s += 0.3
        if hook.get("emotion") in b.emotions:
            hook_s += 0.2
        # Clarity: grade 4 or lower is full marks, falling to 0 at max_grade + 2.
        mg = p["script"]["max_grade"]
        clarity = 1.0 if grade <= 4 else max(0.0, 1 - (grade - 4) / (mg + 2 - 4))
        # Proof: an approved claim lands in the proof beat (2-10s), ideally with a number.
        proof_say = _norm(beats[1]["say"]) if len(beats) > 1 else ""
        if any(c in proof_say for c in self._approved):
            proof = 0.7 + (0.3 if re.search(r"\d", proof_say) else 0.0)
        else:
            proof = 0.4 if any(c in _norm(spoken) for c in self._approved) else 0.0
        # CTA: exact words in the last beat.
        cta = 1.0 if beats and _norm(b.cta) in _norm(beats[-1].get("say", "")) else 0.0
        # Pacing: 20-28s is the sweet spot inside the 15-35s window.
        lo, hi = p["script"]["min_seconds"], p["script"]["max_seconds"]
        pacing = 1.0 if 20 <= secs <= 28 else (0.6 if lo <= secs <= hi else 0.0)
        return {"hook": min(1.0, hook_s), "clarity": clarity, "proof": proof, "cta": cta, "pacing": pacing}
