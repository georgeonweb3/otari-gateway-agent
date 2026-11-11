# Results — SpendGuard CLI on Otari (all live, 2026-11-11)

**Every call this agent makes runs through Otari — and when the money runs out, Otari says no first.** A research agent (planner → researcher → critic, three open-weight models, one gateway key) whose spending limit lives in the gateway, enforced before dispatch:

```
Call 1: REFUSED by the gateway before dispatch.
--- gateway error, verbatim ---
{"detail":"API key has exceeded budget limit","code":"budget_exceeded"}
--- end of gateway error ---
```

That is a key whose per-key limit was cut to **$0.001** in the Otari dashboard, below the ≈ $0.0043 it had already spent: HTTP 403, on call 1, **0 calls executed**. No code in this repo can talk its way past it — that is the product.

## The numbers

| Run | Calls | Tokens | Cost | Outcome |
| --- | --- | --- | --- | --- |
| Pipeline, tools off | 3 | 2,413 | ≈ $0.0005 | VERDICT: PASS. Researcher refused to invent citations it could not source — and said exactly what it could not verify. |
| Code-execution run | 3 | 4,509 | — | VERDICT: PASS. `otari_code_execution` computed 500 calls/day × $0.0002 × 30 days = **exactly $3.00/month**, and a $2.00 cap binding at **333 calls/day**. |
| Budget demo, $0.05 key | 500 | — | ≈ $0.0043 spent | No refusal: at ≈ $0.0000086/call it would take ~5,800 calls to burn $0.05. The limit was then cut to $0.001 (next row). |
| Budget refusal, $0.001 key | 1 | — | $0 | **Refused before dispatch** (verbatim error above). |
| **Final run, with retrieval** | **9** | **23,284** | **$0.042818** (gateway-reported) | **VERDICT: PASS** — a fully cited answer (below). |

## The final run — the pipeline, completed

`python3 agent.py --with-retrieval` on the question *"What are the current best practices for putting spending limits on autonomous AI agents, and what do at least two production systems actually enforce today?"*

- Planner (tools off) emitted 3 search queries.
- **Retrieval stage:** one lean standalone Otari web-search call per query — **3/3 searches executed, 20 unique sources** collected into `sources.json` (paywithlocus.com, aptible.com, docs.llmgateway.io, docs.langchain.com, anthropic.com, learn.microsoft.com, getmaxim.ai, dev.to, …).
- Researcher synthesis (tools off) answered **from those sources only**, citing URLs inline; the critic verified every cited link against the source list and confirmed **four production systems** covered (Aptible LLM Gateway, LLM Gateway, LangChain/LangSmith, Azure) — VERDICT: PASS. Its bottom line, honestly hedged: gateway-layer, code-enforced budgets are the recommended pattern, but only Aptible documents a concrete dollar cap (org-wide $1–$10,000 plus per-key) among the systems retrieved; the others enforce request-rate limits rather than spend caps.
- One wrinkle, logged in the run output rather than hidden: tool executions on the budget-capped main key are refused inline (`budget_exceeded`) whatever the remaining balance — the dashboard showed $0.02 of the $0.50 limit spent — so the searches executed on the workspace's spare key, which has no per-key budget attached. Same call, A/B keys, reproduced all evening. Filed as [issue #3185](https://github.com/mozilla-ai/otari/issues/3185).

## Otari features used — exactly what the runs exercised

- **OpenAI-compatible gateway** — one workspace key in front of a **174-model catalog**; the app changes only `base_url` (`https://api.otari.ai/api/v1`).
- **Three open-weight models in distinct roles** through that one key: planner `openai/gpt-oss-20b`, researcher `deepseek/deepseek-v4.1-flash-1017`, critic `z-ai/glm-5.3-flash`.
- **Per-API-key budgets, enforced before dispatch** — main key capped at $0.50; a second key capped at $0.001 refused on call 1 (above).
- **`otari_web_search`** — executed in the final run's retrieval stage (3/3 searches, 20 sources) and in standalone probes (5 sources, $0.010383, id `chatcmpl-RQxeMe4VSOFWljhEjjPOZKOc`). Inside a multi-call sequence its executions were refused inline; on the budget-capped key they are refused anywhere. Both behaviours are in [issue #3185](https://github.com/mozilla-ai/otari/issues/3185).
- **`otari_code_execution`** — fired server-side in the arithmetic run (content-level evidence; the API returns no `tool_calls` array, but the raw response's `otari_tool_calls` array does report each execution's status).
- **Tools preflight** — before any tools-declaring run, the client calls `GET /api/v1/tools` and declares only what the workspace reports available (declaring a disabled tool gets the whole call refused with a bare 403). Built by us, on Otari's API.
- **Per-request cost readback** — every response's raw usage object carries `cost_usd`; the client prints it per call (the final run's $0.042818 total is the sum of the gateway's own figures). What is still missing: a key-level readback — `GET /api/v1/key` 404s though documented ([issue #3184](https://github.com/mozilla-ai/otari/issues/3184)).
- **Workspace "Allow web access" toggle** — enabling it flipped all three gateway tools to available in `GET /api/v1/tools`.

**Not used, deliberately not claimed:** routing policies (none configured) and guardrail profiles. On guardrails we checked properly: `GET /api/v1/tool-settings/guardrails/profiles` answers `{"available": false, "reason": "No guardrails service is configured…"}`, and a per-request `guardrails` field is processed but reports `status: "unavailable", reason: "not_configured"` in the `Otari-Guardrails` header — the check never evaluates on the hosted deployment, so there is nothing honest to claim.

## Evidence

- Raw run outputs (project records): `live-run-main.txt` (pipeline), `live-run-codeexec.txt` (code execution), `live-run-main-tools.txt` (in-sequence tool refusals), `live-run-retrieval.txt` (retrieval stage, raw), `live-run-final.txt` (the final run above), `live-run-budget.txt` + `live-run-budget-refusal.txt` (budget demo + refusal). `sources.json` (this repo) holds the final run's 20 sources.
- Issues filed on the Otari repo: [#3184](https://github.com/mozilla-ai/otari/issues/3184) (spend readback 404s; usage endpoints master-key-only) and [#3185](https://github.com/mozilla-ai/otari/issues/3185) (tool executions refused with `budget_exceeded` in sequences and on budget-capped keys, while standalone calls on an uncapped key succeed; fetch declaration 422s the whole request).
- Full narrative, architecture and setup: [README.md](README.md). Feedback on Otari itself: [FEEDBACK.md](FEEDBACK.md).
