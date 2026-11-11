"""Otari gateway client — the ONLY module in this project that talks to a model.

Every LLM call in SpendGuard CLI routes through Mozilla's Otari gateway
(OpenAI-compatible API, docs at github.com/mozilla-ai/otari). No provider is
ever called directly: Otari picks the provider, enforces budgets *before*
dispatch, and logs every request.

There is no mocked or fallback output anywhere in this project. A call either
returns the gateway's real response or raises the gateway's real error.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from openai import APIStatusError, OpenAI

# ---------------------------------------------------------------------------
# Config — everything tunable lives in this one block.
#
# Model ids are copied from the Otari Models page ("Use this model" panel).
# They carry dated suffixes and churn, so if one stops resolving, fix it here
# (or set the matching OTARI_MODEL_* env var) — nowhere else.
# ---------------------------------------------------------------------------

# OpenAI-SDK base URL for a US otari.ai account. The "/api" segment is NOT
# optional: https://api.otari.ai/v1/... never reaches the gateway (404/403).
# EU account? Use "https://eu.api.otari.ai/api/v1" — an Otari key belongs to
# one region; sent to the other region's host it answers 421.
BASE_URL = os.environ.get("OTARI_BASE_URL", "https://api.otari.ai/api/v1")

MODELS = {
    # Cheap, fast open-weight model: decomposes the question into a plan.
    "planner": os.environ.get("OTARI_MODEL_PLANNER", "openai/gpt-oss-20b"),
    # Main reasoner: runs the gateway tools and drafts the answer.
    # (deepseek-v4.1-flash-1017 verified live in GET /api/v1/models, 2026-11-11.)
    "researcher": os.environ.get(
        "OTARI_MODEL_RESEARCHER", "deepseek/deepseek-v4.1-flash-1017"
    ),
    # Third model, different family: critiques the draft before it ships.
    # (glm-5.3-flash verified live in GET /api/v1/models, 2026-11-11; the old
    # "z-ai/glm-flash" default does not exist in the hosted catalog.)
    "critic": os.environ.get("OTARI_MODEL_CRITIC", "z-ai/glm-5.3-flash"),
}

# Gateway-run tools (docs/tools.md). They are declared in-request and executed
# by Otari, not by this app — and only work if the workspace has them switched
# on. Before any tools-declaring call, chat() runs a preflight against
# GET https://api.otari.ai/api/v1/tools and declares only the tools it
# reports available (declaring a disabled one gets the whole call refused).
# Set OTARI_GATEWAY_TOOLS=0 to run the agent without them.
GATEWAY_TOOLS_ENABLED = os.environ.get("OTARI_GATEWAY_TOOLS", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
GATEWAY_TOOLS = [
    {"type": "otari_web_search"},
    {"type": "otari_web_fetch"},
    {"type": "otari_code_execution"},
]

# Status codes the gateway uses when it refuses a call before dispatch
# (budget exhausted / limit hit). The exact code gets confirmed on the first
# live run; the refusal body is carried verbatim either way.
BUDGET_STATUS_CODES = (402, 403, 429)


class MissingApiKey(RuntimeError):
    """OTARI_API_KEY is not set. The message says exactly what to do."""


class BudgetRefused(Exception):
    """Otari refused a call before dispatch (budget / spending limit).

    `raw` is the gateway's response body, verbatim — the JSON detail (e.g.
    {"detail": "API key has exceeded budget limit", "code":
    "budget_exceeded"}), not the SDK's one-line summary of it. In
    --budget-demo mode it is printed as-is: the proof that the gateway, not
    this app, is the spend guard.
    """

    def __init__(self, status_code: int, raw: str):
        super().__init__(raw)
        self.status_code = status_code
        self.raw = raw


@dataclass
class CallResult:
    role: str
    model: str
    content: str
    latency_s: float
    response_id: str | None = None
    usage: dict | None = field(default=None)
    tool_calls: list | None = field(default=None)
    # Gateway-reported figures from the raw response, when present:
    # usage.cost_usd (a string in the JSON) and the otari_tool_calls array
    # describing gateway-run tool executions (status + sources for search).
    cost_usd: float | None = None
    otari_tool_calls: list | None = field(default=None)


def require_api_key() -> str:
    """Return the Otari API key or raise MissingApiKey with exact fix steps."""
    key = os.environ.get("OTARI_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "OTARI_API_KEY is not set.\n"
            "Create a key in the Otari dashboard (otari.ai -> your workspace -> "
            "API keys -> Create key; the token starts with otk_ and is shown "
            "only once), then run:\n"
            "  export OTARI_API_KEY=otk_..."
        )
    return key


def get_client(api_key: str | None = None) -> OpenAI:
    """Build the OpenAI SDK client pointed at the Otari gateway.

    Defaults to OTARI_API_KEY; an explicit key can be passed for the rare
    stage that must run under a different key of the same workspace (the
    value is never printed or logged).
    """
    return OpenAI(base_url=BASE_URL, api_key=api_key or require_api_key())


# ---------------------------------------------------------------------------
# Tools preflight.
#
# Declaring a gateway tool the workspace has switched off gets the WHOLE
# call refused (observed live: a bare 403 with an empty body), so before
# any run that would declare tools we ask the gateway what it actually
# has on — GET /api/v1/tools — and declare only the available ones.
# The result is fetched once per process and cached.
# ---------------------------------------------------------------------------

_preflight_cache: list[dict] | None = None


def fetch_tools() -> list[dict]:
    """GET /api/v1/tools with this key; returns the raw tool entries."""
    url = BASE_URL.rstrip("/") + "/tools"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {require_api_key()}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, dict):
        return payload.get("data") or payload.get("tools") or []
    return payload


def filter_available_tools(wanted: list[dict]) -> list[dict]:
    """Reduce `wanted` tool declarations to the ones the gateway reports
    available, logging one preflight line per tool. Cached per process.

    If the preflight itself fails, declare nothing and say so: a run
    without tools beats a run refused outright for declaring one.
    """
    global _preflight_cache
    if _preflight_cache is None:
        try:
            entries = fetch_tools()
        except Exception as exc:  # preflight unreachable: degrade, loudly
            print(
                f"[otari] preflight: GET /api/v1/tools failed ({exc}); "
                "declaring no gateway tools this run",
                flush=True,
            )
            _preflight_cache = []
            return []
        by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
        available: list[dict] = []
        for tool in GATEWAY_TOOLS:
            tool_type = tool["type"]
            entry = by_id.get(tool_type)
            if entry is not None and entry.get("available"):
                print(f"[otari] preflight: {tool_type} available", flush=True)
                available.append(tool)
            else:
                reason = entry.get("reason") if entry else "not listed"
                print(
                    f"[otari] preflight: {tool_type} unavailable ({reason})",
                    flush=True,
                )
        _preflight_cache = available
    wanted_types = {tool["type"] for tool in wanted}
    return [tool for tool in _preflight_cache if tool["type"] in wanted_types]


def chat(
    role: str,
    messages: list[dict],
    *,
    tools: bool = False,
    tools_override: list[dict] | None = None,
    client: OpenAI | None = None,
) -> CallResult:
    """One chat completion through Otari, in the given role's model.

    Prints one metadata line per call (role, model, latency, response id,
    token usage, gateway-reported cost when present) so the run can be
    cross-checked against Otari's Requests dashboard.

    With tools=True (or a tools_override list), the declarations first go
    through the tools preflight: only tools GET /api/v1/tools reports
    available are declared, because declaring a disabled one gets the
    whole call refused.

    Raises BudgetRefused (carrying the gateway's error verbatim) when Otari
    refuses the call on budget/limits; any other API error propagates as-is.
    """
    if role not in MODELS:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(MODELS)}")
    client = client or get_client()
    kwargs: dict = {"model": MODELS[role], "messages": messages}
    if GATEWAY_TOOLS_ENABLED and (tools or tools_override is not None):
        declared = filter_available_tools(tools_override or GATEWAY_TOOLS)
        if declared:
            kwargs["tools"] = declared

    started = time.monotonic()
    try:
        resp = client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        if exc.status_code in BUDGET_STATUS_CODES:
            # Carry the gateway's response body itself (the JSON detail),
            # not str(exc) — the SDK's summary is just "Error code: 403"
            # and throws away the only part worth showing.
            body = ""
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    body = response.text or ""
                except Exception:
                    body = ""
            if not body:
                fallback = getattr(exc, "body", None)
                if isinstance(fallback, str):
                    body = fallback
                elif fallback is not None:
                    body = json.dumps(fallback)
                else:
                    body = str(exc)
            raise BudgetRefused(exc.status_code, body) from exc
        raise
    latency = time.monotonic() - started

    message = resp.choices[0].message
    usage = None
    cost_usd = None
    otari_tool_calls = None
    if getattr(resp, "usage", None) is not None:
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }
        # The gateway also reports the call's cost in the raw usage object
        # (usage.cost_usd) — the typed SDK fields above don't surface it,
        # so read it from the dumped response.
        raw_cost = resp.usage.model_dump().get("cost_usd")
        if raw_cost is not None:
            try:
                cost_usd = float(raw_cost)
            except (TypeError, ValueError):
                cost_usd = None
    raw_dump = resp.model_dump()
    if isinstance(raw_dump.get("otari_tool_calls"), list):
        otari_tool_calls = raw_dump["otari_tool_calls"]
    tool_calls = None
    if getattr(message, "tool_calls", None):
        tool_calls = [tc.model_dump() for tc in message.tool_calls]

    meta = (
        f"[otari] role={role} model={MODELS[role]} "
        f"latency={latency:.2f}s id={resp.id}"
    )
    if usage:
        meta += (
            f" tokens={usage['total_tokens']}"
            f" (in {usage['prompt_tokens']} / out {usage['completion_tokens']})"
        )
    if cost_usd is not None:
        meta += f" cost=${cost_usd:.6f}"
    print(meta, flush=True)
    if tool_calls:
        print(f"[otari] gateway returned tool_calls: {json.dumps(tool_calls)}", flush=True)
    if otari_tool_calls:
        summary = ", ".join(
            f"{tc.get('type')}={tc.get('status')}" for tc in otari_tool_calls
        )
        print(f"[otari] gateway tool executions: {summary}", flush=True)

    return CallResult(
        role=role,
        model=MODELS[role],
        content=message.content or "",
        latency_s=latency,
        response_id=resp.id,
        usage=usage,
        tool_calls=tool_calls,
        cost_usd=cost_usd,
        otari_tool_calls=otari_tool_calls,
    )


def fetch_key_usage() -> dict:
    """Read this key's own budget + usage back from GET /api/v1/key.

    Uses only the stdlib so the spend readback works even where the OpenAI
    SDK is not installed. Raises on any HTTP error — the caller reports it
    honestly rather than inventing a spend figure.
    """
    url = BASE_URL.rstrip("/") + "/key"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {require_api_key()}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))
