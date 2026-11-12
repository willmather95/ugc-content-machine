"""Placeholder frames, thumbnails and the cohort contact sheet.

SVG output is pure stdlib and byte-stable. PNG output needs Pillow (optional); when Pillow is
missing, PNG steps are skipped and the SVG versions are still written.
"""

from __future__ import annotations

import textwrap
from html import escape
from pathlib import Path
from typing import Any

try:  # Pillow is optional
    from PIL import Image, ImageDraw, ImageFont

    HAVE_PIL = True
except ImportError:  # pragma: no cover - exercised only without Pillow
    HAVE_PIL = False

# world-class-charts categorical palette, in order.
PALETTE = ["#0a72e8", "#e8590c", "#14866d", "#b13f8a", "#521af9", "#06626b", "#84520b", "#5f524e"]
PASS = "#10935f"
HELD = "#dc383d"
INK = "#141414"
MUTED = "#6b6b6b"
PAGE = "#f2f2f1"


def angle_color(ctx: dict[str, Any]) -> str:
    return PALETTE[int(ctx.get("angle_index", 0)) % len(PALETTE)]


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(c: str, base: str, t: float) -> tuple[int, int, int]:
    a, b = _hex(c), _hex(base)
    return tuple(round(a[i] * t + b[i] * (1 - t)) for i in range(3))  # type: ignore[return-value]


# ------------------------------------------------------------------------------------------
def placeholder_frame_svg(ctx: dict[str, Any]) -> str:
    """A 540x960 (9:16) placeholder first frame. Clearly labeled as a placeholder."""
    col = angle_color(ctx)
    lines = textwrap.wrap(ctx["hook"], 18)[:5]
    hook_tspans = "".join(f'<tspan x="40" dy="{0 if i == 0 else 62}">{escape(l)}</tspan>' for i, l in enumerate(lines))
    ff = textwrap.wrap(ctx["first_frame"], 44)[:3]
    ff_tspans = "".join(f'<tspan x="40" dy="{0 if i == 0 else 24}">{escape(l)}</tspan>' for i, l in enumerate(ff))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="540" height="960" viewBox="0 0 540 960">
  <rect width="540" height="960" fill="{col}"/>
  <rect y="0" width="540" height="56" fill="#141414"/>
  <text x="20" y="36" font-family="Geist, Inter, Arial, sans-serif" font-size="20" font-weight="600" fill="#ffffff">PLACEHOLDER FRAME · DRY RUN · {escape(ctx['ad_id'])}</text>
  <text x="40" y="200" font-family="Geist, Inter, Arial, sans-serif" font-size="52" font-weight="700" fill="#ffffff">{hook_tspans}</text>
  <rect x="40" y="620" width="460" height="140" rx="16" fill="#ffffff" fill-opacity="0.16"/>
  <text x="60" y="660" font-family="Geist, Inter, Arial, sans-serif" font-size="18" fill="#ffffff" font-weight="600">FIRST FRAME (to generate)</text>
  <text x="40" y="700" font-family="Geist, Inter, Arial, sans-serif" font-size="18" fill="#ffffff" transform="translate(20,0)">{ff_tspans}</text>
  <text x="40" y="860" font-family="Geist, Inter, Arial, sans-serif" font-size="22" fill="#ffffff">{escape(ctx['format_name'])} · {escape(ctx['angle_name'])}</text>
  <text x="40" y="900" font-family="Geist, Inter, Arial, sans-serif" font-size="20" fill="#ffffff" fill-opacity="0.85">AI-generated · label required</text>
