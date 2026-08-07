"""Conversation loading.

`load_conversations()` returns the hand-written seed corpus by default. Passing
`use_hf=True` attempts to pull a real HuggingFace coding-chat dataset and map it
into the same shape, falling back to the seed on *any* failure -- missing
package, no network, unexpected schema. The seed path never touches the network
and has no dependencies beyond the standard library.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seed import make_seed  # noqa: E402


# Candidate HF datasets, tried in order. Each entry is (repo_id, config, split).
# "CodeChat" is the dataset the brief named; it 404s on the Hub, so Code-Feedback
# is the working multi-turn stand-in -- same {role, content} message shape.
_HF_CANDIDATES = [
    ("nreHieW/CodeChat", None, "train"),
    ("m-a-p/Code-Feedback", None, "train"),
]

_LANGUAGE_HINTS = {
    "python": ("python", "def ", "import ", ".py", "pytest", "django", "flask", "pandas"),
    "javascript": ("javascript", "typescript", "node", "const ", "=>", ".js", ".jsx", "npm", "react"),
}


def _guess_language(text: str) -> str:
    """Cheap keyword vote so HF rows get a usable `language` field."""
    low = text.lower()
    scores = {
        lang: sum(low.count(hint) for hint in hints)
        for lang, hints in _LANGUAGE_HINTS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "python"


def _normalize_turns(raw) -> list[dict]:
    """Coerce a HF row's message list into [{'role','content'}, ...].

    Accepts the common variants: 'from'/'value' (ShareGPT), 'role'/'content'
    (OpenAI), and maps assistant aliases onto 'assistant'.
    """
    turns = []
    for msg in raw or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or msg.get("from") or ""
        content = msg.get("content") or msg.get("value") or ""
        role = str(role).strip().lower()
        if role in ("human", "user", "prompter"):
            role = "user"
        elif role in ("gpt", "assistant", "bot", "chatgpt", "model"):
            role = "assistant"
        else:
            continue  # drop system turns -- they aren't part of the billed dialogue
        content = str(content).strip()
        if content:
            turns.append({"role": role, "content": content})
    return turns


def _load_from_hf(limit: int) -> list[dict]:
    """Try each candidate dataset; raise if none yields usable conversations."""
    from datasets import load_dataset  # imported lazily so seed path needs nothing

    last_error: Exception | None = None
    for repo_id, config, split in _HF_CANDIDATES:
        try:
            ds = load_dataset(repo_id, config, split=split, streaming=True)
            convos: list[dict] = []
            for i, row in enumerate(ds):
                if len(convos) >= limit:
                    break
                raw = (
                    row.get("conversations")
                    or row.get("messages")
                    or row.get("conversation")
                )
                turns = _normalize_turns(raw)
                # Only keep genuine multi-turn dialogues that end on the assistant.
                if len(turns) < 4 or turns[-1]["role"] != "assistant":
                    continue
                blob = "\n".join(t["content"] for t in turns)
                convos.append(
                    {
                        "conv_id": f"hf-{i:05d}",
                        "language": _guess_language(blob),
                        "turns": turns,
                    }
                )
            if convos:
                return convos
        except Exception as exc:  # noqa: BLE001 -- any failure means try the next one
            last_error = exc
            continue
    raise RuntimeError(f"no HF candidate yielded conversations (last error: {last_error})")


def load_conversations(limit: int = 100, use_hf: bool = False) -> list[dict]:
    """Return up to `limit` conversations as {conv_id, language, turns}.

    Always succeeds: the HF branch is entirely best-effort and falls back to the
    offline seed corpus on any exception.
    """
    if use_hf:
        try:
            convos = _load_from_hf(limit)
            print(f"[load_data] loaded {len(convos)} conversations from HuggingFace")
            return convos
        except Exception as exc:  # noqa: BLE001 -- silent fallback is the contract
            print(f"[load_data] HuggingFace unavailable ({type(exc).__name__}); using seed corpus")

    return make_seed()[:limit]


if __name__ == "__main__":
    for use_hf in (False, True):
        convos = load_conversations(limit=15, use_hf=use_hf)
        print(f"use_hf={use_hf}: {len(convos)} conversations, "
              f"first={convos[0]['conv_id']}, turns={len(convos[0]['turns'])}")
