"""Build data/templates.jsonl from the clustered collapsed prompts.

Reads data/results.jsonl, clusters it, distills one parameterized template per
qualifying cluster, and writes the template rows plus an updated
summary.json["num_templates"].

Usage:
  python templates/build_templates.py
  python templates/build_templates.py --min-cluster-size 3 --distance-threshold 0.75
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cluster import DISTANCE_THRESHOLD, MIN_CLUSTER_SIZE, build_clusters, load_results  # noqa: E402
from distill import distill_template  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
TEMPLATES_PATH = os.path.join(DATA_DIR, "templates.jsonl")
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")

MAX_EXAMPLES = 3


def _majority_language(cluster: list[dict]) -> str:
    """Most common language in the cluster; ties break alphabetically for determinism."""
    counts = Counter(row["language"] for row in cluster)
    top = max(counts.values())
    return sorted(lang for lang, n in counts.items() if n == top)[0]


def build_template_row(template_id: str, cluster: list[dict]) -> dict:
    """Distill one cluster into a templates.jsonl row."""
    prompts = [row["collapsed_prompt"] for row in cluster]
    distilled = distill_template(prompts)

    n = len(cluster)
    return {
        "template_id": template_id,
        "cluster_size": n,
        "template_text": distilled["template_text"],
        "slots": distilled["slots"],
        "example_prompts": prompts[:MAX_EXAMPLES],
        "avg_tokens_saved": round(sum(r["tokens_saved"] for r in cluster) / n, 2),
        "avg_turns_saved": round(sum(r["turns_saved"] for r in cluster) / n, 2),
        "language": _majority_language(cluster),
    }


def update_summary_num_templates(count: int) -> None:
    """Patch summary.json's num_templates in place, leaving other fields alone."""
    if not os.path.exists(SUMMARY_PATH):
        print(f"[templates] {os.path.relpath(SUMMARY_PATH, REPO_ROOT)} not found; "
              f"skipping num_templates update (run engine/run.py first)")
        return

    with open(SUMMARY_PATH, encoding="utf-8") as fh:
        summary = json.load(fh)
    summary["num_templates"] = count
    with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"[templates] summary.json num_templates -> {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Distill reusable prompt templates.")
    parser.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE,
                        help=f"minimum members for a cluster to qualify (default {MIN_CLUSTER_SIZE})")
    parser.add_argument("--distance-threshold", type=float, default=None,
                        help=f"pin the cosine distance threshold (default: relax from {DISTANCE_THRESHOLD})")
    parser.add_argument("--workers", type=int, default=8,
                        help="thread pool size for distillation calls (default 8)")
    args = parser.parse_args()

    rows = load_results()
    clusters = build_clusters(
        rows,
        distance_threshold=args.distance_threshold,
        min_cluster_size=args.min_cluster_size,
    )

    if not clusters:
        print("[templates] no qualifying clusters -- writing an empty templates.jsonl")
        open(TEMPLATES_PATH, "w", encoding="utf-8").close()
        update_summary_num_templates(0)
        print("0 templates")
        return

    ids = [f"T{i}" for i in range(1, len(clusters) + 1)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        template_rows = list(pool.map(build_template_row, ids, clusters))

    with open(TEMPLATES_PATH, "w", encoding="utf-8") as fh:
        for row in template_rows:
            fh.write(json.dumps(row) + "\n")
    print(f"[templates] wrote {len(template_rows)} rows -> "
          f"{os.path.relpath(TEMPLATES_PATH, REPO_ROOT)}")

    update_summary_num_templates(len(template_rows))

    print("\n" + "=" * 68)
    print("  DISTILLED TEMPLATES")
    print("=" * 68)
    for row in template_rows:
        print(f"  {row['template_id']}  [{row['language']}]  "
              f"{row['cluster_size']} prompts  "
              f"avg saved {row['avg_tokens_saved']:,.0f} tokens / "
              f"{row['avg_turns_saved']:.1f} turns")
        print(f"      {row['template_text']}")
        print(f"      slots: {', '.join(row['slots'])}")
    print("=" * 68)
    print(f"\n{len(template_rows)} templates")


if __name__ == "__main__":
    main()
