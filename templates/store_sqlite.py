"""Local SQLite store for distilled templates -- the always-works fallback.

EverOS (templates/store_everos.py) is the sponsor-bonus path and needs a hosted
API key. This module has no such dependency: it reads data/templates.jsonl and
writes the rows into data/templates.db so the templates are queryable regardless.

Usage:
  python templates/store_sqlite.py
  python templates/store_sqlite.py --show
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
TEMPLATES_PATH = os.path.join(DATA_DIR, "templates.jsonl")
DB_PATH = os.path.join(DATA_DIR, "templates.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    template_id      TEXT PRIMARY KEY,
    cluster_size     INTEGER NOT NULL,
    template_text    TEXT    NOT NULL,
    slots            TEXT    NOT NULL,   -- JSON array
    example_prompts  TEXT    NOT NULL,   -- JSON array
    avg_tokens_saved REAL    NOT NULL,
    avg_turns_saved  REAL    NOT NULL,
    language         TEXT    NOT NULL,
    markdown         TEXT    NOT NULL    -- human-readable rendering
);
"""


def load_templates(path: str = TEMPLATES_PATH) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found -- run `python templates/build_templates.py` first"
        )
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def render_markdown(row: dict) -> str:
    """Readable markdown for one template -- the same body EverOS stores."""
    slots = "\n".join(f"- `{{{slot}}}`" for slot in row["slots"])
    examples = "\n".join(f"{i}. {p}" for i, p in enumerate(row["example_prompts"], 1))
    return f"""# Prompt template {row['template_id']} ({row['language']})

## Template
```
{row['template_text']}
```

## Slots
{slots}

## Measured savings
- Distilled from **{row['cluster_size']}** collapsed conversations
- Avg tokens saved: **{row['avg_tokens_saved']:,.0f}**
- Avg turns saved: **{row['avg_turns_saved']:.1f}**

## Example prompts
{examples}
"""


def store_templates(rows: list[dict], db_path: str = DB_PATH) -> int:
    """Upsert every template row into SQLite. Returns the number stored."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            """
            INSERT INTO templates (template_id, cluster_size, template_text, slots,
                                   example_prompts, avg_tokens_saved, avg_turns_saved,
                                   language, markdown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(template_id) DO UPDATE SET
                cluster_size     = excluded.cluster_size,
                template_text    = excluded.template_text,
                slots            = excluded.slots,
                example_prompts  = excluded.example_prompts,
                avg_tokens_saved = excluded.avg_tokens_saved,
                avg_turns_saved  = excluded.avg_turns_saved,
                language         = excluded.language,
                markdown         = excluded.markdown
            """,
            [
                (
                    r["template_id"],
                    r["cluster_size"],
                    r["template_text"],
                    json.dumps(r["slots"]),
                    json.dumps(r["example_prompts"]),
                    float(r["avg_tokens_saved"]),
                    float(r["avg_turns_saved"]),
                    r["language"],
                    render_markdown(r),
                )
                for r in rows
            ],
        )
        conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Store templates in local SQLite.")
    parser.add_argument("--show", action="store_true", help="print the stored rows afterwards")
    args = parser.parse_args()

    rows = load_templates()
    n = store_templates(rows)
    print(f"[sqlite] stored {n} templates -> {os.path.relpath(DB_PATH, REPO_ROOT)}")

    with sqlite3.connect(DB_PATH) as conn:
        stored = conn.execute(
            "SELECT template_id, language, cluster_size, avg_tokens_saved, template_text "
            "FROM templates ORDER BY template_id"
        ).fetchall()
    for tid, lang, size, saved, text in stored:
        print(f"  {tid}  [{lang}]  {size} prompts  avg {saved:,.0f} tokens saved")
        print(f"      {text}")

    if args.show:
        with sqlite3.connect(DB_PATH) as conn:
            for (md,) in conn.execute("SELECT markdown FROM templates ORDER BY template_id"):
                print("\n" + "-" * 68 + "\n" + md)

    return n


if __name__ == "__main__":
    main()
