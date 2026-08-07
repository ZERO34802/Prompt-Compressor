"""Match a new prompt against stored templates -- the retrieval half.

templates/store_sqlite.py and templates/store_everos.py only *store* templates;
this module answers the reverse question: given a fresh prompt, which stored
template (from data/templates.jsonl) does it look like?

Each template's "signature" is the embedding of its concatenated example_prompts,
computed with the same local model templates/cluster.py uses
(sentence-transformers all-MiniLM-L6-v2 -- fully local, no API keys). Signatures
are cached in memory after the first call.

The match threshold is deliberately HIGH (0.62): in a demo it is far worse to
claim a false match than to say "new task". Below the threshold we report
matched=False and the caller can treat the prompt as a template-to-be-learned.

Usage:
  python3 templates/match_template.py "some new prompt"   # match one prompt
  python3 templates/match_template.py                     # self-test on known prompts
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

EMBED_MODEL = "all-MiniLM-L6-v2"  # same local model as templates/cluster.py
MATCH_THRESHOLD = 0.62
NO_MATCH_MESSAGE = "New task — no template yet (would be learned)"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_PATH = os.path.join(REPO_ROOT, "data", "templates.jsonl")

# In-memory caches, filled lazily on first match() call.
_model = None
_templates: list[dict] | None = None
_signatures: np.ndarray | None = None  # (n_templates, dim), L2-normalized


def load_templates(path: str = TEMPLATES_PATH) -> list[dict]:
    """Read data/templates.jsonl into a list of row dicts."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found -- run `python templates/build_templates.py` first"
        )
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _get_signatures() -> tuple[list[dict], np.ndarray]:
    """Return (templates, signature matrix), building and caching on first use."""
    global _templates, _signatures
    if _templates is None or _signatures is None:
        templates = load_templates()
        if not templates:
            raise ValueError(f"{TEMPLATES_PATH} contains no templates")
        texts = ["\n".join(t["example_prompts"]) for t in templates]
        vectors = _get_model().encode(texts, normalize_embeddings=True)
        _templates = templates
        _signatures = np.asarray(vectors, dtype=np.float64)
    return _templates, _signatures


def match(new_prompt: str) -> dict:
    """Find the stored template most similar to `new_prompt`.

    Returns a dict with matched, template_id, template_text, similarity,
    avg_tokens_saved, avg_turns_saved, language. When the best cosine
    similarity is below MATCH_THRESHOLD, matched is False and a message
    explains that this is a new, not-yet-learned task.
    """
    templates, signatures = _get_signatures()
    query = np.asarray(
        _get_model().encode([new_prompt], normalize_embeddings=True),
        dtype=np.float64,
    )[0]
    similarities = signatures @ query  # rows are normalized -> dot == cosine
    best_idx = int(np.argmax(similarities))
    best = templates[best_idx]
    similarity = float(similarities[best_idx])

    result = {
        "matched": similarity >= MATCH_THRESHOLD,
        "template_id": best["template_id"],
        "template_text": best["template_text"],
        "similarity": round(similarity, 4),
        "avg_tokens_saved": best["avg_tokens_saved"],
        "avg_turns_saved": best["avg_turns_saved"],
        "language": best["language"],
    }
    if not result["matched"]:
        result["message"] = NO_MATCH_MESSAGE
    return result


def match_to_json(new_prompt: str) -> str:
    """JSON-string version of match() so the dashboard can consume it."""
    return json.dumps(match(new_prompt))


def _print_result(prompt: str, result: dict) -> None:
    print(f'\nprompt: "{prompt[:90]}{"..." if len(prompt) > 90 else ""}"')
    if result["matched"]:
        print(
            f"  ✓ RECOGNIZED — suggests {result['template_id']} "
            f"(similarity {result['similarity']:.2f}), "
            f"saves ~{result['avg_turns_saved']:.1f} turns / "
            f"~{result['avg_tokens_saved']:,.0f} tokens"
        )
        print(f"    template: {result['template_text']}")
    else:
        print(
            f"  ◇ NEW TASK — no stored template matches "
            f"(sim {result['similarity']:.2f} < {MATCH_THRESHOLD})"
        )


def _self_test() -> None:
    """Match 3 prompts taken from the stored templates' own example_prompts.

    These prompts are part of the signatures themselves, so they SHOULD match --
    this proves the retrieval path works end to end.
    """
    templates, _ = _get_signatures()
    probes = [t["example_prompts"][0] for t in templates[:3]]
    print(f"[match] self-test: {len(probes)} prompts drawn from "
          f"{os.path.relpath(TEMPLATES_PATH, REPO_ROOT)} (all should match)")
    hits = 0
    for prompt in probes:
        result = match(prompt)
        _print_result(prompt, result)
        hits += result["matched"]
    print(f"\n[match] self-test done: {hits}/{len(probes)} matched")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        _print_result(prompt, match(prompt))
    else:
        _self_test()
