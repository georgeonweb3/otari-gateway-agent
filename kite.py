"""Kite Passport wiring — the second budget in "Two budgets, one agent".

Kite Agent Passport (docs.gokite.ai, fetched 2026-11-11) gives the agent
a verifiable identity (DID) and spending sessions the user approves with
a passkey. Flow, per the docs:

  1. ksearch services list          — discover paid services in the catalog
  2. kpass agent:session create     — request a session; returns an
     approval URL the user opens on a device where their passkey lives
  3. kpass session execute          — paid call, settled in USDC on Kite

Session caps used by this entry (the demo task's plan): $2 per
transaction, $10 total, 1 hour.

HARD CONSTRAINT (proven live by an existing entrant, Nov 5-11):
Passport only pays hosts in Kite's catalog. Their own merchants are
refused ``payment_target_forbidden``; the only Kite-run x402 gateway in
the dev catalog charges USDC on Kite mainnet, which dev Passport
refuses as ``unsupported_asset``. Real pieUSD testnet settlement works
via the x402 facilitator direct to a merchant — Passport-mediated
payment to own hosts does not. Consequences, enforced in code:

- The live path (KiteCli) only ever pays catalog services found via
  ksearch. It never constructs a payment to an own host.
- The simulator (KiteSimulator) is the offline stand-in: it executes
  the same cap checks and ledger writes, but every receipt is labelled
  ``simulated: true`` with ``tx_hash: null``. A simulated receipt is
  never presented as a settlement.
- Direct x402 settlement to our own testnet merchant (facilitator
  https://facilitator.pieverse.io/v2, pieUSD, Kite chain eip155:2368)
  is a phase-2 live step; the stub here refuses to run offline and
  says exactly what it needs.

The passkey approval is George's own device gesture and cannot be
delegated — session creation returns the approval URL and waits.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field

# Demo-task session caps (USD), from PLAN.md.
DEFAULT_MAX_PER_TX_USD = 2.0
DEFAULT_MAX_TOTAL_USD = 10.0
DEFAULT_TTL_HOURS = 1

KITE_CHAIN = "eip155:2368"  # Kite testnet chain id (pieUSD, 18 dp)
X402_FACILITATOR = "https://facilitator.pieverse.io/v2"


class KiteCapRefused(Exception):
    """A payment would breach the session's caps. ``reason`` is one of
    "per_tx", "total", or "not_active". ``record`` mirrors the refusal
    shape used by the inference budget so both budgets refuse alike."""

    def __init__(self, reason: str, detail: str, *, session: "KiteSession" = None):
        self.reason = reason
        self.record = {
            "detail": detail,
            "code": f"kite_session_{reason}_exceeded",
            "max_per_tx_usd": session.max_per_tx_usd if session else None,
            "max_total_usd": session.max_total_usd if session else None,
            "spent_usd": session.spent_usd if session else None,
        }
        super().__init__(json.dumps(self.record))


class KiteCliMissing(RuntimeError):
    """The kpass/ksearch CLI is not installed on this machine."""


@dataclass
class KiteSession:
    """One user-approved spending session (or its offline draft).

    ``status``: draft -> pending_approval -> active -> exhausted/expired.
    A session only pays while active; ``authorize`` enforces both caps
    before any payment is attempted.
    """

    task_summary: str
    max_per_tx_usd: float = DEFAULT_MAX_PER_TX_USD
    max_total_usd: float = DEFAULT_MAX_TOTAL_USD
    ttl_hours: int = DEFAULT_TTL_HOURS
    session_id: str | None = None
    request_id: str | None = None
    approval_url: str | None = None
    status: str = "draft"
    spent_usd: float = 0.0
    created_at: float = field(default_factory=time.time)

    def authorize(self, amount_usd: float) -> None:
        """Check a payment against the caps. Raises KiteCapRefused."""
        if self.status != "active":
            raise KiteCapRefused(
                "not_active",
                f"Kite session is {self.status}, not active — no payment "
                "may be attempted.",
                session=self,
            )
        if amount_usd > self.max_per_tx_usd:
            raise KiteCapRefused(
                "per_tx",
                f"Payment ${amount_usd:.2f} exceeds the session's "
                f"${self.max_per_tx_usd:.2f} per-transaction cap.",
                session=self,
            )
        if self.spent_usd + amount_usd > self.max_total_usd:
            raise KiteCapRefused(
                "total",
                f"Payment ${amount_usd:.2f} would take session spend to "
                f"${self.spent_usd + amount_usd:.2f}, past the "
                f"${self.max_total_usd:.2f} total cap.",
                session=self,
            )

    def record_payment(self, amount_usd: float) -> None:
        self.spent_usd = round(self.spent_usd + amount_usd, 2)
        if self.spent_usd >= self.max_total_usd:
            self.status = "exhausted"

    def to_dict(self) -> dict:
        return {
            "task_summary": self.task_summary,
            "max_per_tx_usd": self.max_per_tx_usd,
            "max_total_usd": self.max_total_usd,
            "ttl_hours": self.ttl_hours,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "approval_url": self.approval_url,
            "status": self.status,
            "spent_usd": self.spent_usd,
        }


# ---------------------------------------------------------------------------
# Hash-chained spend ledger (append-only).
# ---------------------------------------------------------------------------

@dataclass
class LedgerEntry:
    seq: int
    ts: float
    kind: str  # "inference" | "payment" | "refusal" | "note"
    detail: str
    amount_usd: float | None
    prev_hash: str
    hash: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "detail": self.detail,
            "amount_usd": self.amount_usd,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "extra": self.extra,
        }


def _entry_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


class SpendLedger:
    """Append-only, hash-chained record of every spend decision.

    Each entry's hash covers the previous entry's hash plus this entry's
    canonical JSON, so any edit to an earlier entry breaks every later
    hash. ``verify()`` recomputes the whole chain.
    """

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    def append(
        self, kind: str, detail: str, amount_usd: float | None = None,
        **extra,
    ) -> LedgerEntry:
        prev = self.entries[-1].hash if self.entries else self.GENESIS
        body = {
            "seq": len(self.entries) + 1,
            "ts": round(time.time(), 3),
            "kind": kind,
            "detail": detail,
            "amount_usd": amount_usd,
            "extra": extra,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        entry = LedgerEntry(
            seq=body["seq"], ts=body["ts"], kind=kind, detail=detail,
            amount_usd=amount_usd, prev_hash=prev,
            hash=_entry_hash(prev, canonical), extra=extra,
        )
        self.entries.append(entry)
        return entry

    def verify(self) -> bool:
        """Recompute every hash; False on the first mismatch."""
        prev = self.GENESIS
        for entry in self.entries:
            body = {
                "seq": entry.seq, "ts": entry.ts, "kind": entry.kind,
                "detail": entry.detail, "amount_usd": entry.amount_usd,
                "extra": entry.extra,
            }
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
            if entry.prev_hash != prev:
                return False
            if entry.hash != _entry_hash(prev, canonical):
                return False
            prev = entry.hash
        return True

    def to_json(self) -> str:
        return json.dumps(
            [e.to_dict() for e in self.entries], indent=2, sort_keys=True
        )

    @classmethod
    def from_json(cls, text: str) -> "SpendLedger":
        ledger = cls()
        for raw in json.loads(text):
            ledger.entries.append(LedgerEntry(**raw))
        return ledger


# ---------------------------------------------------------------------------
# Simulator — offline stand-in. Every receipt is labelled simulated.
# ---------------------------------------------------------------------------

SIMULATED_CATALOG = [
    {
        "service_id": "sim-data-bundle-1gb",
        "name": "Simulated 1GB data bundle",
        "price_usd": 0.45,
        "currency": "pieUSD (simulated)",
        "host": "simulated-catalog-host",
    },
    {
        "service_id": "sim-weather-api",
        "name": "Simulated weather API call",
        "price_usd": 0.02,
        "currency": "pieUSD (simulated)",
        "host": "simulated-catalog-host",
    },
]


class KiteSimulator:
    """Offline Kite stand-in: same cap checks and ledger writes as the
    live path, but no chain, no wallet, no settlement. Receipts are
    explicitly simulated (``tx_hash`` is always None) and must never be
    presented as a real settlement."""

    def __init__(self, ledger: SpendLedger):
        self.ledger = ledger

    def search_catalog(self, query: str) -> list[dict]:
        query = query.lower()
        return [
            s for s in SIMULATED_CATALOG
            if query in s["name"].lower() or query in s["service_id"]
        ] or list(SIMULATED_CATALOG)

    def execute_payment(
        self, session: KiteSession, service: dict, amount_usd: float
    ) -> dict:
        """Authorize against the session caps, then record a SIMULATED
        payment in the ledger. Returns the receipt (simulated)."""
        session.authorize(amount_usd)
        session.record_payment(amount_usd)
        entry = self.ledger.append(
            "payment",
            f"SIMULATED payment ${amount_usd:.2f} to "
            f"{service['service_id']} ({service['name']})",
            amount_usd=amount_usd,
            simulated=True,
            service_id=service["service_id"],
            tx_hash=None,
            session_id=session.session_id,
        )
        return {
            "simulated": True,
            "tx_hash": None,
            "amount_usd": amount_usd,
            "service_id": service["service_id"],
            "session_id": session.session_id,
            "ledger_seq": entry.seq,
            "ledger_hash": entry.hash,
        }


def direct_x402_settlement(*args, **kwargs):
    """Real pieUSD testnet settlement to our own merchant via the x402
    facilitator. OFFLINE STUB: refuses to run until phase 2, when a
    funded testnet wallet and the facilitator flow are wired live.
    Never returns a fake tx hash."""
    raise NotImplementedError(
        "direct x402 settlement is a phase-2 live step: it needs a "
        "funded Kite testnet (pieUSD) wallet and the facilitator at "
        f"{X402_FACILITATOR}. It is not simulated and must not be faked."
    )


# ---------------------------------------------------------------------------
# Live CLI wrapper — shells out to kpass/ksearch when installed.
# ---------------------------------------------------------------------------

class KiteCli:
    """Thin wrapper over the kpass/ksearch CLIs (docs.gokite.ai).

    Every method raises KiteCliMissing when the CLI is absent. No method
    is called in phase 1 (offline); the phase-2 live run uses these
    after `kpass` is installed and George has approved a session with
    his passkey.
    """

    def __init__(self, kpass: str = "kpass", ksearch: str = "ksearch"):
        if shutil.which(kpass) is None:
            raise KiteCliMissing(
                f"{kpass!r} CLI not found. Install it first: "
                "curl -fsSL https://cli.gokite.ai/install.sh | bash"
            )
        if shutil.which(ksearch) is None:
            raise KiteCliMissing(
                f"{ksearch!r} CLI not found (ships with the kpass install)."
            )
        self.kpass = kpass
        self.ksearch = ksearch

    def _run_json(self, argv: list[str]) -> dict:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=120, check=False
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{' '.join(argv)} failed (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    def search_services(self, query: str, limit: int = 10) -> list[dict]:
        """ksearch services list — catalog services only, never own hosts."""
        out = self._run_json([
            self.ksearch, "services", "list", "--query", query,
            "--limit", str(limit), "--output", "json",
        ])
        if isinstance(out, dict):
            return out.get("services") or out.get("data") or []
        return out

    def session_create(self, session: KiteSession) -> KiteSession:
        """kpass agent:session create. Returns the session with the
        approval URL; the user approves it on a passkey device."""
        out = self._run_json([
            self.kpass, "agent:session", "create",
            "--task-summary", session.task_summary,
            "--max-amount-per-tx", str(session.max_per_tx_usd),
            "--max-total-amount", str(session.max_total_usd),
            "--ttl", f"{session.ttl_hours}h",
            "--payment-approach", "x402_http",
            "--output", "json",
        ])
        session.request_id = out.get("request_id") or out.get("requestId")
        session.session_id = out.get("session_id") or out.get("sessionId")
        session.approval_url = out.get("approval_url") or out.get("approvalUrl")
        session.status = "pending_approval"
        return session

    def session_status(self, request_id: str, wait: bool = False) -> dict:
        argv = [
            self.kpass, "agent:session", "status",
            "--request-id", request_id, "--output", "json",
        ]
        if wait:
            argv.insert(5, "--wait")
        return self._run_json(argv)

    def session_execute(
        self, session_id: str, service_id: str, payload: dict | None = None
    ) -> dict:
        """kpass session execute — paid call to a CATALOG service only."""
        argv = [
            self.kpass, "session", "execute",
            "--session-id", session_id,
            "--service-id", service_id,
            "--output", "json",
        ]
        if payload:
            argv += ["--payload", json.dumps(payload)]
        return self._run_json(argv)
