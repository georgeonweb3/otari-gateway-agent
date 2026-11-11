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
  python3 agent.py --with-retrieval "question"
      # planner -> standalone Otari web-search retrieval per query ->
      # researcher synthesis over the collected sources (tools off) ->
      # critic checks every claim carries a citation
"""

from __future__ import annotations

import argparse
import json
import os
import re
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


DEFAULT_QUESTION = (
    "What are the current best practices for putting spending limits on "
    "autonomous AI agents, and what do at least two production systems "
    "(e.g. cloud LLM gateways, agent frameworks) actually enforce today?"
)

RETRIEVAL_SYSTEM = (
    "You have a web search tool. Use it exactly once for the query the "
    "user gives you, then output a source list: for each source found, "
    "its title, its URL, and a one-line snippet of what it says. Output "
    "only the source list. If the search does not run, say so plainly."
)

RETRIEVAL_SYSTEM_LEAN = (
    "Search the web once for the query. List the sources: title, URL, "
    "one-line snippet. Nothing else."
)

SYNTHESIS_SYSTEM = (
    "You are the researcher in a research pipeline. Answer the user's "
    "question using ONLY the sources provided — they were retrieved live "
    "through the Otari gateway's web search. Cite the source URL inline "
    "after every factual claim, like [https://example.com/page]. Cover "
    "at least two distinct production systems if the sources support "
    "it. Do not state anything the sources do not support; if the "
    "sources are thin on a point, say what could not be verified. Do "
    "not invent figures, prices or features."
)

CRITIC_CITATIONS_SYSTEM = (
    "You are the critic in a research pipeline, from a different model "
    "family than the author of the draft. The draft was written from a "
    "fixed source list, which you are also given. Check: (1) every "
    "factual claim carries an inline citation to a URL that appears in "
    "the source list; (2) at least two distinct production systems are "
    "covered; (3) no figure or feature appears that the sources do not "
    "support. Reply with a first line that is exactly 'VERDICT: PASS' "
    "or 'VERDICT: REVISE', followed by short bullet reasons."
)

REVISION_SOURCES_SYSTEM = (
    "You are the researcher revising a draft after a critic's review. "
    "You may only use the sources provided: add the missing citations "
    "(URLs from the source list, inline), cover a second production "
    "system if the sources support one, and remove or soften any claim "
    "the sources do not support. Output the full revised answer."
)


def parse_queries(plan_content: str, question: str) -> list[str]:
    """Pull the planner's QUERY: lines (max 3, deduped); fall back to the
    question itself if the planner emitted none."""
    queries: list[str] = []
    for line in plan_content.splitlines():
        match = re.match(r"\s*QUERY:\s*(.+)", line)
        if match:
            query = match.group(1).strip()
            if query and query not in queries:
                queries.append(query)
    return queries[:3] or [question]


def _snippet_for(content: str, url: str) -> str:
    """Best-effort one-line snippet: the text the model put right after
    this source's URL in its source list. Empty string if not found —
    snippets are never invented."""
    idx = content.find(url)
    if idx < 0:
        return ""
    tail = content[idx + len(url):]
    tail = re.split(r"https?://", tail)[0]  # stop at the next source's URL
    tail = re.sub(r"[*#`>]+", " ", tail)
    tail = re.sub(r"^\s*\d+\.\s*", " ", tail)
    tail = re.sub(r"\s+", " ", tail).strip(" -—:;,.")
    return tail[:220]


def _completed_search_sources(result: otari.CallResult) -> list[dict]:
    """Sources from this call's completed gateway web searches, if any."""
    sources: list[dict] = []
    for tool_call in result.otari_tool_calls or []:
        if tool_call.get("type") != "web_search":
            continue
        if tool_call.get("status") != "completed":
            continue
        for raw in tool_call.get("sources") or []:
            url = (raw.get("url") or "").strip()
            if not url:
                continue
            sources.append(
                {
                    "title": (raw.get("title") or "").strip(),
                    "url": url,
                    "snippet": _snippet_for(result.content, url),
                }
            )
    return sources


def run_retrieval(
    queries: list[str], main_client, log_path: str
) -> tuple[list[dict], list[otari.CallResult], dict]:
    """One lean standalone search call per query; collect real sources.

    Tool executions on the budget-capped main key are currently refused
    inline by the gateway (budget_exceeded, whatever the remaining
    balance — see issue #3185), so a refused first attempt is retried
    once with a leaner prompt on the workspace's spare key
    (OTARI_SETUP_KEY, same org, no per-key budget attached) when one is
    configured. Which key executed is logged, never hidden. If both
    attempts fail, the query contributes no sources — nothing is faked.
    """
    spare_key = os.environ.get("OTARI_SETUP_KEY", "").strip()
    spare_client = None
    if spare_key and spare_key != os.environ.get("OTARI_API_KEY", "").strip():
        spare_client = otari.get_client(spare_key)

    all_results: list[otari.CallResult] = []
    sources_by_url: dict[str, dict] = {}
    stats = {"executed": 0, "via": set()}
    log: list[str] = []

    for i, query in enumerate(queries, 1):
        attempts = [
            ("main key", main_client, RETRIEVAL_SYSTEM),
            (
                "spare key (no per-key budget attached)"
                if spare_client is not None
                else "main key",
                spare_client if spare_client is not None else main_client,
                RETRIEVAL_SYSTEM_LEAN,
            ),
        ]
        got: list[dict] = []
        for attempt_no, (key_label, use_client, system) in enumerate(attempts, 1):
            log.append(
                f"\n===== query {i}/{len(queries)} attempt {attempt_no} "
                f"via {key_label} =====\n{query}\n"
            )
            try:
                result = otari.chat(
                    "researcher",
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": query},
                    ],
                    tools_override=[{"type": "otari_web_search"}],
                    client=use_client,
                )
            except Exception as exc:  # refused/errored: record, try next
                log.append(f"call failed: {type(exc).__name__}: {exc}\n")
                print(
                    f"[retrieval] query {i} attempt {attempt_no} "
                    f"({key_label}) failed: {type(exc).__name__}",
                    flush=True,
                )
                continue
            all_results.append(result)
            log.append(
                f"response id: {result.response_id}\n"
                f"otari_tool_calls: {json.dumps(result.otari_tool_calls)}\n"
                f"--- content ---\n{result.content}\n"
            )
            got = _completed_search_sources(result)
            if got:
                stats["executed"] += 1
                stats["via"].add(key_label)
                print(
                    f"[retrieval] query {i}: {len(got)} sources "
                    f"(via {key_label})",
                    flush=True,
                )
                break
            print(
                f"[retrieval] query {i} attempt {attempt_no} "
                f"({key_label}): search did not execute",
                flush=True,
            )
        for source in got:
            sources_by_url.setdefault(source["url"], source)

    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("SpendGuard CLI — retrieval stage raw output\n")
        fh.write("".join(log))
    return list(sources_by_url.values()), all_results, stats


