"""Shared test helpers. Everything is offline."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from ugcm.brief import load_brief, parse_brief
from ugcm.cost import load_prices
from ugcm.gate import load_policy

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
FIXTURES = ROOT / "fixtures"


def brief(**overrides):
    data = json.loads((CONFIG / "brief.json").read_text())
    data.update(overrides)
    return parse_brief(data)


def brief_data() -> dict:
    return copy.deepcopy(json.loads((CONFIG / "brief.json").read_text()))


def policy():
    return load_policy(CONFIG / "policy.toml")


def prices():
    return load_prices(CONFIG / "prices.toml")


def verified_prices():
    return load_prices(FIXTURES / "prices.verified-test.toml")


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="ugcm-test-"))


def clean_hook() -> dict:
    return {"line": "Why is summit water always warm?", "first_frame": "hand pulls the bottle from the pack at the summit", "emotion": "curiosity"}


def clean_script() -> dict:
    return {
        "beats": [
            {"t": "0-2s", "say": "Why is summit water always warm?", "show": "hand pulls the bottle from the pack"},
            {"t": "2-10s", "say": "Here is the fix. I filled it with ice water at six this morning. Keeps water cold for 24 hours.", "show": "Fernway bottle in hand"},
            {"t": "10-25s", "say": "It rode in the sun on my pack all day. At the top, I poured it out and it was still cold. Ice still clinking. It is the Fernway Trail Bottle. Holds 750 ml.", "show": "trail b-roll"},
            {"t": "last 3s", "say": "Tap the link to get yours.", "show": "bottle on a rock"},
        ],
        "caption": "The bottle that is still cold at the top. #hiking #dayhike #trailtips #AIgenerated",
    }


__all__ = ["brief", "brief_data", "policy", "prices", "verified_prices", "tmpdir", "clean_hook", "clean_script", "load_brief", "ROOT", "CONFIG", "FIXTURES"]
