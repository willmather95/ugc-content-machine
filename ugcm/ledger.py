"""Prompt and asset ledger (SQLite, stdlib only).

Every prompt, template version, seed, response, gate result, asset path + SHA-256 and cost line
goes in one file next to the cohort. Two runs with the same brief, seed, policy and provider
produce the same ledger fingerprint; `verify` re-hashes every asset on disk against the ledger.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .util import canonical_json, sha256_file, sha256_text

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, cohort_id TEXT NOT NULL, created_at TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL, provider_deterministic INTEGER NOT NULL,
  backend TEXT NOT NULL, policy_version TEXT NOT NULL,
  brief_sha TEXT NOT NULL, policy_sha TEXT NOT NULL, prices_sha TEXT NOT NULL, seed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ad_id TEXT, stage TEXT NOT NULL,
  attempt INTEGER NOT NULL, template_id TEXT NOT NULL, template_sha TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL, seed INTEGER NOT NULL,
  prompt_sha TEXT NOT NULL, prompt_text TEXT NOT NULL, response_text TEXT NOT NULL, response_sha TEXT NOT NULL,
  input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ads (
  run_id TEXT NOT NULL, ad_id TEXT NOT NULL, format_id TEXT NOT NULL, angle_id TEXT NOT NULL,
  emotion TEXT NOT NULL, hook TEXT NOT NULL, first_frame TEXT NOT NULL, script_json TEXT NOT NULL,
  script_sha TEXT NOT NULL, seconds REAL NOT NULL, status TEXT NOT NULL, regenerations INTEGER NOT NULL,
  final_score INTEGER NOT NULL, PRIMARY KEY (run_id, ad_id)
);
CREATE TABLE IF NOT EXISTS gate_results (
  run_id TEXT NOT NULL, ad_id TEXT NOT NULL, attempt INTEGER NOT NULL, passed INTEGER NOT NULL,
  score INTEGER NOT NULL, codes TEXT NOT NULL, failures_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
  PRIMARY KEY (run_id, ad_id, attempt)
);
CREATE TABLE IF NOT EXISTS assets (
  run_id TEXT NOT NULL, ad_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, backend TEXT NOT NULL, payload_sha TEXT NOT NULL,
  status TEXT NOT NULL, PRIMARY KEY (run_id, path)
);
CREATE TABLE IF NOT EXISTS cost_estimates (
  run_id TEXT NOT NULL, ad_id TEXT NOT NULL, item TEXT NOT NULL, model TEXT NOT NULL,
  qty TEXT NOT NULL, unit TEXT NOT NULL, unit_usd TEXT NOT NULL, usd TEXT NOT NULL, prices_verified INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  run_id TEXT NOT NULL, source TEXT NOT NULL, dimension TEXT NOT NULL, key TEXT NOT NULL,
  posts INTEGER NOT NULL, views INTEGER NOT NULL, conversions INTEGER NOT NULL,
  posterior_per_1k REAL NOT NULL, p_best REAL NOT NULL, old_weight REAL NOT NULL, new_weight REAL NOT NULL,
  verdict TEXT NOT NULL
);
"""

# Tables (and their sort keys) that make up the reproducibility fingerprint.
FINGERPRINT_TABLES: dict[str, str] = {
    "runs": "run_id",
    "prompts": "id",
    "ads": "ad_id",
    "gate_results": "ad_id, attempt",
    "assets": "path",
    "cost_estimates": "ad_id, item",
}


class Ledger:
    def __init__(self, path: Path, *, fresh: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fresh and self.path.exists():
            self.path.unlink()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        if self.db is None:
            return
        self.db.commit()
        self.db.close()
        self.db = None  # type: ignore[assignment]

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- writes --------------------------------------------------------------------------
    def _insert(self, table: str, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))

    def add_run(self, **row: Any) -> None:
        self._insert("runs", row)

    def add_prompt(self, *, run_id: str, ad_id: str | None, stage: str, attempt: int, spec, completion) -> None:
        self._insert("prompts", {
            "run_id": run_id, "ad_id": ad_id, "stage": stage, "attempt": attempt,
            "template_id": spec.template_id, "template_sha": spec.template_sha,
            "provider": completion.provider, "model": completion.model, "seed": spec.seed,
            "prompt_sha": spec.prompt_sha, "prompt_text": spec.system + "\n\n" + spec.user,
            "response_text": completion.text, "response_sha": sha256_text(completion.text)[:16],
            "input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens,
        })

    def add_ad(self, **row: Any) -> None:
        self._insert("ads", row)

    def add_gate(self, *, run_id: str, ad_id: str, attempt: int, result) -> None:
        self._insert("gate_results", {
            "run_id": run_id, "ad_id": ad_id, "attempt": attempt, "passed": int(result.passed),
            "score": result.score, "codes": ",".join(result.codes),
            "failures_json": canonical_json(result.to_dict()["failures"]),
            "metrics_json": canonical_json(result.metrics),
        })

    def add_asset(self, *, run_id: str, ad_id: str, kind: str, path: Path, root: Path, backend: str, payload_sha: str, status: str) -> None:
        self._insert("assets", {
            "run_id": run_id, "ad_id": ad_id, "kind": kind, "path": Path(path).relative_to(root).as_posix(),
            "sha256": sha256_file(path), "bytes": Path(path).stat().st_size, "backend": backend,
            "payload_sha": payload_sha, "status": status,
        })

    def add_costs(self, run_id: str, est) -> None:
        for l in est.lines:
            self._insert("cost_estimates", {
                "run_id": run_id, "ad_id": l.ad_id, "item": l.item, "model": l.model, "qty": str(l.qty),
                "unit": l.unit, "unit_usd": str(l.unit_usd), "usd": str(l.usd), "prices_verified": int(est.prices_verified),
            })

    def add_feedback(self, rows: Iterable[dict[str, Any]]) -> None:
        for r in rows:
            self._insert("feedback", r)

    def commit(self) -> None:
        self.db.commit()

    # --- reads ---------------------------------------------------------------------------
    def rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def fingerprint(self) -> str:
        """SHA-256 over every reproducibility-relevant row, in a stable order."""
        parts = []
        for table, order in FINGERPRINT_TABLES.items():
            rows = self.rows(f"SELECT * FROM {table} ORDER BY {order}")
            for r in rows:
                r.pop("id", None)  # autoincrement ids follow insert order, already captured by sort
            parts.append({table: rows})
        return sha256_text(canonical_json(parts))

    def counts(self) -> dict[str, int]:
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in [*FINGERPRINT_TABLES, "feedback"]}

    def verify_assets(self, root: Path) -> list[str]:
        """Re-hash every asset on disk. Returns a list of problems (empty means all good)."""
        problems = []
        for r in self.rows("SELECT path, sha256, bytes FROM assets ORDER BY path"):
            p = Path(root) / r["path"]
            if not p.exists():
                problems.append(f"missing: {r['path']}")
            elif sha256_file(p) != r["sha256"]:
                problems.append(f"hash changed: {r['path']}")
        return problems
