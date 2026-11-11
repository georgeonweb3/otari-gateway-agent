# Feedback on Otari, from one evening of building on it

I built SpendGuard CLI for this hackathon: a research agent (planner, researcher, critic) where every model call goes through Otari, and the spending limit lives in the gateway instead of in my code. Repo: https://github.com/georgeonweb3/otari-gateway-agent (branch `devs-wilding`). Everything below happened live on the hosted gateway on 2026-11-11, on a fresh org with the $2.00 welcome credit. Two of the findings are filed as issues: https://github.com/mozilla-ai/otari/issues/3184 and https://github.com/mozilla-ai/otari/issues/3185.

## 1. When were you most tempted to skip Otari and call the provider directly?

Twice, and both times it was about debugging blind, not about saving money or dodging the gateway.

First, when I tried to read my own spend back through the API and `GET /api/v1/key` returned 404. The dashboard showed my main key at $0.02 of its $0.50 limit, so the number existed. There was just no API path to it: `/usage` and `/keys` are master-key-only (401), and my completion ids would not resolve in `/usage/requests/{id}` (404, "Request not found"). Calling a provider direct would at least have told me what a call cost.

Second, when tool executions inside my agent sequence started coming back `budget_exceeded` with no documented explanation of which limit they were hitting. Same search, standalone, worked fine. In that moment the gateway felt like a black box sitting between me and a working agent.

What stopped me both times: the refusal itself was the thing I came to test, and it worked. A $0.001-capped key got refused on call 1, before dispatch, with a clean JSON body. That is the feature. I was not going to route around the one thing that had just proved itself.

## 2. What did you end up building yourself that Otari should have done for you?

Three things, all small, all on the observability side:

- **Per-request cost visibility.** The completion response carries tokens but no cost, and the key readback endpoint 404s, so my client prints its own per-call metadata (role, model, latency, response id, tokens) and my runs end with a hand-written note saying "readback unavailable, check the dashboard." A `cost` field in the response, or the documented `/key` endpoint actually shipping, deletes that code.
- **A tools preflight.** My client now tells the user to check `GET /api/v1/tools` before a run, and the agent is built to degrade honestly when a tool is off, because declaring a workspace-disabled tool got the whole call refused with a bare 403 and an empty body. Even then, the preflight did not predict the in-sequence `budget_exceeded` refusals: availability said yes, runtime said no.
- **Refusal reasons in error bodies.** My `BudgetRefused` handling exists because the OpenAI SDK's default error string is just "Error code: 403" and throws the gateway's body away. The gateway's actual JSON is one line and says everything: `{"detail":"API key has exceeded budget limit","code":"budget_exceeded"}`. It should reach the developer without the developer digging it out of the raw response, and a tool refusal should name the limit it hit.

## 3. If the rules hadn't required Otari, would you have continued using Otari?

Yes, for two things. Per-key budgets enforced before dispatch are the real product for agent work: my agent cannot talk its way past a cap that lives in the gateway, and I proved that tonight with a refusal on call 1, zero calls executed. And one key across a 174-model catalog let me run three model families (gpt-oss, DeepSeek, GLM) in one evening with zero provider accounts and one base-URL change.

The honest summary: the enforcement is ahead of the observability. Otari stops the spend cold and then cannot tell you, through its own API, what you spent or which limit your tools just hit. Fix the readback and the tool errors and this becomes the default way I run agents.

## What worked

- OpenAI compatibility is real. I pointed the standard SDK at `https://api.otari.ai/api/v1`, set one key, and everything worked. (The `/api` segment matters. Leave it out and nothing reaches the gateway.)
- Per-key budgets enforce before dispatch. Not a warning, not an email after the fact: HTTP 403, `budget_exceeded`, call never ran.
- The catalog. 174 models behind one key; three open-weight models in three distinct roles in a single pipeline, 2,413 tokens, about $0.0005 for the full run.
- Code execution fired server-side and did the arithmetic properly: 500 calls/day at $0.0002 is exactly $3.00/month, and a $2 cap binds at 333 calls/day.
- The $2.00 welcome credit is enough to genuinely evaluate the platform. My main key shows $0.02 spent after an evening of real runs.

## What didn't work

- `GET /api/v1/key` 404s even though the docs list it as the key self-read. `/api/v1/usage` and `/api/v1/keys` answer 401 (master-key-only). Completion ids do not resolve in `/api/v1/usage/requests/{id}`. Filed: issue #3184.
- Tool executions inside a multi-call agent sequence were refused with `[tool error] budget_exceeded: this call would take the request past a spend limit, so it was not run`, while identical standalone calls executed (web search: 5 sources, $0.010383, id `chatcmpl-RQxeMe4VSOFWljhEjjPOZKOc`). Which limit governs tools inside a sequence is not documented anywhere I could find. Filed: issue #3185.
- Declaring `otari_web_fetch` in the agent run rejected the whole request up front: HTTP 422, `{"detail":"Web Retrieval is limited to 10 calls per request"}`, before a single retrieval happened.
- Declaring a workspace-disabled tool earlier in the evening returned a bare 403 with an empty body. Nothing to debug from.
- The API returns no `tool_calls` array for gateway tool runs, so the only evidence a tool executed is the message content. Workable, but it makes verification harder than it should be.

## What's missing

- Per-request cost in the completion response, or a working key self-read endpoint. One of the two; ideally both.
- A tools preflight that matches runtime behaviour: if `GET /api/v1/tools` says available, the execution should not die on an unrelated-looking budget error, or the error should say why.
- Refusal reasons with values: which limit (org, workspace, key, per-request tool budget), what it is set to, what the call would have taken it to.
- Request ids that join completions to usage records, so a run can reconcile what it did against what it cost without opening the dashboard.

Built in Lagos, Nigeria, in one evening.
