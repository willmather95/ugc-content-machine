"""TregBackend: adapter for a pay-per-call tool catalog (search -> get price -> call).

Shape only. It is never called in this repo:
  * it is paid, so the pipeline must hand it an Authorization from cost.authorize(), which
    needs verified prices and --approve-spend equal to the estimate, and
  * its default transport refuses to touch the network. A real transport must be injected.

Every call is metered: the per-request price is charged against the approved amount BEFORE the
request goes out, so a run stops at the approved line instead of after it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..cost import Authorization, PriceBook, SpendNotApproved, money
from ..util import pretty_json, write_text
from .base import GenerationRequest, GenerationResult

CatalogTransport = Callable[[str, dict[str, Any]], dict[str, Any]]


class NetworkDisabled(RuntimeError):
    pass


def _no_network(method: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NetworkDisabled(
        f"TregBackend transport is not configured (tried {method}). This repo never calls a paid catalog. "
        "Inject a transport only after the owner approves the spend."
    )


class TregBackend:
    name = "treg"
    is_paid = True

    def __init__(self, prices: PriceBook, transport: CatalogTransport | None = None):
        self.prices = prices
        self.transport = transport or _no_network

    def request_cost(self, req: GenerationRequest) -> Decimal:
        unit = self.prices.unit(req.model_key)
        if req.kind == "video":
            return money(Decimal(req.params["duration_s"]) * unit)
        if req.kind == "image":
            return money(unit)
        if req.kind == "voice":
            return money(Decimal(len(req.params["text"])) / 1000 * unit)
        raise ValueError(req.kind)

    def catalog_body(self, req: GenerationRequest) -> dict[str, Any]:
        """The exact body for the catalog's `call` method."""
        return {
            "tool_id": req.tool_id,
            "params": req.params,
            "seed": req.seed,
            "max_cost_usd": str(self.request_cost(req)),
            "idempotency_key": req.payload_sha[:32],
        }

    def submit(self, req: GenerationRequest, out_dir: Path, auth: Authorization | None, ctx: dict[str, Any]) -> GenerationResult:
        if auth is None:
            raise SpendNotApproved("TregBackend is paid and got no spend authorization. Nothing was sent.")
        body = self.catalog_body(req)
        req_path = write_text(out_dir / "requests" / f"{req.kind}.json", pretty_json(body))
        auth.charge(self.request_cost(req), f"{req.ad_id} {req.kind}")
        reply = self.transport("call", body)
        return GenerationResult(req.ad_id, req.kind, "submitted", req_path, req.payload_sha, [], str(reply.get("job_id", "")))

