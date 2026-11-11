"""Inference budget — the client-side enforcement of the agent's own
thinking spend.

SERV Reasoning is itself metered: every plan, screen and explanation is a
paid call. This module makes the agent's inference spend a first-class
enforced budget, alongside the Kite payment session. It is deliberately
shaped like the Otari gateway's own refusal so the two budgets read the
same way in the ledger and in the demo:

  {"detail": "Inference budget exceeded: ...", "code":
  "inference_budget_exceeded", ...}

Rules:
- Spend is tracked ONLY from the gateway's own cost readback per call
  (``cost_usd`` in the raw response usage object, when SERV reports it).
  No cost is ever invented. If a call reports no cost, it is recorded
  with cost=None and the budget says so.
- Before every planned call the budget projects the next call's cost as
  the largest single-call cost observed so far in this run (or, when a
  verified price table is loaded, from that table). If spent + projected
  exceeds the limit, the call is refused BEFORE dispatch: no request is
  sent, and the refusal record is the proof.
- The first call of a run has nothing to project from, so it is allowed;
  its real cost then seeds the projection for the rest of the run.

Phase-1 note (offline): no live SERV call has been made yet, so whether
``cost_usd`` appears in SERV's raw responses is UNVERIFIED. The phase-2
live smoke test must confirm it; if SERV does not report per-call cost,
fill ``serv_prices.json`` from the SERV console Models page and load it
with ``InferenceBudget.load_price_table`` — the table path is tested
with a synthetic table here, never with invented live prices.
"""

from __future__ import annotations

import json
import os

DEFAULT_INFERENCE_BUDGET_USD = 0.05


class InferenceBudgetRefused(Exception):
    """This run's inference budget would be exceeded by the next call.

    ``record`` is the refusal, verbatim-style, mirroring the Otari
    gateway's ``{"detail": ..., "code": ...}`` shape so the demo can show
    both budgets refusing in the same format. Nothing was dispatched.
    """

    def __init__(
        self,
        *,
        limit_usd: float,
        spent_usd: float,
        estimated_next_usd: float | None,
        calls_made: int,
    ):
        estimate_part = (
            f"next call estimated ${estimated_next_usd:.6f}"
            if estimated_next_usd is not None
            else "next call cost unknown (no cost observed yet)"
        )
        detail = (
            "Inference budget exceeded: this call would take the run past "
            f"its ${limit_usd:.2f} inference budget "
            f"(spent ${spent_usd:.6f}, {estimate_part})"
        )
        self.record = {
            "detail": detail,
            "code": "inference_budget_exceeded",
            "limit_usd": limit_usd,
            "spent_usd": round(spent_usd, 6),
            "estimated_next_usd": (
                round(estimated_next_usd, 6)
                if estimated_next_usd is not None
                else None
            ),
            "calls_made": calls_made,
        }
        super().__init__(json.dumps(self.record))
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd
        self.estimated_next_usd = estimated_next_usd
        self.calls_made = calls_made


class InferenceBudget:
    """Tracks one run's SERV inference spend against a hard limit."""

    def __init__(self, limit_usd: float = DEFAULT_INFERENCE_BUDGET_USD):
        if limit_usd <= 0:
            raise ValueError("inference budget must be > 0")
        self.limit_usd = limit_usd
        self.spent_usd = 0.0
        self.calls_made = 0
        self.calls_without_cost = 0
        self._max_observed_call_cost: float | None = None
        self._price_table: dict | None = None

    # -- price table (phase 2, verified console prices only) --------------

    def load_price_table(self, path: str) -> None:
        """Load per-model USD-per-1M-token prices from a JSON file.

        Format: {"<model>": {"input_per_1m": float, "output_per_1m":
        float}}. The file must come from the SERV console Models page
        (phase 2); never hand-write prices. Raises if the file is
        missing or malformed.
        """
        with open(path, encoding="utf-8") as fh:
            table = json.load(fh)
        if not isinstance(table, dict):
            raise ValueError("price table must be a JSON object")
        for model, rates in table.items():
            if not isinstance(rates, dict) or "input_per_1m" not in rates \
                    or "output_per_1m" not in rates:
                raise ValueError(f"price table entry for {model!r} is incomplete")
        self._price_table = table

    def estimate_for_tokens(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float | None:
        """Cost estimate from a loaded price table, else None."""
        if self._price_table is None or model not in self._price_table:
            return None
        rates = self._price_table[model]
        return (
            prompt_tokens * rates["input_per_1m"]
            + completion_tokens * rates["output_per_1m"]
        ) / 1_000_000

    # -- projection + enforcement ------------------------------------------

    def estimate_next_call(self) -> float | None:
        """Projected cost of the next call: largest single-call cost
        observed so far this run, or None if nothing is observed yet."""
        return self._max_observed_call_cost

    def check(self, estimated_next_usd: float | None = None) -> None:
        """Refuse before dispatch if the next call would breach the limit.

        Raises InferenceBudgetRefused; returns None when the call may go
        ahead. An unknown estimate (no cost observed yet) never refuses —
        there is nothing to project from.
        """
        estimate = (
            estimated_next_usd
            if estimated_next_usd is not None
            else self.estimate_next_call()
        )
        if estimate is None:
            return
        if self.spent_usd + estimate > self.limit_usd:
            raise InferenceBudgetRefused(
                limit_usd=self.limit_usd,
                spent_usd=self.spent_usd,
                estimated_next_usd=estimate,
                calls_made=self.calls_made,
            )

    def record(self, cost_usd: float | None) -> None:
        """Record one completed call's gateway-reported cost.

        ``cost_usd=None`` means the gateway did not report a cost for
        this call: it is counted, not invented.
        """
        self.calls_made += 1
        if cost_usd is None:
            self.calls_without_cost += 1
            return
        self.spent_usd += cost_usd
        if (
            self._max_observed_call_cost is None
            or cost_usd > self._max_observed_call_cost
        ):
            self._max_observed_call_cost = cost_usd

    def summary(self) -> dict:
        return {
            "limit_usd": self.limit_usd,
            "spent_usd": round(self.spent_usd, 6),
            "calls_made": self.calls_made,
            "calls_without_cost": self.calls_without_cost,
            "max_single_call_usd": self._max_observed_call_cost,
        }


def budget_from_env() -> InferenceBudget:
    """Build the budget from SERV_INFERENCE_BUDGET_USD (default $0.05)."""
    raw = os.environ.get("SERV_INFERENCE_BUDGET_USD", "")
    limit = float(raw) if raw.strip() else DEFAULT_INFERENCE_BUDGET_USD
    return InferenceBudget(limit)
