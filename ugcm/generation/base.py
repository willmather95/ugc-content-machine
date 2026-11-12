"""GenerationBackend interface: turns a gated script into asset requests.

One ad becomes three requests: a 9:16 video (chained from fixed-length clips, same reference
image in every clip), a first-frame still (the thumbnail) and a voiceover. The request payload
is built once here so a dry run writes the exact bytes a paid backend would send.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..cost import Authorization
from ..util import canonical_json, sha256_text

REFERENCE_IMAGE = "brand/fernway-trail-bottle-ref.png"  # one product reference for every clip


@dataclass(frozen=True)
class GenerationRequest:
    ad_id: str
    kind: str              # "video" | "image" | "voice"
    model_key: str         # key in config/prices.toml
    tool_id: str           # catalog tool id the request targets
    params: dict[str, Any]
    seed: int

    def payload(self) -> dict[str, Any]:
        return {"tool_id": self.tool_id, "params": self.params, "seed": self.seed, "client_ref": f"{self.ad_id}:{self.kind}"}

    @property
    def payload_sha(self) -> str:
        return sha256_text(canonical_json(self.payload()))


@dataclass
class GenerationResult:
    ad_id: str
    kind: str
    status: str                       # "dry-run" | "submitted" | "refused"
    request_path: Path
    payload_sha: str
    assets: list[Path] = field(default_factory=list)
    detail: str = ""


@runtime_checkable
class GenerationBackend(Protocol):
    name: str
    is_paid: bool

    def submit(self, req: GenerationRequest, out_dir: Path, auth: Authorization | None, ctx: dict[str, Any]) -> GenerationResult: ...


def build_requests(*, ad_id: str, script: dict, hook: dict, seconds: float, seed: int, prices, tier: str, clip_seconds: int, aspect: str) -> list[GenerationRequest]:
    beats = script["beats"]
    clips = max(1, math.ceil(seconds / clip_seconds))
    shot_list = [{"t": b["t"], "show": b["show"], "caption": b["say"]} for b in beats]
    video = GenerationRequest(
        ad_id=ad_id,
        kind="video",
        model_key=tier,
        tool_id=prices.models[tier]["tool_id"],
        params={
            "aspect_ratio": aspect,
            "duration_s": clips * clip_seconds,
            "clip_seconds": clip_seconds,
            "clips": clips,
            "chain": "continue_from_last_frame",
            "reference_image": REFERENCE_IMAGE,
            "shot_list": shot_list,
            "first_frame": hook["first_frame"],
            "captions": "burned_in_off",  # captions and numbers are rendered in code later, never by the video model
            "audio": "none",              # one voice pass replaces audio at the end
        },
        seed=seed,
    )
    image = GenerationRequest(
        ad_id=ad_id,
        kind="image",
        model_key="thumb-image",
        tool_id=prices.models["thumb-image"]["tool_id"],
        params={"aspect_ratio": aspect, "prompt": hook["first_frame"], "reference_image": REFERENCE_IMAGE, "text_overlay": "none"},
        seed=seed,
    )
    voice = GenerationRequest(
        ad_id=ad_id,
        kind="voice",
        model_key="voice",
        tool_id=prices.models["voice"]["tool_id"],
        params={"text": " ".join(b["say"] for b in beats), "voice": "warm-neutral-1", "pace_wps": 2.6, "format": "wav"},
        seed=seed,
    )
    return [video, image, voice]
