#!/usr/bin/env python3
"""SpendGuard CLI — a research agent that runs entirely on the Otari gateway.

Pipeline (three open-weight models, three distinct roles, one gateway key):

  1. planner    (small/cheap)  — turns the question into a research plan.
  2. researcher (main reasoner) — drafts the answer using Otari's gateway
     tools: otari_web_search / otari_web_fetch for facts and
     otari_code_execution for ALL arithmetic. No mental maths.
  3. critic     (different family) — flags unsupported claims in the draft;
     a REVISE verdict forces one revision pass before anything is printed
     as the final answer.

Every call goes through client.py -> Otari. If Otari refuses a call because
the key's budget is spent, the run stops and shows the refusal — the gateway
is the spend guard, not this app.

Usage:
  python3 agent.py "Is Project X's unlock next month bigger than 5% of supply?"
  python3 agent.py --budget-demo   # call until the gateway refuses on budget
"""

from __future__ import annotations

import argparse
import json
import sys

import client as otari

PLANNER_SYSTEM = (
    "You are the planner in a research pipeline. Decompose the user's "
    "question into a short plan: exactly 3 web search queries (prefix each "
    "with 'QUERY: ') and 1 calculation that will be needed (prefix it with "
    "'CALC: '). Output only those 4 lines."
)

RESEARCHER_SYSTEM = (
    "You are the researcher in a research pipeline. Answer the user's "
    "question using the plan provided. Use the Otari gateway web search and "
    "web fetch tools for every factual claim, and the code execution tool "
    "for ALL arithmetic (percentages, totals, conversions) — never do maths "
    "in your head. Cite the source URL next to each factual claim. If a tool "
    "is unavailable, say what you could not verify instead of guessing."
)

CRITIC_SYSTEM = (
    "You are the critic in a research pipeline, from a different model "
    "family than the author of the draft. Check the draft for: claims with "
    "no cited source, arithmetic that is not shown, and conclusions stronger "
    "than the evidence. Reply with a first line that is exactly "
    "'VERDICT: PASS' or 'VERDICT: REVISE', followed by short bullet reasons."
)

REVISION_SYSTEM = (
    "You are the researcher revising a draft after a critic's review. Fix "
    "every point the critic raised: add sources, show arithmetic (via the "
    "code execution tool), or soften/remove claims you cannot support. "
    "Output the full revised answer."
)


def run_research(question: str) -> int:
    otari_client = otari.get_client()  # raises MissingApiKey before any work

    print(f"Question: {question}\n")
    plan = otari.chat(
        "planner",
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": question},
        ],
        client=otari_client,
    )
    print(f"\n--- Plan ({plan.model}) ---\n{plan.content}\n")

    draft = otari.chat(
        "researcher",
        [
            {"role": "system", "content": RESEARCHER_SYSTEM},
            {
                "role": "user",
                "content": f"Question: {question}\n\nPlan:\n{plan.content}",
            },
        ],
        tools=True,
        client=otari_client,
    )
    print(f"\n--- Draft ({draft.model}) ---\n{draft.content}\n")

    critique = otari.chat(
        "critic",
        [
            {"role": "system", "content": CRITIC_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\nDraft to check:\n{draft.content}"
                ),
            },
        ],
        client=otari_client,
    )
    print(f"\n--- Critique ({critique.model}) ---\n{critique.content}\n")

    answer = draft.content
    first_line = critique.content.strip().splitlines()[0] if critique.content.strip() else ""
    if first_line.startswith("VERDICT: REVISE"):
        revised = otari.chat(
            "researcher",
            [
                {"role": "system", "content": REVISION_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\nDraft:\n{draft.content}\n\n"
                        f"Critic's review:\n{critique.content}"
                    ),
                },
            ],
            tools=True,
            client=otari_client,
        )
        answer = revised.content
        print("(Draft revised once per the critic's review.)\n")

    print(f"=== Answer ===\n{answer}\n")
    print_spend_summary()
    return 0


def print_spend_summary() -> None:
    """Cross-check spend against the gateway's own record (GET /api/v1/key).

    On the hosted gateway that endpoint currently answers 404 for a regular
    key (verified live, 2026-11-11), so there is no API spend readback —
    say exactly that instead of surfacing the raw HTTP failure, and never
    invent a figure. A successful pipeline run still exits 0.
    """
    try:
        usage = otari.fetch_key_usage()
    except Exception:  # 404 on the hosted gateway; report it plainly
        print(
            "Spend readback via API unavailable: GET /api/v1/key returns "
            "404 on the hosted gateway (docs list it as a key self-read). "
            "Balance is visible in the Otari dashboard."
        )
        return
    print(f"[otari] key budget/usage per GET /api/v1/key: {json.dumps(usage)}")


def run_budget_demo(max_calls: int = 500) -> int:
    """Make cheap calls until Otari refuses one on budget; print the refusal.

    Set the API key's Limit (USD) low first (e.g. $0.01 in the Otari
    dashboard) or this will simply spend real balance, one tiny call at a
    time, up to max_calls.
    """
    otari_client = otari.get_client()
    model = otari.MODELS["planner"]
    print(
        f"Budget demo: calling {model} via Otari until the gateway refuses "
        f"a call on budget (max {max_calls} calls).\n"
        "Tip: this demo is meant to run with a tiny per-key limit set in "
        "the Otari dashboard.\n"
    )
    for i in range(1, max_calls + 1):
        try:
            otari.chat(
                "planner",
                [{"role": "user", "content": "Reply with the single word: ok"}],
                client=otari_client,
            )
        except otari.BudgetRefused as refused:
            print(f"\nCall {i}: REFUSED by the gateway before dispatch.")
            print("--- gateway error, verbatim ---")
            print(refused.raw)
            print("--- end of gateway error ---")
            print(
                "That refusal is the demo: the budget lives in Otari, is "
                "enforced before the call runs, and no code in this repo "
                "can talk its way past it."
            )
            print_spend_summary()
            return 0
        print(f"  call {i}: ok")
    print(
        f"\nNo refusal after {max_calls} calls. The key's budget was never "
        "hit — lower the per-key Limit (USD) in the Otari dashboard and rerun."
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SpendGuard CLI — research agent on the Otari gateway."
    )
    parser.add_argument("question", nargs="?", help="the research question")
    parser.add_argument(
        "--budget-demo",
        action="store_true",
        help="call until Otari refuses on budget, printing the refusal verbatim",
    )
    args = parser.parse_args(argv)

    try:
        if args.budget_demo:
            return run_budget_demo()
        if not args.question:
            parser.print_usage(sys.stderr)
            print("error: give a question, or run with --budget-demo", file=sys.stderr)
            return 2
        return run_research(args.question)
    except otari.MissingApiKey as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except otari.BudgetRefused as refused:
        print("\nOtari refused a call before dispatch (budget / spending limit).")
        print("--- gateway error, verbatim ---")
        print(refused.raw)
        print("--- end of gateway error ---")
        return 1


if __name__ == "__main__":
    sys.exit(main())