</svg>
"""


def _font(size: int, bold: bool = False):
    # Pillow >= 10.1 ships a scalable default font, so output does not depend on system fonts.
    return ImageFont.load_default(size=size)


def placeholder_thumb_png(ctx: dict[str, Any], path: Path, size: tuple[int, int] = (270, 480)) -> Path | None:
    if not HAVE_PIL:
        return None
    img = _thumb(ctx, size)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=False)
    return path


def _thumb(ctx: dict[str, Any], size: tuple[int, int]):
    w, h = size
    col = angle_color(ctx)
    img = Image.new("RGB", size, _hex(col))
    d = ImageDraw.Draw(img)
    s = w / 270
    d.rectangle([0, 0, w, int(26 * s)], fill=_hex(INK))
    d.text((int(8 * s), int(6 * s)), f"PLACEHOLDER · {ctx['ad_id']}", font=_font(int(11 * s)), fill=(255, 255, 255))
    y = int(70 * s)
    for line in textwrap.wrap(ctx["hook"], 16)[:6]:
        d.text((int(16 * s), y), line, font=_font(int(24 * s)), fill=(255, 255, 255))
        y += int(30 * s)
    d.text((int(16 * s), h - int(56 * s)), ctx["format_name"], font=_font(int(13 * s)), fill=(255, 255, 255))
    d.text((int(16 * s), h - int(34 * s)), "AI-generated · label required", font=_font(int(11 * s)), fill=_mix("#ffffff", col, 0.8))
    return img


# ------------------------------------------------------------------------------------------
def contact_sheet_png(items: list[dict[str, Any]], path: Path, *, title: str, subtitle: str, cols: int = 6) -> Path | None:
    """Grid of every ad in the cohort. Passed ads show their placeholder frame; held ads are
    greyed with the failure codes printed on them."""
    if not HAVE_PIL:
        return None
    tw, th, label_h, gap, pad, head = 216, 384, 64, 18, 48, 120
    rows = (len(items) + cols - 1) // cols
    W = pad * 2 + cols * tw + (cols - 1) * gap
    H = head + pad + rows * (th + label_h) + (rows - 1) * gap + pad
    sheet = Image.new("RGB", (W, H), _hex(PAGE))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 36), title, font=_font(34), fill=_hex(INK))
    d.text((pad, 82), subtitle, font=_font(18), fill=_hex(MUTED))
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (tw + gap)
        y = head + pad + r * (th + label_h + gap)
        thumb = _thumb(it, (tw, th))
        if it["status"] != "passed":
            grey = Image.new("RGB", (tw, th), (236, 236, 235))
            thumb = Image.blend(thumb, grey, 0.78)
            td = ImageDraw.Draw(thumb)
            td.rectangle([0, th // 2 - 40, tw, th // 2 + 40], fill=_hex(HELD))
            td.text((14, th // 2 - 32), "HELD", font=_font(24), fill=(255, 255, 255))
            td.text((14, th // 2 + 2), ", ".join(it.get("codes", []))[:30], font=_font(12), fill=(255, 255, 255))
        sheet.paste(thumb, (x, y))
        status_col = PASS if it["status"] == "passed" else HELD
        d.rectangle([x, y + th, x + tw, y + th + 4], fill=_hex(status_col))
        d.text((x, y + th + 12), f"{it['ad_id']}  score {it['score']}", font=_font(15), fill=_hex(INK))
        regen = it.get("regenerations", 0)
        d.text((x, y + th + 34), f"{it['angle_name']}" + (f"  ·  {regen} regen" if regen else ""), font=_font(13), fill=_hex(MUTED))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG", optimize=False)
    return path


def contact_sheet_svg(items: list[dict[str, Any]], *, title: str, subtitle: str, cols: int = 6) -> str:
    tw, th, label_h, gap, pad, head = 108, 192, 44, 12, 32, 90
    rows = (len(items) + cols - 1) // cols
    W = pad * 2 + cols * tw + (cols - 1) * gap
    H = head + pad + rows * (th + label_h) + (rows - 1) * gap + pad
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Geist, Inter, Arial, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="{PAGE}"/>',
        f'<text x="{pad}" y="44" font-size="24" font-weight="700" fill="{INK}">{escape(title)}</text>',
        f'<text x="{pad}" y="70" font-size="13" fill="{MUTED}">{escape(subtitle)}</text>',
    ]
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (tw + gap)
        y = head + pad + r * (th + label_h + gap)
        fill = angle_color(it) if it["status"] == "passed" else "#d9d9d8"
        parts.append(f'<rect x="{x}" y="{y}" width="{tw}" height="{th}" rx="6" fill="{fill}"/>')
        for j, line in enumerate(textwrap.wrap(it["hook"], 14)[:5]):
            parts.append(f'<text x="{x + 8}" y="{y + 30 + j * 16}" font-size="12" font-weight="600" fill="#ffffff">{escape(line)}</text>')
        if it["status"] != "passed":
            parts.append(f'<rect x="{x}" y="{y + th // 2 - 14}" width="{tw}" height="28" fill="{HELD}"/>')
            parts.append(f'<text x="{x + 8}" y="{y + th // 2 + 5}" font-size="12" font-weight="700" fill="#ffffff">HELD</text>')
        col = PASS if it["status"] == "passed" else HELD
        parts.append(f'<rect x="{x}" y="{y + th}" width="{tw}" height="3" fill="{col}"/>')
        parts.append(f'<text x="{x}" y="{y + th + 18}" font-size="11" fill="{INK}">{escape(it["ad_id"])} · {it["score"]}</text>')
        parts.append(f'<text x="{x}" y="{y + th + 34}" font-size="10" fill="{MUTED}">{escape(it["angle_name"][:20])}</text>')
    parts.append("</svg>\n")
    return "\n".join(parts)
