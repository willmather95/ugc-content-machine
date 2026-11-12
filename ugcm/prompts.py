"""Versioned prompt templates.

Every LLM call is built from a named template with a version. The ledger stores the template
id, version and a hash of the template text, so a later run can tell whether a prompt changed.
Bump the version whenever you edit a template.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from string import Template
from typing import Any

from .util import canonical_json, sha256_text

SYSTEM = (
    "You write short-form vertical video ads (9:16, 15-35 seconds) for one brand. "
    "Use only the approved claims, word for word. Never invent results, reviews or customers. "
    "Never name competitors. Reply with JSON only, matching the schema in the request."
)

HOOKS_V = "hooks@3"
HOOKS = Template(
    """Brand: $brand ($product). Audience: $audience.
Format: $format_name. Shape: $format_beats.
Angle: $angle_name.
Write $n hooks. Each hook is a spoken line (max 12 words, said in the first 2 seconds)
plus a first-frame visual that shows the bottle, a face or the payoff. Never a blank frame.
Tag each with one emotion from: $emotions.
Avoid these problems from earlier attempts: $avoid
Schema: {"hooks": [{"line": str, "first_frame": str, "emotion": str}]}"""
)

SCRIPT_V = "script@4"
SCRIPT = Template(
    """Brand: $brand ($product). Audience: $audience.
Format: $format_name. Angle: $angle_name.
Hook (say it first, word for word): "$hook"
First frame: $first_frame
Approved claims (use one or two, word for word, nothing else): $claims
CTA (exact words, in the last beat): "$cta"
Beat sheet: 0-2s hook | 2-10s proof | 10-25s detail | last 3s CTA. Grade 8 reading level or lower.
Caption: under 150 characters, at most 4 hashtags, include $disclosure.
Avoid these problems from earlier attempts: $avoid
Schema: {"beats": [{"t": str, "say": str, "show": str}], "caption": str}"""
)

TEMPLATES = {HOOKS_V: HOOKS, SCRIPT_V: SCRIPT}


@dataclass(frozen=True)
class PromptSpec:
    task: str                 # "hooks" | "script"
    template_id: str          # e.g. "script@4"
    system: str
    user: str
    vars: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    max_tokens: int = 1200

    @property
    def template_sha(self) -> str:
        return sha256_text(TEMPLATES[self.template_id].template)[:16]

    @property
    def prompt_sha(self) -> str:
        return sha256_text(canonical_json({"s": self.system, "u": self.user, "seed": self.seed}))[:16]


def _avoid(avoid: list[str]) -> str:
    return "; ".join(avoid) if avoid else "none"


def hooks_prompt(*, brief, fmt, angle, n: int, seed: int, avoid: list[str] | None = None) -> PromptSpec:
    avoid = avoid or []
    v = {
        "brand": brief.brand.name,
        "product": brief.brand.product,
        "audience": brief.audience,
        "format_id": fmt.id,
        "format_name": fmt.name,
        "format_beats": fmt.beats,
        "angle_id": angle.id,
        "angle_name": angle.name,
        "emotions": ", ".join(brief.emotions),
        "n": n,
        "avoid": _avoid(avoid),
        "avoid_codes": list(avoid),
    }
    return PromptSpec("hooks", HOOKS_V, SYSTEM, HOOKS.substitute(v), v, seed, 900)


def disclosure_text(brief, policy) -> str:
    bits = []
    if brief.disclosure.ai_label:
        bits.append(f"the AI label {policy['disclosure']['ai_label_tag']}")
    if brief.disclosure.paid_partnership:
        bits.append("#ad")
    if brief.disclosure.affiliate:
        bits.append(f'the line "{policy["disclosure"]["affiliate_line"]}"')
    return " and ".join(bits) or "no disclosure"


def script_prompt(*, brief, policy, fmt, angle, hook: dict, seed: int, avoid: list[str] | None = None) -> PromptSpec:
    avoid = avoid or []
    v = {
        "brand": brief.brand.name,
        "product": brief.brand.product,
        "audience": brief.audience,
        "format_id": fmt.id,
        "format_name": fmt.name,
        "angle_id": angle.id,
        "angle_name": angle.name,
        "hook": hook["line"],
        "first_frame": hook["first_frame"],
        "emotion": hook["emotion"],
        "claims": " | ".join(brief.approved_claims),
        "approved_claims": list(brief.approved_claims),
        "cta": brief.cta,
        "url": brief.brand.url,
        "disclosure": disclosure_text(brief, policy),
        "ai_label_tag": policy["disclosure"]["ai_label_tag"],
        "needs_ai_label": brief.disclosure.ai_label,
        "needs_paid_tag": brief.disclosure.paid_partnership,
        "avoid": _avoid(avoid),
        "avoid_codes": list(avoid),
    }
    return PromptSpec("script", SCRIPT_V, SYSTEM, SCRIPT.substitute(v), v, seed, 1200)
