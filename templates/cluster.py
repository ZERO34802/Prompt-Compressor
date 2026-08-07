"""Cluster collapsed prompts by task type.

Embeds each collapsed prompt locally (sentence-transformers all-MiniLM-L6-v2, no
API) and groups them with cosine agglomerative clustering. Only clusters with at
least MIN_CLUSTER_SIZE members are kept -- a template distilled from one or two
prompts isn't a pattern, it's a coincidence.

If the embedding model can't be loaded (no network on first run, package
missing), falls back to TF-IDF vectors so the stage still produces clusters
offline. The fallback is reported, never silent.
"""

from __future__ import annotations

import json
import os

import numpy as np
from sklearn.cluster import AgglomerativeClustering

EMBED_MODEL = "all-MiniLM-L6-v2"

# Cosine distance ceiling for merging. The spec's starting point was 0.5, but on
# this corpus the *closest* pair of collapsed prompts sits at 0.58 -- 15 genuinely
# different bug types don't embed close together -- so 0.5 produces 15 singletons
# and zero templates. 0.75 yields 3 coherent clusters covering 13/15 prompts.
# `build_clusters` also relaxes automatically if a corpus needs more room.
DISTANCE_THRESHOLD = 0.75
RELAX_LADDER = (0.75, 0.80, 0.85)
MIN_CLUSTER_SIZE = 3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_PATH = os.path.join(REPO_ROOT, "data", "results.jsonl")


def load_results(path: str = RESULTS_PATH) -> list[dict]:
    """Read data/results.jsonl into a list of row dicts."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found -- run `python engine/run.py --limit 15` first"
        )
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def embed(prompts: list[str]) -> np.ndarray:
    """Embed prompts locally. Returns an (n, d) float array."""
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(EMBED_MODEL)
        vectors = model.encode(prompts, normalize_embeddings=True)
        print(f"[cluster] embedded {len(prompts)} prompts with {EMBED_MODEL}")
        return np.asarray(vectors, dtype=np.float64)
    except Exception as exc:  # noqa: BLE001
        print(f"[cluster] {EMBED_MODEL} unavailable ({type(exc).__name__}: {exc}); "
              f"falling back to TF-IDF")
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectors = TfidfVectorizer(stop_words="english", sublinear_tf=True).fit_transform(prompts)
        return np.asarray(vectors.todense(), dtype=np.float64)


def _labels_at(vectors: np.ndarray, distance_threshold: float) -> np.ndarray:
    model = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=distance_threshold,
    )
    return model.fit_predict(vectors)


def cluster_prompts(
    prompts: list[str],
    distance_threshold: float = DISTANCE_THRESHOLD,
) -> np.ndarray:
    """Return a cluster label per prompt via cosine agglomerative clustering."""
    if len(prompts) < 2:
        return np.zeros(len(prompts), dtype=int)
    return _labels_at(embed(prompts), distance_threshold)


def _group(labels: np.ndarray, rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for label, row in zip(labels, rows):
        grouped.setdefault(int(label), []).append(row)
    return grouped


def build_clusters(
    rows: list[dict] | None = None,
    distance_threshold: float | None = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> list[list[dict]]:
    """Group result rows into qualifying clusters, largest first.

    Each returned cluster is a list of the original result rows, so downstream
    stages have the savings figures and language alongside the prompt text.

    Embeds once, then walks RELAX_LADDER and stops at the tightest threshold that
    yields at least one cluster of `min_cluster_size` -- so a tightly-clustered
    corpus gets tight templates and a diverse one still gets templates at all.
    Pass `distance_threshold` explicitly to pin a single value.
    """
    if rows is None:
        rows = load_results()
    if len(rows) < min_cluster_size:
        print(f"[cluster] only {len(rows)} rows -- fewer than min_cluster_size={min_cluster_size}")
        return []

    prompts = [r["collapsed_prompt"] for r in rows]
    vectors = embed(prompts)

    ladder = (distance_threshold,) if distance_threshold is not None else RELAX_LADDER

    grouped: dict[int, list[dict]] = {}
    used = ladder[-1]
    for threshold in ladder:
        grouped = _group(_labels_at(vectors, threshold), rows)
        used = threshold
        if any(len(c) >= min_cluster_size for c in grouped.values()):
            break
        if threshold != ladder[-1]:
            print(f"[cluster] no cluster reached {min_cluster_size} members at "
                  f"distance_threshold={threshold}; relaxing")

    kept = [c for c in grouped.values() if len(c) >= min_cluster_size]
    kept.sort(key=len, reverse=True)

    covered = sum(len(c) for c in kept)
    print(f"[cluster] distance_threshold={used}: {len(grouped)} raw clusters -> "
          f"{len(kept)} with >= {min_cluster_size} members "
          f"({len(grouped) - len(kept)} dropped as too small, "
          f"{covered}/{len(rows)} prompts covered)")
    return kept


if __name__ == "__main__":
    rows = load_results()
    clusters = build_clusters(rows)
    for i, cluster in enumerate(clusters, start=1):
        langs = {r["language"] for r in cluster}
        print(f"\n--- cluster {i}: {len(cluster)} members {sorted(langs)} ---")
        for row in cluster:
            print(f"  [{row['conv_id']}] {row['collapsed_prompt'][:100]}")
