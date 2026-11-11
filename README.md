# SpendGuard for SERV — "Two budgets, one agent"

**Hook: your agent's shopping is capped. Its thinking isn't — until now.**

A spend-guarded shopping agent for the SERV Hackathon Edition 02 (Kite
track). It guards the *checkout* with a Kite Passport spending session
($2/tx, $10 total, 1h, passkey-approved) — and it guards the *thinking*
with a hard inference budget on its own SERV Reasoning calls (default
$0.05/run). If the next planned reasoning call would breach the budget,
the run refuses **before dispatch** and writes the refusal verbatim.

Demo task (deliberately the same one the Kite-track competitor
PurseStrings demos — same task, different guard): *cheapest 1GB data
bundle under $0.50*. A deceptive listing steers the agent toward extra
paid reasoning calls and a pricier merchant; the inference budget
refuses the next reasoning call mid-plan; the Kite session caps the
payment; the run ends with a hash-chained ledger and a cited answer.

## How SERV Reasoning powers it

- **Planner + critic on `claude-sonnet-5`** (the plan's
  `claude-sonnet-5-serv-kronos` is not in the live catalog — verified
  2026-11-11; `claude-sonnet-5` is the closest verified substitute),
  screening on
  `claude-haiku-4.5` — one OpenAI-SDK client pointed at
  `https://inference-api.openserv.ai/v1`.
- **`serv_prompt_guard` declared on every call** (opt-in injection
  protection, per docs.openserv.ai/serv-reasoning/tools);
  **`serv_shadow_agent` on planner and critic** (validate-and-iterate,
  hint: every step must respect the budget, every claim must cite).
- **Traceable:** every call's `x-openserv-request-id` lands in the
  hash-chained spend ledger, matching console Usage > Activity.
- **Retrieval:** lean standalone Otari web-search calls (the pattern
  from the Otari lineage below) collect sources into `sources.json`;
  the researcher synthesizes with inline citations; the critic checks
  them.

## Quantified proof (already in hand, from the Otari lineage)

- Otari $0.001 budget refusal: HTTP 403 `budget_exceeded`, call 1, 0
  calls ran — verbatim.
- Otari final run: 9 calls, 23,284 tokens, $0.042818, 20 cited sources,
  critic PASS.
- This repo's test suite: **25/25 mocked tests pass** (budget refusal
  before dispatch, ledger hash-chain tamper detection, Kite cap
  enforcement, SERV tool declarations).

**New proof to capture live (phase 2):** a SERV inference-budget
refusal verbatim; a Kite testnet settlement tx hash (pieUSD); a
red-team eval count (N attacks, 0 paid, 0 over-budget).

## Status — HONEST, 2026-11-11 (phase 1, offline scaffolding)

- ✅ SERV client, inference budget, Kite session + ledger + simulator,
  pipeline wiring, 25 mocked tests — all built, all offline.
- ⚠️ **No live SERV call has been made.** George signed into the
  console (org "George CODM's Works...", $1.00 credit posted) but has
  not entered the API key; the credit must not be touched until phase 2.
- ⚠️ SERV guard declarations are from the published docs, **not yet
  confirmed live**. Whether `cost_usd` appears in SERV responses is
  also unverified — the budget tracks gateway-reported cost only.
- ⚠️ Kite payments are **simulated** in this phase. Receipts are
  labelled `simulated: true` with `tx_hash: null` and are never a
  settlement. Live Kite needs the `kpass` CLI, George's passkey
  approval, and catalog hosts only (Passport refuses own merchants
  with `payment_target_forbidden` — proven by a competitor).

See [BUILD-STATUS.md](BUILD-STATUS.md) for the full phase-2 checklist.

## Run it (phase 2, live)

```bash
cp .env.example .env   # set SERV_API_KEY and OTARI_API_KEY
python3 serv_agent.py "cheapest 1GB data bundle under $0.50"
python3 serv_agent.py --inference-budget 0.05 --kite-mode simulated
python3 -m unittest discover -s tests -v   # offline, no key, no spend
```

## Lineage

Built on tonight's SpendGuard CLI (Otari gateway): planner/researcher/
critic pipeline, retrieval stage, tools preflight, `BudgetRefused`
handling. That work lives on branch `devs-wilding` in this same repo —
see [OTARI-README.md](OTARI-README.md), [RESULTS.md](RESULTS.md) and
[FEEDBACK.md](FEEDBACK.md), including GitHub issues
[#3184](https://github.com/mozilla-ai/otari/issues/3184) and
[#3185](https://github.com/mozilla-ai/otari/issues/3185) filed against
the Otari gateway. The SERV integration, inference budget, and Kite
wiring on branch `openserv-kite` are the hackathon's meaningful new
feature.

Built in Lagos, Nigeria.
