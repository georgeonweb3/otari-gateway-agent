# SpendGuard CLI — a research agent whose guardrail is the gateway itself

**A command-line research agent that answers questions about a crypto project using live web search and sandboxed code execution — with every model call routed through Otari, split across three open-weight models in distinct roles, and hard-capped by a gateway budget that refuses calls when the money runs out.**

Built for the Mozilla Otari "Devs Wilding" hackathon (branch: `devs-wilding`). It is the Otari-shaped sibling of [Aegis](https://github.com/georgeonweb3/hermes-aegis-spend-guard), a spend guard for AI agents: instead of policing the agent from inside the app, the spending limit lives in the gateway, where the agent cannot reach it.

## Worked example

_Placeholder — real numbers from the live run land here tonight._

```
$ export OTARI_API_KEY=otk_...
$ python3 agent.py "Is Project X's token unlock next month bigger than 5% of supply? Show the maths."

[otari] role=planner    model=<planner model>    latency=<t>s id=<request id>
[otari] role=researcher model=<researcher model> latency=<t>s id=<request id>
[otari] role=critic     model=<critic model>     latency=<t>s id=<request id>
=== Answer ===
<answer with cited sources and code-executed arithmetic>
[otari] key budget/usage per GET /api/v1/key: <readback>
```

Budget refusal (per-key limit temporarily set to $0.01):

```
$ python3 agent.py --budget-demo
Call <n>: REFUSED by the gateway before dispatch.
--- gateway error, verbatim ---
<the gateway's actual refusal error, pasted unedited>
--- end of gateway error ---
```

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
- **Observability.** Every call prints role, model, latency, response id and token usage, and each run ends with a spend readback from `GET /api/v1/key` — both cross-checkable against the Otari dashboard's Requests tab and `GET /api/v1/usage/requests/{request_id}`.
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
