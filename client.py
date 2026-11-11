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
    "researcher": os.environ.get("OTARI_MODEL_RESEARCHER", "deepseek/deepseek-v4-flash"),
    # Third model, different family: critiques the draft before it ships.
    "critic": os.environ.get("OTARI_MODEL_CRITIC", "z-ai/glm-flash"),
}

# Gateway-run tools (docs/tools.md). They are declared in-request and executed
# by Otari, not by this app — and only work if the workspace has them switched
# on. Check live availability before a run:
#   GET https://api.otari.ai/api/v1/tools   (Authorization: Bearer $OTARI_API_KEY)
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

    `raw` is the gateway's error string, unedited — in --budget-demo mode it
    is printed verbatim as the proof that the gateway, not this app, is the
    spend guard.
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


def get_client() -> OpenAI:
    """Build the OpenAI SDK client pointed at the Otari gateway."""
    return OpenAI(base_url=BASE_URL, api_key=require_api_key())


def chat(
    role: str,
    messages: list[dict],
    *,
    tools: bool = False,
    client: OpenAI | None = None,
) -> CallResult:
    """One chat completion through Otari, in the given role's model.

    Prints one metadata line per call (role, model, latency, response id,
    token usage) so the run can be cross-checked against Otari's Requests
    dashboard and GET /api/v1/usage/requests/{id}.

    Raises BudgetRefused (carrying the gateway's error verbatim) when Otari
    refuses the call on budget/limits; any other API error propagates as-is.
    """
    if role not in MODELS:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(MODELS)}")
    client = client or get_client()
    kwargs: dict = {"model": MODELS[role], "messages": messages}
    if tools and GATEWAY_TOOLS_ENABLED:
        kwargs["tools"] = GATEWAY_TOOLS

    started = time.monotonic()
    try:
        resp = client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        if exc.status_code in BUDGET_STATUS_CODES:
            raise BudgetRefused(exc.status_code, str(exc)) from exc
        raise
    latency = time.monotonic() - started

    message = resp.choices[0].message
    usage = None
    if getattr(resp, "usage", None) is not None:
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }
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
    print(meta, flush=True)
    if tool_calls:
        print(f"[otari] gateway returned tool_calls: {json.dumps(tool_calls)}", flush=True)

    return CallResult(
        role=role,
        model=MODELS[role],
        content=message.content or "",
        latency_s=latency,
        response_id=resp.id,
        usage=usage,
        tool_calls=tool_calls,
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
