"""Small shared helpers: stable hashing, seeded RNGs, canonical JSON."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    """JSON with sorted keys and no whitespace drift, so equal data hashes equally."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def derive_seed(*parts: Any) -> int:
    """A 63-bit seed derived from any parts. Python's hash() is salted per process; this is not."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def rng(*parts: Any) -> random.Random:
    return random.Random(derive_seed(*parts))


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
