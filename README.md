# SpendGuard CLI — a research agent whose guardrail is the gateway itself

**A command-line research agent that answers questions about a crypto project using live web search and sandboxed code execution — with every model call routed through Otari, split across three open-weight models in distinct roles, and hard-capped by a gateway budget that refuses calls when the money runs out.**

Built for the Mozilla Otari "Devs Wilding" hackathon (branch: `devs-wilding`). It is the Otari-shaped sibling of [Aegis](https://github.com/georgeonweb3/hermes-aegis-spend-guard), a spend guard for AI agents: instead of policing the agent from inside the app, the spending limit lives in the gateway, where the agent cannot reach it.

## Worked example (live run, 2026-11-11)

A real run against the hosted gateway — nothing below is mocked.

```
$ export OTARI_API_KEY=otk_...
$ python3 agent.py "What are the current best practices for putting spending limits on autonomous AI agents, and what do at least two production systems (e.g. cloud LLM gateways, agent frameworks) actually enforce today?"

[otari] role=planner    model=openai/gpt-oss-20b                latency=6.71s id=chatcmpl-Rmd2dOS8MmvuNs0sPReF9aOR    tokens=664 (in 157 / out 507)
[otari] role=researcher model=deepseek/deepseek-v4.1-flash-1017 latency=5.77s id=chatcmpl-RHxHj2sdLVU8fcEit9bf9wVl    tokens=798 (in 221 / out 577)
[otari] role=critic     model=z-ai/glm-5.3-flash                latency=6.49s id=chatcmpl-REZ0hfWoQSnfINOIEOixreTh    tokens=951 (in 707 / out 244)
--- Critique (z-ai/glm-5.3-flash) ---
VERDICT: PASS
```

- **3 calls, 3 open-weight models, 2,413 tokens total, ≈ $0.0005 for the whole run.**
- Honest wrinkle: with web search and web fetch disabled in this workspace (see Gateway findings), the researcher could not source the "what do production systems enforce" claims — and said so, listing exactly what it could not verify instead of inventing citations. The critic passed the draft precisely because it refused to fabricate.

### Budget demo (live, 2026-11-11)

```
$ OTARI_API_KEY=<budget-demo key, per-key limit $0.05> python3 agent.py --budget-demo
... 500 calls later: no refusal ...
```

- 500 tiny calls against a key with a **$0.05** per-key limit spent only **≈ $0.0043** (≈ $0.0000086 per call) — the limit was never approached, so the gateway never refused. At that per-call cost it would take roughly **5,800 calls** to burn through a $0.05 limit.
- **Live budget-refusal error: _still to be captured_** — the gateway's verbatim refusal goes on this line once a run actually hits a per-key limit. It has not happened yet; no refusal occurred in any run so far, and this README does not pretend otherwise.

### Gateway findings (live, 2026-11-11)

- **No API spend readback for a regular key.** `GET /api/v1/key` returns **404** on the hosted gateway, even though the docs list it as the key self-read endpoint; `/api/v1/usage` and `/api/v1/keys` answer **401** (master-key-only). The balance is visible only in the Otari dashboard. The agent now ends a run by stating this plainly instead of surfacing the raw 404.
- **Gateway tools, as actually configured in this workspace:** `otari_web_search` and `otari_web_fetch` are **workspace_disabled**; `otari_code_execution` is **available** and fires server-side when declared — in the arithmetic run it computed 500 × $0.0002 × 30 = **exactly $3.00/month**, and a $2.00 monthly cap binding at **333 calls/day**. Note the API returns **no `tool_calls` array** to the client: execution happens behind the gateway and only the final message comes back.
- **Declaring a workspace-disabled tool gets the whole call refused with a bare 403** (no explanatory body). Declaring only available tools succeeds. A 403 by itself therefore does not prove a budget refusal — context (which tools were declared, what limit the key carries) matters, and this repo reports it that way.

## Architecture — how Otari is used, precisely

- **One choke point.** `client.py` is the only module that talks to a model. It builds an OpenAI SDK client with `base_url="https://api.otari.ai/api/v1"` — note the `/api` segment: per Otari's own docs, `https://api.otari.ai/v1/...` never reaches the gateway (404/403). Keys are region-bound; this project targets a US account (`api.otari.ai`). For an EU account set `OTARI_BASE_URL=https://eu.api.otari.ai/api/v1`.
- **Three open-weight models, three roles** (ids copied from the Otari Models page; configurable in one block at the top of `client.py` or via `OTARI_MODEL_PLANNER` / `OTARI_MODEL_RESEARCHER` / `OTARI_MODEL_CRITIC`):
  - *planner* — small and cheap: turns the question into 3 search queries + 1 calculation.
  - *researcher* — the main reasoner: drafts the answer with gateway tools.
  - *critic* — a third model from a different family: flags unsupported claims; a `VERDICT: REVISE` forces one revision pass.
- **Gateway tools, declared in-request** (per `docs/tools.md`): `otari_web_search` and `otari_web_fetch` for facts, `otari_code_execution` for all arithmetic. Otari executes them; this app never scrapes or calculates on the side. They require the workspace Tools toggles to be on — check with `GET /api/v1/tools`. Set `OTARI_GATEWAY_TOOLS=0` to run without them.
- **Budgets as the guardrail.** The demo key carries its own per-key **Limit (USD)** set in the Otari dashboard (key form → Limit + reset period), on top of the workspace budget. Otari enforces budgets *before* a call is dispatched, so an over-budget call never reaches a provider. `--budget-demo` proves it live: it calls until the gateway refuses, then prints the refusal error verbatim.
- **Key scoping.** The key is workspace-scoped and can be restricted to specific models and tools in the dashboard, so the demo key can never touch anything outside this project's needs. It lives only in the `OTARI_API_KEY` env var — never in the repo (`.env` is gitignored).
- **Routing.** Model selectors route through Otari's catalog across providers; a workspace routing policy (ordered model chain with automatic failover) is configured in the dashboard as the fallback layer. Honest note: this repo does not fake a fallback — if the researcher returns an empty answer, the critic step is what catches it, and that is app behaviour, labelled as such.
- **Observability.** Every call prints role, model, latency, response id and token usage, cross-checkable against the Otari dashboard's Requests tab. Each run ends by attempting a spend readback from `GET /api/v1/key`; on the hosted gateway that endpoint currently 404s for a regular key (and the `/api/v1/usage` endpoints are master-key-only, 401), so the run closes by saying so and pointing at the dashboard balance — see Gateway findings above.
- **No mocks.** If the key is missing, the program says exactly what is missing and exits non-zero. If Otari errors, the error is shown, not papered over.

## Setup

1. Create an account at [otari.ai](https://otari.ai) and complete the welcome questionnaire (a new organization gets $2 of credit once it is answered in full).
2. In the default workspace → **API keys** → **Create key**: name it (e.g. `spendguard-demo`), optionally set a **Limit (USD)** with a daily reset, and copy the token (starts with `otk_`, shown once).
3. In the workspace → **Tools**: switch on Web search and Code execution.
4. On the **Models** page, confirm the three model selectors in `client.py`'s config block still match the live catalog ("Use this model" panel) — ids carry dated suffixes and change.
5. Install and run:

```bash
pip install -r requirements.txt        # the only dependency is the openai SDK
export OTARI_API_KEY=otk_...           # your key from step 2

# check which gateway tools your workspace actually has on:
curl https://api.otari.ai/api/v1/tools -H "Authorization: Bearer $OTARI_API_KEY"

python3 agent.py "Is Project X's token unlock next month bigger than 5% of supply? Show the maths."
python3 agent.py --budget-demo         # set the key limit to ~$0.01 first for a fast refusal
```

## Files

| File | What it is |
| --- | --- |
| `client.py` | The Otari integration: config block (base URL, role models, tools), the chat wrapper with per-call metadata, budget-refusal handling, and the `/api/v1/key` spend readback. The first commit on this branch is this file — Otari is where the project starts. |
| `agent.py` | The SpendGuard CLI: planner → researcher → critic pipeline and the `--budget-demo` mode. |
| `.env.example` | Every env var the project reads. |
| `requirements.txt` | `openai` SDK only; everything else is stdlib. |
