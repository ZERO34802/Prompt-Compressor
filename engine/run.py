"""Pipeline entry point: collapse every conversation and write the results.

Writes:
  data/results.jsonl   one row per conversation (schema: contracts/schemas.md)
  data/summary.json    aggregate figures for the dashboard

Usage:
  python engine/run.py --limit 15
  python engine/run.py --limit 100 --use-hf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collapse import USE_REAL_LLM, collapse_conversation  # noqa: E402
from load_data import load_conversations  # noqa: E402
from tokens import billed_cost, count_tokens  # noqa: E402

# Shared with the dashboard -- see contracts/schemas.md
INPUT_PRICE = 3.0 / 1_000_000
OUTPUT_PRICE = 15.0 / 1_000_000

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
RESULTS_PATH = os.path.join(DATA_DIR, "results.jsonl")
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")
TEMPLATES_PATH = os.path.join(DATA_DIR, "templates.jsonl")


def _last_assistant_content(turns: list[dict]) -> str:
    for turn in reversed(turns):
        if turn.get("role") == "assistant":
            return turn.get("content", "")
    return ""


def process_conversation(convo: dict) -> dict:
    """Collapse one conversation and compute its before/after billing row."""
    turns = convo["turns"]
    original = billed_cost(turns)

    collapsed_prompt = collapse_conversation(convo)
    collapsed_prompt_tokens = count_tokens(collapsed_prompt)

    # The collapsed run bills once: the ideal prompt in, one final answer out.
    # We reuse the real conversation's final assistant turn as the stand-in for
    # that answer, since it is the same content the developer needed.
    final_assistant_tokens = count_tokens(_last_assistant_content(turns))
    collapsed_billed_tokens = collapsed_prompt_tokens + final_assistant_tokens

    billed_total = original["billed_total_tokens"]
    tokens_saved = billed_total - collapsed_billed_tokens
    pct_saved = (tokens_saved / billed_total) if billed_total else 0.0

    original_cost = (
        original["billed_input_tokens"] * INPUT_PRICE
        + original["billed_output_tokens"] * OUTPUT_PRICE
    )
    collapsed_cost = (
        collapsed_prompt_tokens * INPUT_PRICE + final_assistant_tokens * OUTPUT_PRICE
    )

    return {
        "conv_id": convo["conv_id"],
        "language": convo["language"],
        "original_turns": len(turns),
        "billed_total_tokens": billed_total,
        "collapsed_prompt": collapsed_prompt,
        "collapsed_prompt_tokens": collapsed_prompt_tokens,
        "collapsed_billed_tokens": collapsed_billed_tokens,
        "tokens_saved": tokens_saved,
        "pct_saved": round(pct_saved, 4),
        "turns_saved": len(turns) - 1,
        "original_cost_usd": round(original_cost, 6),
        "collapsed_cost_usd": round(collapsed_cost, 6),
    }


def _count_templates() -> int:
    """Read the template count off disk so re-running run.py never contradicts
    an existing templates.jsonl. build_templates.py rewrites this field too."""
    if not os.path.exists(TEMPLATES_PATH):
        return 0
    with open(TEMPLATES_PATH, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def build_summary(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        raise ValueError("no conversations processed -- nothing to summarise")

    return {
        "num_conversations": n,
        "avg_original_turns": round(sum(r["original_turns"] for r in rows) / n, 4),
        "avg_billed_tokens": round(sum(r["billed_total_tokens"] for r in rows) / n, 4),
        "avg_collapsed_tokens": round(sum(r["collapsed_billed_tokens"] for r in rows) / n, 4),
        "avg_pct_saved": round(sum(r["pct_saved"] for r in rows) / n, 4),
        "total_tokens_saved": sum(r["tokens_saved"] for r in rows),
        "total_cost_saved_usd": round(
            sum(r["original_cost_usd"] - r["collapsed_cost_usd"] for r in rows), 6
        ),
        "num_templates": _count_templates(),
    }


def print_summary(summary: dict, rows: list[dict]) -> None:
    print("\n" + "=" * 68)
    print("  PROMPT COMPRESSOR -- COLLAPSE SUMMARY")
    print("=" * 68)
    print(f"  conversations processed   {summary['num_conversations']}")
    print(f"  avg turns (original)      {summary['avg_original_turns']:.2f}  ->  1")
    print(f"  avg billed tokens         {summary['avg_billed_tokens']:,.0f}  ->  "
          f"{summary['avg_collapsed_tokens']:,.0f}")
    print(f"  avg tokens saved          {summary['avg_pct_saved'] * 100:.1f}%")
    print(f"  total tokens saved        {summary['total_tokens_saved']:,}")
    print(f"  total cost saved          ${summary['total_cost_saved_usd']:.4f}")
    print(f"  templates on disk         {summary['num_templates']}")
    print("-" * 68)

    best = max(rows, key=lambda r: r["pct_saved"])
    print(f"  best case: {best['conv_id']} saved {best['pct_saved'] * 100:.1f}% "
          f"({best['billed_total_tokens']:,} -> {best['collapsed_billed_tokens']:,} tokens)")
    print(f"    \"{best['collapsed_prompt'][:110]}...\"")
    print("=" * 68 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collapse multi-turn coding conversations.")
    parser.add_argument("--limit", type=int, default=50,
                        help="max conversations to process (default 50)")
    parser.add_argument("--workers", type=int, default=8,
                        help="thread pool size for LLM calls (default 8)")
    parser.add_argument("--use-hf", action="store_true",
                        help="try the HuggingFace dataset before falling back to the seed corpus")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    convos = load_conversations(limit=args.limit, use_hf=args.use_hf)
    mode = "ANTHROPIC API" if USE_REAL_LLM else "deterministic mock (no ANTHROPIC_API_KEY)"
    print(f"[run] {len(convos)} conversations | llm={mode} | workers={args.workers}")

    # ThreadPoolExecutor.map preserves input order, so rows line up with convos.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(process_conversation, convos))

    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    print(f"[run] wrote {len(rows)} rows -> {os.path.relpath(RESULTS_PATH, REPO_ROOT)}")

    summary = build_summary(rows)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"[run] wrote summary  -> {os.path.relpath(SUMMARY_PATH, REPO_ROOT)}")

    print_summary(summary, rows)


if __name__ == "__main__":
    main()