def _sources_block(sources: list[dict]) -> str:
    if not sources:
        return "(no sources were retrieved)"
    lines = []
    for n, source in enumerate(sources, 1):
        lines.append(f"{n}. {source['title']} — {source['url']}")
        if source["snippet"]:
            lines.append(f"   Snippet: {source['snippet']}")
    return "\n".join(lines)


def _totals_line(label: str, results: list[otari.CallResult]) -> str:
    tokens = sum((r.usage or {}).get("total_tokens", 0) for r in results)
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    cost_part = (
        f", gateway-reported cost ${sum(costs):.6f} across {len(costs)} calls"
        if costs
        else ""
    )
    return f"[totals] {label}: {len(results)} calls, {tokens} tokens{cost_part}"


def run_with_retrieval(question: str) -> int:
    otari_client = otari.get_client()  # raises MissingApiKey before any work
    results: list[otari.CallResult] = []

    if otari.GATEWAY_TOOLS_ENABLED:
        # Startup preflight: log which gateway tools the workspace has on.
        otari.filter_available_tools(otari.GATEWAY_TOOLS)

    print(f"Question: {question}\n")
    plan = otari.chat(
        "planner",
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": question},
        ],
        client=otari_client,
    )
    results.append(plan)
    print(f"\n--- Plan ({plan.model}) ---\n{plan.content}\n")

    queries = parse_queries(plan.content, question)
    log_path = os.environ.get("OTARI_RETRIEVAL_LOG", "live-run-retrieval.txt")
    sources, retrieval_results, stats = run_retrieval(queries, otari_client, log_path)
    results.extend(retrieval_results)
    with open("sources.json", "w", encoding="utf-8") as fh:
        json.dump(
            {
                "question": question,
                "queries": queries,
                "sources": sources,
                "searches_executed": stats["executed"],
                "executed_via": sorted(stats["via"]),
            },
            fh,
            indent=2,
        )
    print(
        f"\n[retrieval] {stats['executed']}/{len(queries)} searches executed; "
        f"{len(sources)} unique sources collected; raw output in {log_path}; "
        "sources in sources.json\n"
    )

    block = _sources_block(sources)
    draft = otari.chat(
        "researcher",
        [
            {"role": "system", "content": SYNTHESIS_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\nSources retrieved via Otari "
                    f"web search:\n{block}"
                ),
            },
        ],
        client=otari_client,
    )
    results.append(draft)
    print(f"\n--- Draft ({draft.model}) ---\n{draft.content}\n")

    critique = otari.chat(
        "critic",
        [
            {"role": "system", "content": CRITIC_CITATIONS_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\nSource list:\n{block}\n\n"
                    f"Draft to check:\n{draft.content}"
                ),
            },
        ],
        client=otari_client,
    )
    results.append(critique)
    print(f"\n--- Critique ({critique.model}) ---\n{critique.content}\n")

    answer = draft.content
    first_line = critique.content.strip().splitlines()[0] if critique.content.strip() else ""
    if first_line.startswith("VERDICT: REVISE"):
        revised = otari.chat(
            "researcher",
            [
                {"role": "system", "content": REVISION_SOURCES_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\nSources:\n{block}\n\n"
                        f"Draft:\n{draft.content}\n\nCritic's review:\n"
                        f"{critique.content}"
                    ),
                },
            ],
            client=otari_client,
        )
        results.append(revised)
        answer = revised.content
        print("(Draft revised once per the critic's review.)\n")

    print(f"=== Answer ===\n{answer}\n")
    print(_totals_line("with-retrieval run", results))
    print_spend_summary()
    return 0


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
    parser.add_argument(
        "--with-retrieval",
        action="store_true",
        help="run the retrieval pipeline: planner -> standalone Otari web "
        "search per query -> cited synthesis -> citation-checking critic "
        "(question optional; defaults to the spend-limits question)",
    )
    args = parser.parse_args(argv)

    try:
        if args.budget_demo:
            return run_budget_demo()
        if args.with_retrieval:
            return run_with_retrieval(args.question or DEFAULT_QUESTION)
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
