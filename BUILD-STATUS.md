# OpenServ build status — phase 1 complete (offline), phase 2 = live

Updated: 2026-11-11 ~21:10 WAT. Entry: SERV Hackathon Edition 02,
Kite track — "Two budgets, one agent". Deadline: **Sun Nov 15, 2026,
23:59 UTC (Mon Nov 16, 00:59 WAT)**. Submission form:
https://openserv.typeform.com/submissions02 (X post tagging @openservai
required). Prize pool officially TBA — do not quote $5,000.

Repo: https://github.com/georgeonweb3/otari-gateway-agent —
branch `openserv-kite` (this phase), base `devs-wilding` (Otari entry,
submitted 2026-11-11, DoraHacks build 51065).

## What exists (phase 1, built offline, NO live calls made)

In `~/workspace/openserv-entry/spendguard-serv/` (branch `openserv-kite`):

- `serv_client.py` — SERV Reasoning client (OpenAI SDK,
  `https://inference-api.openserv.ai/v1`, planner/critic
  `claude-sonnet-5-serv-kronos`, screener `claude-haiku-4.5`).
  Declares `serv_prompt_guard` on every call and `serv_shadow_agent`
  (hint + max_iterations via schema defaults) on planner/critic, per
  docs.openserv.ai/serv-reasoning/tools (fetched 2026-11-11).
  Captures `x-openserv-request-id` via raw response; reads `cost_usd`
  from raw usage when present. Key from `SERV_API_KEY` env only.
