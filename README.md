# Prompt-Compressor

Collapsing multi-turn coding conversations into one-shot prompts, and measuring
the tokens that saves.

A real chat session re-sends the whole transcript on every request, so a 6-turn
debugging thread bills a growing prefix of itself once per assistant reply. Most
of those tokens pay for the developer discovering what they should have asked in
the first place. This pipeline reconstructs that ideal first prompt, prices both
paths, and distills the recurring shapes into reusable templates.

## Backend (engine/ + templates/)

```bash
pip install -r requirements.txt

python engine/run.py --limit 15          # -> data/results.jsonl, data/summary.json
python templates/build_templates.py      # -> data/templates.jsonl (+ num_templates)
python templates/store_everos.py         # -> EverOS memories, or data/templates.db
```

Output schemas are fixed by [contracts/schemas.md](contracts/schemas.md) — the
dashboard reads these three files.

### Running without credentials

`engine/collapse.py` has a single switch at the top:

```python
USE_REAL_LLM: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))
```

With no key it returns a **deterministic mock**: rather than a stub constant, it
regex-extracts the concrete details (file, function, exception, line number, root
cause) from the conversation's final exchange and templates them into a plausible
one-shot prompt. So the entire pipeline — billing, collapse, clustering,
distillation, storage — runs end-to-end offline. Export a key (or pin the boolean
to `True`) to switch to `claude-haiku-4-5-20251001`; nothing else changes, because
every LLM call goes through `call_llm`.

### Modules

| File | Role |
| --- | --- |
| `engine/seed.py` | 15 hand-written multi-turn conversations (vague → specific), zero deps |
| `engine/load_data.py` | `load_conversations(limit, use_hf)`; HuggingFace branch falls back to seed on any failure |
| `engine/tokens.py` | `count_tokens` (cl100k_base) and `billed_cost` with re-sent context |
| `engine/collapse.py` | `call_llm` + `collapse_conversation` — the core IP, plus the offline mock |
| `engine/run.py` | Writes `results.jsonl` + `summary.json` (8 worker threads) |
| `templates/cluster.py` | MiniLM embeddings + cosine agglomerative clustering, TF-IDF fallback |
| `templates/distill.py` | `distill_template(prompts)` → `{template_text, slots}` |
| `templates/build_templates.py` | Writes `templates.jsonl`, patches `num_templates` |
| `templates/store_everos.py` | Stores templates as EverOS memories; degrades to SQLite |
| `templates/store_sqlite.py` | Local `data/templates.db` store (always works) |

### Notes on two tuned values

- **Clustering threshold.** The brief suggested a cosine `distance_threshold` of
  0.5, but on this corpus the *closest* pair of collapsed prompts sits at 0.58 —
  15 genuinely different bug types don't embed close together — so 0.5 yields 15
  singletons and zero templates. The default is 0.75 (3 coherent clusters
  covering 13/15 prompts), and `build_clusters` relaxes automatically if a corpus
  needs more room.
- **Savings are scale-dependent.** `pct_saved` rises with conversation length,
  because the re-sent context grows with each turn while the collapsed path always
  bills one prompt plus one answer. The compact seed corpus reports ~45%; the same
  code on real multi-turn data (`--use-hf --limit 60`, HuggingFace
  `m-a-p/Code-Feedback`) reports ~81%. Both are the same honest formula.

### Data sources

`load_conversations()` returns the seed corpus by default. `--use-hf` tries
HuggingFace first: the brief's `CodeChat` no longer resolves on the Hub, so
`m-a-p/Code-Feedback` (same `{role, content}` message shape) is the working
multi-turn source. Any failure — missing package, no network, unexpected schema —
falls back to the seed silently enough not to break a demo, but it always prints
which path it took.
