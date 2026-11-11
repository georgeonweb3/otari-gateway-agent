# SpendGuard CLI — a research agent whose guardrail is the gateway itself

**A command-line research agent that answers questions about a crypto project using live web search and sandboxed code execution — with every model call routed through Otari, split across three open-weight models in distinct roles, and hard-capped by a gateway budget that refuses calls when the money runs out.**

Built for the Mozilla Otari "Devs Wilding" hackathon (branch: `devs-wilding`). It is the Otari-shaped sibling of [Aegis](https://github.com/georgeonweb3/hermes-aegis-spend-guard), a spend guard for AI agents: instead of policing the agent from inside the app, the spending limit lives in the gateway, where the agent cannot reach it.

**Judge's summary: [RESULTS.md](RESULTS.md)** — the refusal proof, the numbers table, and the final run, on one page.

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
- **Then the refusal was captured.** The same key's limit was cut to **$0.001** in the dashboard, below the ≈ $0.0043 it had already spent, and `--budget-demo` was rerun:

  ```
  Call 1: REFUSED by the gateway before dispatch.
  --- gateway error, verbatim ---
  {"detail":"API key has exceeded budget limit","code":"budget_exceeded"}
  --- end of gateway error ---
  ```

  HTTP 403, on **call 1**, with **0 successful calls** in the run: the key was already over its new limit, so the gateway refused before dispatch. That is the whole design working as intended — the cap lives in Otari, keyed on the API key's own spend, and no code in this repo can talk its way past it. (The capture is in `live-run-budget-refusal.txt` in the project records; the client surfaces the gateway's response body itself, after a fix — the OpenAI SDK's default error string is just "Error code: 403" and throws the JSON away.)

### Final run — the retrieval pipeline (live, 2026-11-11)

`python3 agent.py --with-retrieval` answers the same question end to end, with sources:

- Planner (tools off) emitted 3 queries; a **retrieval stage** ran one lean standalone Otari web-search call per query: **3/3 searches executed, 20 unique sources** collected into `sources.json` (paywithlocus.com, aptible.com, docs.llmgateway.io, docs.langchain.com, anthropic.com, learn.microsoft.com, getmaxim.ai, dev.to, …).
- The researcher then synthesized **from those sources only** (tools off), citing URLs inline, and the critic verified every cited link against the source list and confirmed **four production systems** covered (Aptible LLM Gateway, LLM Gateway, LangChain/LangSmith, Azure): **VERDICT: PASS**.
- **9 calls, 23,284 tokens, $0.042818 total** — the sum of the gateway's own per-call `cost_usd` figures, which the client now prints on every call.
- The wrinkle, logged in the run rather than hidden: tool executions on the budget-capped main key are refused inline whatever the remaining balance (dashboard: $0.02 of its $0.50 limit spent), so each search executed on the workspace's **spare key**, which carries no per-key budget. Same call, A/B keys, reproduced all evening — see Gateway findings.

### Gateway findings (live, 2026-11-11)

- **No API spend readback for a regular key.** `GET /api/v1/key` returns **404** on the hosted gateway, even though the docs list it as the key self-read endpoint; `/api/v1/usage` and `/api/v1/keys` answer **401** (master-key-only). The balance is visible only in the Otari dashboard. The agent now ends a run by stating this plainly instead of surfacing the raw 404.
- **Gateway tools, as actually configured in this workspace:** at first check `otari_web_search` and `otari_web_fetch` were **workspace_disabled** and `otari_code_execution` was **available**; after the workspace's **Allow web access** toggle was switched on in the dashboard, all three report available in `GET /api/v1/tools`. `otari_code_execution` fires server-side when declared — in the arithmetic run it computed 500 × $0.0002 × 30 = **exactly $3.00/month**, and a $2.00 monthly cap binding at **333 calls/day** (VERDICT: PASS). Note the assistant message returns **no `tool_calls` array** to the client — execution happens behind the gateway — but the raw response's `otari_tool_calls` array does report each execution's status, which the client now prints alongside the message.
- **Declaring a workspace-disabled tool gets the whole call refused with a bare 403** (no explanatory body). Declaring only available tools succeeds. A 403 by itself therefore does not prove a budget refusal — context (which tools were declared, what limit the key carries) matters, and this repo reports it that way.
- **Tools inside a multi-call agent sequence behaved differently from tools standalone.** With web access enabled, the full pipeline was rerun: **4 calls, 14,384 tokens, ≈ $0.002 of model calls**. Inside that sequence, every tool execution came back refused inline — `[tool error] budget_exceeded: this call would take the request past a spend limit, so it was not run` — even though the dashboard showed the main key at **$0.02 of its $0.50 limit** and no doc explains which spend limit governs tool executions inside a sequence. The identical web search, sent as a standalone isolated call, **did execute**: 5 sources returned, **$0.010383**, completion id `chatcmpl-RQxeMe4VSOFWljhEjjPOZKOc`. And declaring `otari_web_fetch` in the agent run rejected the whole request before any retrieval happened: HTTP 422, `{"detail":"Web Retrieval is limited to 10 calls per request"}`. Both behaviours are filed as an issue on the Otari repo (see the submission notes).
- **Attaching a per-key budget appears to break tool execution on that key.** Later the same evening, the pattern sharpened into an A/B: every tool execution attempted on the budget-capped main key — search or code execution, standalone or in sequence — came back refused/failed inline (e.g. completion `chatcmpl-RrGec334JOKdX0hT0zkHBXEF`: both searches failed, `budget_exceeded`), while the identical calls on the workspace's spare key (no per-key budget attached) **executed** (a standalone search returned 5 sources at $0.010421; then 3/3 searches in the final run). With $0.48 of the main key's $0.50 limit unspent, this is not exhaustion; which budget object the tool check reads is not documented. The retrieval stage therefore runs its searches on the spare key and logs that it did. (Also in issue #3185.)
- **Per-request cost IS reported — in the raw response.** Each completion's usage object carries `cost_usd` (the typed OpenAI SDK usage fields don't surface it, which is why earlier runs estimated). The client now reads it from the raw response and prints it per call; the final run's $0.042818 total is the sum of the gateway's own figures. The key-level readback is what remains missing (previous bullet).
- **Guardrails: processed, but nothing behind them on the hosted deployment.** `GET /api/v1/tool-settings/guardrails/profiles` answers `{"available": false, "reason": "No guardrails service is configured, so its profiles cannot be listed."}`. A chat request carrying a per-request `guardrails` field gets an `Otari-Guardrails` response header — reporting `status: "unavailable", reason: "not_configured"` for the `prompt-injection` profile. The gateway is honest that the check did not run; this entry accordingly does **not** claim guardrails.

## Architecture — how Otari is used, precisely

- **One choke point.** `client.py` is the only module that talks to a model. It builds an OpenAI SDK client with `base_url="https://api.otari.ai/api/v1"` — note the `/api` segment: per Otari's own docs, `https://api.otari.ai/v1/...` never reaches the gateway (404/403). Keys are region-bound; this project targets a US account (`api.otari.ai`). For an EU account set `OTARI_BASE_URL=https://eu.api.otari.ai/api/v1`.
- **Three open-weight models, three roles** (ids copied from the Otari Models page; configurable in one block at the top of `client.py` or via `OTARI_MODEL_PLANNER` / `OTARI_MODEL_RESEARCHER` / `OTARI_MODEL_CRITIC`):
  - *planner* — small and cheap: turns the question into 3 search queries + 1 calculation.
  - *researcher* — the main reasoner: drafts the answer with gateway tools.
  - *critic* — a third model from a different family: flags unsupported claims; a `VERDICT: REVISE` forces one revision pass.
- **Gateway tools, declared in-request** (per `docs/tools.md`): `otari_web_search` and `otari_web_fetch` for facts, `otari_code_execution` for all arithmetic. Otari executes them; this app never scrapes or calculates on the side. They require the workspace Tools toggles to be on, so `client.py` runs a **preflight** — `GET /api/v1/tools`, once per process — and declares only what the gateway reports available. Set `OTARI_GATEWAY_TOOLS=0` to run without them. One consequence of tonight's findings: the pipeline's default mode declares all three tools in the researcher call, while `--with-retrieval` instead runs search as lean standalone calls in a dedicated retrieval stage and synthesizes tools-off, because in-sequence and on-budget-capped-key tool executions are refused (Gateway findings).
- **Budgets as the guardrail.** The demo key carries its own per-key **Limit (USD)** set in the Otari dashboard (key form → Limit + reset period), on top of the workspace budget. Otari enforces budgets *before* a call is dispatched, so an over-budget call never reaches a provider. `--budget-demo` proves it live: it calls until the gateway refuses, then prints the refusal error verbatim.
- **Key scoping.** The key is workspace-scoped and can be restricted to specific models and tools in the dashboard, so the demo key can never touch anything outside this project's needs. It lives only in the `OTARI_API_KEY` env var — never in the repo (`.env` is gitignored).
- **Routing.** Model selectors route through Otari's catalog across providers. Otari also offers dashboard routing policies (ordered model chains with automatic failover); **none was configured for this entry**, and nothing here claims one. If the researcher returns an empty answer, the critic step is what catches it — that is app behaviour, labelled as such.
- **Observability.** Every call prints role, model, latency, response id, token usage and the gateway-reported cost (`usage.cost_usd` in the raw response; the typed SDK usage object hides it), cross-checkable against the Otari dashboard's Requests tab. Each run ends by attempting a spend readback from `GET /api/v1/key`; on the hosted gateway that endpoint currently 404s for a regular key (and the `/api/v1/usage` endpoints are master-key-only, 401), so the run closes by saying so and pointing at the dashboard balance — see Gateway findings above.
- **No mocks.** If the key is missing, the program says exactly what is missing and exits non-zero. If Otari errors, the error is shown, not papered over.

## Otari features used — exactly what tonight's runs exercised

- **OpenAI-compatible gateway.** One workspace API key in front of a **174-model catalog**; the app changes only `base_url` (`https://api.otari.ai/api/v1`) and the key. No provider accounts, no per-provider code.
- **Three open-weight models in distinct roles**, all through that one key: planner `openai/gpt-oss-20b`, researcher `deepseek/deepseek-v4.1-flash-1017`, critic `z-ai/glm-5.3-flash`.
- **Per-API-key budgets, enforced before dispatch.** Main key capped at **$0.50** in the dashboard ($0.02 spent, per the dashboard); a second key capped at **$0.001** was refused on call 1 with HTTP 403 `budget_exceeded` (see Budget demo).
- **`otari_code_execution`** — fired server-side in the arithmetic run (exactly $3.00/month; a $2 cap binding at 333 calls/day; VERDICT: PASS). The assistant message carries no `tool_calls` array; the raw response's `otari_tool_calls` array reports each execution's status.
- **`otari_web_search`** — executed in standalone isolated calls (5 sources returned, $0.010383, completion id `chatcmpl-RQxeMe4VSOFWljhEjjPOZKOc`) and, in the final run, inside the project's own **retrieval stage**: 3/3 planner queries searched, 20 unique sources collected, and a cited synthesis passed the critic (see Final run). Inside a multi-call sequence its executions are refused inline, and on a key carrying a per-key budget they are refused anywhere — both stated plainly in Gateway findings, not smoothed over. The retrieval stage's searches therefore run on the workspace's spare key (no per-key budget), and the run logs say so.
- **Tools preflight (ours, on Otari's API)** — before any tools-declaring call, `client.py` queries `GET /api/v1/tools` once per process and declares only the tools it reports available, logging each verdict. Declaring blind is what produced the bare-403 refusals earlier in the evening.
- **Per-request cost readback** — the gateway reports `cost_usd` in each response's raw usage object; the client prints it per call and the final run's $0.042818 total is the sum of those figures.
- **Workspace "Allow web access" toggle** — enabling it flipped all three gateway tools to available in `GET /api/v1/tools`.
- **Not used:** routing policies (none configured) and guardrail profiles. Guardrails were checked, not assumed: the hosted deployment has **no guardrails service configured** (`GET /api/v1/tool-settings/guardrails/profiles` → `available: false`), and a per-request profile reports `unavailable / not_configured` in the `Otari-Guardrails` header without evaluating anything. Nothing in this repo claims them.

## Setup

1. Create an account at [otari.ai](https://otari.ai) and complete the welcome questionnaire (a new organization gets $2 of credit once it is answered in full).
2. In the default workspace → **API keys** → **Create key**: name it (e.g. `spendguard-demo`), optionally set a **Limit (USD)** with a daily reset, and copy the token (starts with `otk_`, shown once).
3. In the workspace → **Tools**: enable **Allow web access** so the gateway tools report available in `GET /api/v1/tools`.
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
| `RESULTS.md` | One-page judge summary: the refusal proof, the numbers table, the final run, the exact features list. |
| `client.py` | The Otari integration: config block (base URL, role models, tools), tools preflight, the chat wrapper with per-call metadata (including gateway-reported cost and tool-execution statuses), budget-refusal handling, and the `/api/v1/key` spend readback. The first commit on this branch is this file — Otari is where the project starts. |
| `agent.py` | The SpendGuard CLI: planner → researcher → critic pipeline, the `--with-retrieval` mode (standalone search retrieval → cited synthesis → citation-checking critic), and the `--budget-demo` mode. |
| `sources.json` | The 20 sources the final run's retrieval stage collected, with the queries that produced them. |
| `.env.example` | Every env var the project reads. |
| `requirements.txt` | `openai` SDK only; everything else is stdlib. |
