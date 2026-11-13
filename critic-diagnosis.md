# Critic guard-refusal diagnosis — 2026-11-13 ~22:00 WAT

## The incident (phase 2, run 3)

In the phase-2 final run the critic (claude-sonnet-5, shadow_agent on)
returned exactly `I can't share that.` instead of a `VERDICT: PASS /
REVISE` line — 59.30s latency, 3,553 tokens consumed (in 3,163 /
out 390), $0.013294 est, request_id
843b699c-b5c7-4e66-b8e6-889be1c4674f. The pipeline treats any
non-REVISE critique as accept, so the draft stood and the run ended
coherently (DataHub NG $0.45, simulated payment, ledger valid).

Note the shape: a prompt-guard refusal on input returns **0 tokens**
(see the SwiftNet screener refusal, request_id
17091080-a1d3-465b-9fff-9d516aba5747). The critic call consumed full
tokens, so the refusal came **after** generation — it is a
shadow-agent validation outcome, not an input-guard trip.

## Isolation probes

Replays of the run-3 critic call from its SAVED inputs (sources block
rebuilt from `sources-serv-final.json`, draft extracted verbatim from
`live-run-serv-final.txt`), via `/tmp/critic_probe.py` (probe script
kept out of the repo; it loads the SERV key as an authd surrogate and
prints no secret). Same system prompt, same shadow hint, same inputs.

| Probe | Model | Shadow | Result |
|---|---|---|---|
| P1 (first attempt) | haiku-4.5 | off | Call executed (2,574 tokens, ~$0.00515) but the probe script crashed on a formatting bug before printing the verdict — spend sunk, result unrecorded. Script fixed (price table load + None-safe cost print). |
| P1 (re-run) | haiku-4.5 | off | **No refusal.** `**REVISION REQUIRED**` (non-standard format) — haiku objects that no screening records are in the input. $0.004785, 6.55s. |
| P2 | haiku-4.5 | on | **No refusal.** `VERDICT: PASS`, 25.46s, $0.004714. |
| P3 | sonnet-5 | on | **No refusal.** `VERDICT: REVISE` (same complaint as P1: no screening records in the input), 23.73s, $0.013684. |
| P3b (repeat of P3) | sonnet-5 | on | **No refusal.** Pass-style verdict (draft supported, DataHub NG $0.45 correct), ~41s, ~$0.014. |

The red-team run's critic (fresh inputs, sonnet-5, shadow on) also
returned `VERDICT: PASS`, 42.12s, $0.014594.

## Finding

**The refusal does not reproduce.** Four replays of the exact run-3
inputs (plus one fresh-input run) all returned verdicts. It is not
deterministically triggered by the critic's input text (sources block
or draft — haiku reads the same text clean), nor by the shadow-agent
path as such (haiku + shadow is clean), nor by the model (sonnet-5 +
shadow is clean twice).

Best explanation, from the evidence: **nondeterministic / transient
gateway behaviour in the shadow-agent validation loop.** The critic's
verdicts are demonstrably sampled, not deterministic — the identical
inputs produced REVISE (P3), a pass-style verdict (P3b), PASS (P2)
and a non-standard `**REVISION REQUIRED**` (P1). The refusal is most
plausibly another sample outcome: the validation loop failing to
converge within its iterations, with the gateway substituting the
guard-refusal string as the final content. Supporting hint: run 3's
critic latency (59.30s) is the longest of any critic call observed
(P3 23.73s, P3b ~41s, red-team 42.12s) — consistent with extra
validation iterations before giving up.

## Consequence for the entry

- **No input-sanitization fix is indicated.** Unlike the researcher
  case (where the SwiftNet injected text provably tripped the guard
  and withholding UNSAFE descriptions fixed it), there is no tainted
  input to sanitize here — the same inputs pass clean on replay.
- **The pipeline already handles it correctly.** A refusal is not a
  REVISE, so the draft stands; in run 3 the draft was correct. The
  refusal is a missing verdict, not a wrong verdict.
- **Optional polish (not required for submission):** retry the critic
  once when it returns a refusal instead of a verdict (bounded, one
  retry, ~$0.014), and log the refusal. The nondeterminism also means
  the demo should keep presenting the critic as advisory, which it
  already does — REVISE forces one revision pass, anything else
  accepts.

## Probe spend

- P1 crashed attempt ~$0.00515, P1 $0.004785, P2 $0.004714,
  P3 $0.013684, P3b ~$0.014 — **~$0.042 total** for the diagnosis.
- Combined with the red-team run ($0.037811 SERV), total SERV spend
  for this task is **~$0.080**, inside the ~$0.10 cap. No top-up, no
  card, no payments of any kind were made.
