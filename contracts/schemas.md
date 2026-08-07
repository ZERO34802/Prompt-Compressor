results.jsonl (Person A writes, one JSON per line):
{ "conv_id": str, "language": str, "original_turns": int,
  "billed_total_tokens": int, "collapsed_prompt": str,
  "collapsed_prompt_tokens": int, "collapsed_billed_tokens": int,
  "tokens_saved": int, "pct_saved": float, "turns_saved": int,
  "original_cost_usd": float, "collapsed_cost_usd": float }

templates.jsonl (Person A writes, one JSON per line):
{ "template_id": str, "cluster_size": int, "template_text": str,
  "slots": [str], "example_prompts": [str],
  "avg_tokens_saved": float, "avg_turns_saved": float, "language": str }

summary.json (Person A writes, single object):
{ "num_conversations": int, "avg_original_turns": float,
  "avg_billed_tokens": float, "avg_collapsed_tokens": float,
  "avg_pct_saved": float, "total_tokens_saved": int,
  "total_cost_saved_usd": float, "num_templates": int }

PRICING CONSTANTS (both use these):
INPUT_PRICE = 3.0 / 1_000_000
OUTPUT_PRICE = 15.0 / 1_000_000