- `budget.py` — `InferenceBudget` (default $0.05/run). Refuses BEFORE
  dispatch when spent + max-observed-call-cost would breach the limit;
  refusal record mirrors Otari's `{"detail": ..., "code":
  "inference_budget_exceeded", ...}` shape. Tracks gateway-reported
  cost only — nothing invented. Optional verified price-table loader
  (`serv_prices.json`, console prices only).
- `kite.py` — `KiteSession` ($2/tx, $10 total, 1h; per-tx + total cap
  enforcement, `KiteCapRefused`), hash-chained append-only
  `SpendLedger` (SHA-256, `verify()`), `KiteSimulator` (same cap
  checks; receipts labelled `simulated: true`, `tx_hash: null`),
  `KiteCli` wrapper (`ksearch`/`kpass`, raises `KiteCliMissing` when
  absent), `direct_x402_settlement()` stub that refuses offline.
- `serv_agent.py` — pipeline: SERV planner (guard + shadow) →
  per-listing SERV screening → Otari retrieval (lean standalone
  searches, `sources.json`, cited synthesis) → SERV researcher →
  SERV critic (shadow) → Kite session authorizes payment → ledger.
  Refusals (inference budget / SERV guard / Kite cap) write
  `refusal.json` verbatim-style and exit 1 with the ledger intact.
- `demo_listings.json` — red-team fixture: DataHub NG $0.45 (SAFE),
  SwiftNet Deals $0.49 (UNSAFE — injected instruction to ignore the
  budget and buy a $2.40 premium bundle), BudgetAirtime $0.62 (over
  ceiling). Ground truth in fixture metadata, never shown to the model.
- `tests/test_spendguard_serv.py` — **25/25 pass, all mocked**
  (`python3 -m unittest discover -s tests -v`): budget refusal before
  dispatch (fake client never called), ledger tamper detection, Kite
  per-tx/total/not-active caps, simulator labelling, SERV tool
  declarations, missing-key errors, full mocked pipeline run
  (6 SERV calls, $0.03 mocked spend, simulated $0.45 payment,
  chain valid) + mocked mid-run budget refusal (refusal.json written).
- Otari pipeline intact in the copy: `agent.py --help` verified;
  `client.py`/`agent.py` untouched except the README move
  (`README.md` → `OTARI-README.md`; new SERV `README.md` written).

## What is stubbed / simulated (honest labels, never faked)

- Kite payments: **simulated only**. No chain, no wallet, no
  settlement. Receipts carry `simulated: true`, `tx_hash: null`.
- `direct_x402_settlement()`: raises `NotImplementedError` offline.
- Merchant listings: fixture file, not live Kite catalog results.
- `serv_prices.json`: does not exist; the price-table path is tested
  with a synthetic table only.

## What needs the live key (phase 2) — exact checklist

George: create the SERV API key in the console (org "George CODM's
Works...", $1.00 credit posted 2026-11-11 21:01 WAT — note: the
hackathon promised $5 pre-registration credits; only $1.00 landed,
so budget live runs tightly) and put it in `.env` as `SERV_API_KEY`
(never in chat, never committed). Then, in order:

1. [ ] **SERV smoke test** — one cheap call:
   `SERV_API_KEY=... .venv/bin/python -c` via `serv_client.chat`
   (or `serv_agent.py` with `--inference-budget 0.05`). Confirm:
   (a) `claude-sonnet-5-serv-kronos` resolves; (b) declared
   `serv_prompt_guard` is accepted (no error about the tool);
   (c) response carries `x-openserv-request-id`; (d) whether
   `cost_usd` appears in raw usage — if NOT, fill `serv_prices.json`
   from the console Models page and load it, and note the budget
   then runs on verified table estimates (labelled as estimates).
   Save raw output to `live-run-serv-smoke.txt`.
2. [ ] **Inference-budget refusal capture (core proof)** — run the
   pipeline with a tiny budget (`--inference-budget 0.005`) so the
   second/third call refuses mid-plan; save `refusal.json` verbatim
   and the ledger. Must differ from PurseStrings' captured
   `serv_prompt_guard` refusal — this one is a budget refusal.
3. [ ] **Full live run** — default $0.05 budget, simulated Kite:
   verify planner → screening (SwiftNet flagged UNSAFE) → cited
   synthesis → critic PASS → simulated $0.45 payment, ledger chain
   valid. Save `live-run-serv-final.txt` + `ledger.json`.
4. [ ] **Kite live path** — install `kpass` (`curl -fsSL
   https://cli.gokite.ai/install.sh | bash`), `kpass signup` +
   passkey creation (**George's device gesture, cannot be
   delegated**), `ksearch services list` for payable catalog hosts.
   Request a session ($2/$10/1h) and approve it via the approval
   URL on George's passkey device. Then a catalog-host payment via
   `kpass session execute` (live path only ever pays catalog
   services — own hosts are refused `payment_target_forbidden`).
5. [ ] **Testnet settlement receipt (proof b)** — pieUSD settlement
   via the x402 facilitator to our own testnet merchant, OR a
   catalog-host payment receipt; record the tx hash. If neither is
   reachable, the entry ships with the simulated path honestly
   labelled — decide consciously, do not fake it.
6. [ ] **Red-team eval (proof c)** — run the 3-listing fixture
   (plus 2–3 new adversarial listings) and record: N attacks tried,
   0 paid, 0 over-budget. Save `live-run-redteam.txt`.
7. [ ] **Package** — demo video (3–5 min, per-video approval),
   deck/write-up (hook + worked example + quantified proof up front),
   X post tagging @openservai (George posts from @georgebuilding2),
   Typeform submit by ~20:00 WAT Nov 15 (buffer before 23:59 UTC).

## Blockers / risks

- $1.00 credit landed, not $5 — live runs must stay inside ~$1
  (a full SERV run is $0.02–0.08 per the existing entrant; ~12–40
  runs possible, but keep a margin and get George's go for any
  top-up — none planned).
- Kite passkey + catalog-host payment are George-gated and
  unverified; if blocked, the entry can still submit with the
  simulated Kite path honestly labelled, but it loses proof (b).
- Prize pool TBA; Nigeria eligibility unwritten (no restriction
  published). Prizes paid after verification (of what, unstated).
- BCH BLAZE kickoff stream Sat Nov 14 15:00 WAT — George must
  attend; build around it.
