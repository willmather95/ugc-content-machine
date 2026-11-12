"""DryRunBackend: free and offline. Writes the exact request payloads plus a placeholder
first frame (SVG, and a PNG thumbnail when Pillow is installed). Spends nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cost import Authorization
from ..render import placeholder_frame_svg, placeholder_thumb_png
from ..util import pretty_json, write_text
from .base import GenerationRequest, GenerationResult


class DryRunBackend:
    name = "dryrun"
    is_paid = False

    def submit(self, req: GenerationRequest, out_dir: Path, auth: Authorization | None, ctx: dict[str, Any]) -> GenerationResult:
        req_path = write_text(out_dir / "requests" / f"{req.kind}.json", pretty_json(req.payload()))
        assets: list[Path] = []
        if req.kind == "image":
            assets.append(write_text(out_dir / "frame.svg", placeholder_frame_svg(ctx)))
            png = placeholder_thumb_png(ctx, out_dir / "thumb.png")
            if png is not None:
                assets.append(png)
        return GenerationResult(req.ad_id, req.kind, "dry-run", req_path, req.payload_sha, assets, "DRY RUN: payload written, nothing sent")
