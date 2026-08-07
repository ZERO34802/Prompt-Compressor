"""Store distilled templates as EverOS memories (sponsor bonus).

Each template is written as one readable markdown memory containing the template
text, its slots, and the measured savings, so an agent can later recall "the
template for this kind of bug" instead of re-deriving it.

What the SDK actually requires
------------------------------
`pip install everos-cloud` gives the HTTP client for the hosted EverOS API,
imported as `everos_cloud`. It needs an API key (EVEROS_API_KEY); the relevant
call is:

    from everos_cloud import EverOS
    client = EverOS(api_key=os.environ.get("EVEROS_API_KEY"))
    client.add(session_id=..., messages=[...])

So this script is written against that real API, and when the key is absent (or
the API errors) it falls back to templates/store_sqlite.py rather than failing.
templates.jsonl is the contract the dashboard depends on; this stage is a bonus
and must never break the pipeline.

Usage:
  EVEROS_API_KEY=... python templates/store_everos.py
  python templates/store_everos.py              # -> SQLite fallback
  python templates/store_everos.py --dry-run    # render memories without sending
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store_sqlite import load_templates, render_markdown, store_templates  # noqa: E402

SESSION_ID = "prompt_compressor"


def memory_messages(row: dict) -> list[dict]:
    """Render one template as an EverOS memory: a single user message whose
    content is the template's rendered markdown."""
    return [
        {
            "role": "user",
            "content": render_markdown(row),
            "timestamp": int(time.time() * 1000),
        },
    ]


def store_in_everos(rows: list[dict], dry_run: bool = False) -> int:
    """Store templates as EverOS memories. Returns the count stored.

    Raises on any failure so the caller can fall back; it never swallows an error
    and reports success.
    """
    from everos_cloud import EverOS

    if dry_run:
        for row in rows:
            msgs = memory_messages(row)
            print(f"\n--- {row['template_id']} would be stored as {len(msgs)} messages ---")
            print(msgs[0]["content"][:400] + "...")
        return len(rows)

    client = EverOS(api_key=os.environ.get("EVEROS_API_KEY"))
    stored = 0
    for row in rows:
        client.add(
            session_id=SESSION_ID,
            messages=memory_messages(row),
        )
        print(f"[everos] stored {row['template_id']} ({row['language']}, "
              f"{row['cluster_size']} prompts)")
        stored += 1
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description="Store templates as EverOS memories.")
    parser.add_argument("--dry-run", action="store_true",
                        help="render the memories without contacting the API")
    parser.add_argument("--no-fallback", action="store_true",
                        help="fail loudly instead of falling back to SQLite")
    args = parser.parse_args()

    rows = load_templates()
    print(f"[everos] {len(rows)} templates to store")

    try:
        n = store_in_everos(rows, dry_run=args.dry_run)
        print(f"[everos] done -- {n} templates stored"
              f"{' (dry run, nothing sent)' if args.dry_run else ''}")
        return
    except Exception as exc:  # noqa: BLE001 -- bonus stage, must not break the pipeline
        reason = f"{type(exc).__name__}: {exc}"
        if not os.environ.get("EVEROS_API_KEY"):
            reason = "EVEROS_API_KEY is not set"
        print(f"[everos] unavailable ({reason})")
        if args.no_fallback:
            raise

    print("[everos] falling back to local SQLite store")
    n = store_templates(rows)
    print(f"[sqlite] stored {n} templates -> data/templates.db")


if __name__ == "__main__":
    main()
