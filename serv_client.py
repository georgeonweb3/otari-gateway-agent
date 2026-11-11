"""SERV Reasoning client — the ONLY module in this project that talks to
OpenServ.

SERV Reasoning is OpenAI-SDK compatible: point the standard SDK at
``https://inference-api.openserv.ai/v1`` with a SERV API key and the
calls route through SERV. Every call returns ``x-openserv-request-id``,
traceable in console Usage > Activity.

SERV Tools (docs.openserv.ai/serv-reasoning/tools, fetched 2026-11-11):
features are turned on by declaring a specially-named tool in the
request's ``tools`` array. SERV detects any tool whose name begins with
``serv_``, applies the feature server-side, and strips the tool before
the request reaches the model — so these tools never appear as tool
calls in the response. Declaring is enough; no description or
parameters are required for ``serv_prompt_guard``. ``serv_shadow_agent``
takes ``hint`` / ``max_iterations`` as schema *defaults*.

  - serv_prompt_guard: opt-in protection against prompt-injection
    attempts that leak or override the system prompt.
  - serv_shadow_agent: validate-and-iterate loop over the output;
    defaults max_iterations=3 (1..10). A hint adds task-specific
    validation criteria.

VERIFICATION STATUS (phase 2, LIVE 2026-11-11): the function-tool
declarations ARE accepted by the gateway (smoke test: 200, both
serv_prompt_guard and serv_shadow_agent declared; x-openserv-request-id
present on every response). The bare {"type": "serv_prompt_guard"} shape
is REJECTED (400 invalid_request_error — accepted types are "function"
and "custom"). SERV requires a system prompt on every call (400 without
one). SERV does NOT report cost_usd in usage — all spend is priced from
the verified models catalog via serv_prices.json and labelled estimated.
serv_prompt_guard fires live: it returned "I can't share that." with 0
tokens on the injected SwiftNet listing (screener) and on a researcher
call whose prompt embedded the tainted listing text — the pipeline now
withholds UNSAFE descriptions from synthesis (see serv_agent.py).
There is no mocked or fallback output anywhere in this project. A call
either returns SERV's real response or raises SERV's real error.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from openai import APIStatusError, OpenAI

# ---------------------------------------------------------------------------
# Config — everything tunable lives in this one block.
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get(
    "SERV_BASE_URL", "https://inference-api.openserv.ai/v1"
)

MODELS = {
    # Planner + critic: claude-sonnet-5 — the closest verified model to
    # the plan's claude-sonnet-5-serv-kronos, which is NOT in the live
    # catalog (verified 2026-11-11 via GET /models: 38 models, no
    # kronos). Same family/generation, reasoning enabled. Honest
    # substitution; the plan's Kronos references are superseded.
    "planner": os.environ.get(
        "SERV_MODEL_PLANNER", "claude-sonnet-5"
    ),
    "researcher": os.environ.get(
        "SERV_MODEL_RESEARCHER", "claude-sonnet-5"
    ),
    # Screening/explanations: cheap Haiku, same split the existing
    # entrant uses.
    "screener": os.environ.get("SERV_MODEL_SCREENER", "claude-haiku-4.5"),
    "critic": os.environ.get(
        "SERV_MODEL_CRITIC", "claude-sonnet-5"
    ),
}

# SERV Tools, OpenAI-SDK function-tool shape. Per the docs, declaring by
# name is sufficient; shadow-agent options are schema defaults, not
# call-time arguments.
SERV_TOOLS = {
    "serv_prompt_guard": {
        "type": "function",
        "function": {
            "name": "serv_prompt_guard",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "serv_shadow_agent": {
        "type": "function",
        "function": {
            "name": "serv_shadow_agent",
            "parameters": {
                "type": "object",
                "properties": {
                    "hint": {
                        "type": "string",
                        "default": (
                            "The answer must be a meaningful, valid "
                            "response to the request."
                        ),
                    },
                    "max_iterations": {"type": "integer", "default": 3},
                },
            },
        },
    },
}

# Status codes SERV uses when it refuses a call before dispatch
# (guard refusal / budget / limit). The exact mapping gets confirmed on
# the first live run; the refusal body is carried verbatim either way.
BUDGET_STATUS_CODES = (402, 403, 429)


class MissingApiKey(RuntimeError):
    """SERV_API_KEY is not set. The message says exactly what to do."""


class BudgetRefused(Exception):
    """SERV refused a call before dispatch (guard / budget / limit).

    ``raw`` is the gateway's response body, verbatim — not the SDK's
    one-line summary of it. In the demo this is printed as-is: the proof
    that the guard, not this app, is the spend guard.
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
    # SERV-reported figures from the raw response, when present:
    # usage.cost_usd and the x-openserv-request-id header (traceable in
    # console Usage > Activity). VERIFIED 2026-11-11: SERV does NOT
    # report cost_usd (smoke test: usage carries only token counts), so
    # cost_usd is usually a price-table ESTIMATE — see cost_estimated.
    # Never presented as gateway-reported when it is estimated.
    cost_usd: float | None = None
    request_id: str | None = None
    # True when cost_usd came from the loaded price table (catalog
    # pricing x returned token counts), not from the gateway.
    cost_estimated: bool = False


def require_api_key() -> str:
    """Return the SERV API key or raise MissingApiKey with exact fix steps."""
    key = os.environ.get("SERV_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "SERV_API_KEY is not set.\n"
            "Create a key in the OpenServ console (console.openserv.ai -> "
            "API Keys -> Create key), then run:\n"
            "  export SERV_API_KEY=..."
        )
    return key


