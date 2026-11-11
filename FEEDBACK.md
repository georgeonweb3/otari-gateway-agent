# Feedback on Otari, from one evening of building on it

I built SpendGuard CLI for this hackathon: a research agent (planner, researcher, critic) where every model call goes through Otari, and the spending limit lives in the gateway instead of in my code. Repo: https://github.com/georgeonweb3/otari-gateway-agent (branch `devs-wilding`). Everything below happened live on the hosted gateway on 2026-11-11, on a fresh org with the $2.00 welcome credit. Two of the findings are filed as issues: https://github.com/mozilla-ai/otari/issues/3184 and https://github.com/mozilla-ai/otari/issues/3185.

## 1. When were you most tempted to skip Otari and call the provider directly?

Twice, and both times it was about debugging blind, not about saving money or dodging the gateway.

First, when I tried to read my own spend back through the API and `GET /api/v1/key` returned 404. The dashboard showed my main key at $0.02 of its $0.50 limit, so the number existed. There was just no API path to it: `/usage` and `/keys` are master-key-only (401), and my completion ids would not resolve in `/usage/requests/{id}` (404, "Request not found"). Calling a provider direct would at least have told me what a call cost.

Second, when tool executions inside my agent sequence started coming back `budget_exceeded` with no documented explanation of which limit they were hitting. Same search, standalone, worked fine. In that moment the gateway felt like a black box sitting between me and a working agent.

What stopped me both times: the refusal itself was the thing I came to test, and it worked. A $0.001-capped key got refused on call 1, before dispatch, with a clean JSON body. That is the feature. I was not going to route around the one thing that had just proved itself.

## 2. What did you end up building yourself that Otari should have done for you?

Three things, all small, all on the observability side:

- **Per-request cost visibility in my client.** The gateway does put `cost_usd` in the raw response's usage object — but the OpenAI SDK's typed usage fields don't surface it, and the key-level readback (`/key`) 404s, so my client digs the cost out of the raw dump, prints it per call alongside role/model/latency/id/tokens, and my runs still end with a hand-written note saying "key readback unavailable, check the dashboard." Surfacing cost in the typed response — or shipping the documented `/key` endpoint — deletes most of that code.
- **A tools preflight.** My client now calls `GET /api/v1/tools` before any tools-declaring run and declares only what the workspace reports available, because declaring a workspace-disabled tool got the whole call refused with a bare 403 and an empty body. Even then, the preflight does not predict runtime behaviour: availability said yes, and executions still died — inside sequences, and (later) on any key carrying a per-key budget.
- **A retrieval stage.** Because tool executions inside my pipeline calls kept being refused, the final version of the agent does its web search as lean standalone calls in a separate stage, collects the sources, and synthesizes from them with tools off. That shape executes — 3/3 searches, 20 sources — but only on the workspace key that carries no per-key budget; the capped key refused the identical calls. An agent framework should not have to route around the gateway's own tool accounting to use the gateway's own search.
- **Refusal reasons in error bodies.** My `BudgetRefused` handling exists because the OpenAI SDK's default error string is just "Error code: 403" and throws the gateway's body away. The gateway's actual JSON is one line and says everything: `{"detail":"API key has exceeded budget limit","code":"budget_exceeded"}`. It should reach the developer without the developer digging it out of the raw response, and a tool refusal should name the limit it hit.

## 3. If the rules hadn't required Otari, would you have continued using Otari?

Yes, for two things. Per-key budgets enforced before dispatch are the real product for agent work: my agent cannot talk its way past a cap that lives in the gateway, and I proved that tonight with a refusal on call 1, zero calls executed. And one key across a 174-model catalog let me run three model families (gpt-oss, DeepSeek, GLM) in one evening with zero provider accounts and one base-URL change.

The honest summary: the enforcement is ahead of the observability. Otari stops the spend cold and then cannot tell you, through its own API, what you spent or which limit your tools just hit. Fix the readback and the tool errors and this becomes the default way I run agents.

## What worked

- OpenAI compatibility is real. I pointed the standard SDK at `https://api.otari.ai/api/v1`, set one key, and everything worked. (The `/api` segment matters. Leave it out and nothing reaches the gateway.)
- Per-key budgets enforce before dispatch. Not a warning, not an email after the fact: HTTP 403, `budget_exceeded`, call never ran.
- The catalog. 174 models behind one key; three open-weight models in three distinct roles in a single pipeline, 2,413 tokens, about $0.0005 for the full run.
- Code execution fired server-side and did the arithmetic properly: 500 calls/day at $0.0002 is exactly $3.00/month, and a $2 cap binds at 333 calls/day.
- The final pipeline run completed the original research question with real retrieval: 3 standalone searches, 20 sources, a synthesis citing them, and a critic that verified the citations — 9 calls, 23,284 tokens, $0.042818 at the gateway's own reported prices.
- Even the guardrails plumbing is honest about itself: with no guardrails service configured on the hosted deployment, a per-request profile reports `status: "unavailable", reason: "not_configured"` in the response header instead of silently passing. (We didn't claim guardrails — there was nothing configured to claim.)
- The $2.00 welcome credit is enough to genuinely evaluate the platform. My main key shows $0.02 spent after an evening of real runs.

## What didn't work

- `GET /api/v1/key` 404s even though the docs list it as the key self-read. `/api/v1/usage` and `/api/v1/keys` answer 401 (master-key-only). Completion ids do not resolve in `/api/v1/usage/requests/{id}`. Filed: issue #3184.
- Tool executions inside a multi-call agent sequence were refused with `[tool error] budget_exceeded: this call would take the request past a spend limit, so it was not run`, while identical standalone calls executed (web search: 5 sources, $0.010383, id `chatcmpl-RQxeMe4VSOFWljhEjjPOZKOc`). Which limit governs tools inside a sequence is not documented anywhere I could find. Filed: issue #3185.
- By the end of the evening the pattern had sharpened: tool executions on my main key — the one carrying the $0.50 per-key budget — were refused everywhere, standalone included (both searches failed in completion `chatcmpl-RrGec334JOKdX0hT0zkHBXEF`), while the identical calls on the workspace's spare key, which has no per-key budget attached, executed (3/3 searches in the final run). The capped key had $0.48 of its limit unspent, so this is not exhaustion: attaching a budget appears to break tool execution on that key. My agent's retrieval stage now runs its searches on the spare key and logs that it did. Also in issue #3185.
- Declaring `otari_web_fetch` in the agent run rejected the whole request up front: HTTP 422, `{"detail":"Web Retrieval is limited to 10 calls per request"}`, before a single retrieval happened.
- Declaring a workspace-disabled tool earlier in the evening returned a bare 403 with an empty body. Nothing to debug from.
- The assistant message carries no `tool_calls` array for gateway tool runs: the raw response's `otari_tool_calls` array does report each execution's status (once I found it), but the typed SDK response hides it, so the only evidence a tool executed, through the SDK, is the message content. Workable, but it makes verification harder than it should be.

## What's missing

- Per-request cost in the completion response, or a working key self-read endpoint. One of the two; ideally both.
- A tools preflight that matches runtime behaviour: if `GET /api/v1/tools` says available, the execution should not die on an unrelated-looking budget error, or the error should say why.
- Refusal reasons with values: which limit (org, workspace, key, per-request tool budget), what it is set to, what the call would have taken it to.
- Request ids that join completions to usage records, so a run can reconcile what it did against what it cost without opening the dashboard.

Built in Lagos, Nigeria, in one evening.