def get_client(api_key: str | None = None) -> OpenAI:
    """Build the OpenAI SDK client pointed at SERV Reasoning.

    Defaults to SERV_API_KEY; an explicit key can be passed for the rare
    stage that must run under a different key (the value is never
    printed or logged).
    """
    return OpenAI(base_url=BASE_URL, api_key=api_key or require_api_key())


def serv_tools(
    *, prompt_guard: bool = True, shadow_agent: bool = False,
    shadow_hint: str | None = None, shadow_max_iterations: int | None = None,
) -> list[dict]:
    """Build the SERV Tools declarations for one request.

    ``serv_prompt_guard`` is on by default for every call in this
    project (the agent handles untrusted merchant listings). The shadow
    agent is opt-in per call: it trades extra inference for validated
    output, so it is declared only where a wrong answer costs more than
    the validation calls — planner and critic.
    """
    import copy

    tools: list[dict] = []
    if prompt_guard:
        tools.append(copy.deepcopy(SERV_TOOLS["serv_prompt_guard"]))
    if shadow_agent:
        tool = copy.deepcopy(SERV_TOOLS["serv_shadow_agent"])
        props = tool["function"]["parameters"]["properties"]
        if shadow_hint is not None:
            props["hint"]["default"] = shadow_hint
        if shadow_max_iterations is not None:
            if not 1 <= shadow_max_iterations <= 10:
                raise ValueError("serv_shadow_agent max_iterations must be 1..10")
            props["max_iterations"]["default"] = shadow_max_iterations
        tools.append(tool)
    return tools


def chat(
    role: str,
    messages: list[dict],
    *,
    budget=None,
    shadow_agent: bool = False,
    shadow_hint: str | None = None,
    shadow_max_iterations: int | None = None,
    client: OpenAI | None = None,
) -> CallResult:
    """One chat completion through SERV Reasoning, in the given role's model.

    ``budget`` is an InferenceBudget (budget.py): the call is refused
    locally BEFORE dispatch if it would breach the run's inference
    budget, and its gateway-reported cost is recorded after. Prints one
    metadata line per call (role, model, latency, SERV request id, token
    usage, gateway-reported cost when present) so the run can be
    cross-checked against console Usage > Activity.

    Every call declares serv_prompt_guard; planner/critic calls also
    declare serv_shadow_agent (see serv_tools()).

    Raises InferenceBudgetRefused (carrying the refusal record) when the
    run's own budget blocks the call, or BudgetRefused (carrying SERV's
    error verbatim) when SERV refuses it; any other API error
    propagates as-is.
    """
    if role not in MODELS:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(MODELS)}")
    client = client or get_client()
    kwargs: dict = {
        "model": MODELS[role],
        "messages": messages,
        "tools": serv_tools(
            shadow_agent=shadow_agent,
            shadow_hint=shadow_hint,
            shadow_max_iterations=shadow_max_iterations,
        ),
    }

    if budget is not None:
        budget.check()

    started = time.monotonic()
    try:
        # Raw response: needed for the x-openserv-request-id header.
        raw = client.chat.completions.with_raw_response.create(**kwargs)
        resp = raw.parse()
        request_id = raw.headers.get("x-openserv-request-id")
    except APIStatusError as exc:
        if exc.status_code in BUDGET_STATUS_CODES:
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
    if getattr(resp, "usage", None) is not None:
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }
        raw_cost = resp.usage.model_dump().get("cost_usd")
        if raw_cost is not None:
            try:
                cost_usd = float(raw_cost)
            except (TypeError, ValueError):
                cost_usd = None
    if cost_usd is None:
        raw_dump = resp.model_dump()
        for key in ("cost_usd", "cost"):
            if isinstance(raw_dump.get(key), (int, float, str)):
                try:
                    cost_usd = float(raw_dump[key])
                    break
                except (TypeError, ValueError):
                    continue

    # VERIFIED 2026-11-11: SERV reports no per-call cost. Fall back to
    # the loaded price table (catalog pricing x returned tokens) so the
    # inference budget still enforces; the cost is labelled estimated
    # everywhere it surfaces. Without a table, cost stays unknown and
    # the budget cannot project (see budget.py).
    cost_estimated = False
    if cost_usd is None and budget is not None and usage is not None:
        estimated = budget.estimate_for_tokens(
            MODELS[role], usage["prompt_tokens"], usage["completion_tokens"]
        )
        if estimated is not None:
            cost_usd = estimated
            cost_estimated = True

    if budget is not None:
        budget.record(cost_usd)

    meta = (
        f"[serv] role={role} model={MODELS[role]} "
        f"latency={latency:.2f}s request_id={request_id}"
    )
    if usage:
        meta += (
            f" tokens={usage['total_tokens']}"
            f" (in {usage['prompt_tokens']} / out {usage['completion_tokens']})"
        )
    if cost_usd is not None:
        meta += f" cost=${cost_usd:.6f}"
        if cost_estimated:
            meta += " (est, price table)"
    else:
        meta += " cost=not-reported"
    print(meta, flush=True)

    return CallResult(
        role=role,
        model=MODELS[role],
        content=message.content or "",
        latency_s=latency,
        response_id=resp.id,
        usage=usage,
        cost_usd=cost_usd,
        request_id=request_id,
        cost_estimated=cost_estimated,
    )
