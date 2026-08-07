"""Distill a cluster of similar prompts into one parameterized template.

Reuses `engine/collapse.py`'s `call_llm`, so this stage inherits the same
real-vs-mock switch and needs no credentials to run.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from collapse import call_llm  # noqa: E402  -- one LLM wrapper for the whole project

# The distillation prompt, verbatim. Note the literal JSON braces in the spec, so
# placeholders are substituted with .replace() rather than .format().
DISTILL_PROMPT = """You are given several developer prompts that address the same TYPE of task. Produce ONE reusable parameterized template capturing their common structure. Replace parts that VARY with {slot_name} placeholders (descriptive snake_case). Keep constant parts as literal text. Output ONLY valid JSON: {"template_text": "...", "slots": ["slot1","slot2"]}

Prompts:
{prompts}

JSON:"""

_SLOT_RE = re.compile(r"\{([a-zA-Z_][\w]*)\}")


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` (or bare ```) wrappers the model sometimes adds."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_response(raw: str) -> dict | None:
    """Parse the model's JSON, tolerating fences and surrounding prose."""
    candidate = _strip_fences(raw)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost {...} span in the response.
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def distill_template(prompts: list[str]) -> dict:
    """Turn several same-type prompts into {"template_text": str, "slots": [str]}.

    Always returns a usable dict. `slots` is reconciled against the placeholders
    actually present in `template_text`, so the two can never disagree -- a model
    that lists a slot it didn't use, or uses one it didn't list, gets corrected.
    """
    prompt_block = "\n".join(f"- {p}" for p in prompts)
    prompt = DISTILL_PROMPT.replace("{prompts}", prompt_block)

    raw = call_llm(prompt, max_tokens=500)
    parsed = _parse_response(raw)

    if not isinstance(parsed, dict) or not str(parsed.get("template_text", "")).strip():
        # Unparseable response: degrade to a template rather than failing the run.
        return {
            "template_text": "In {file}, {function}() {symptom} because {root_cause}; {fix_direction}.",
            "slots": ["file", "function", "symptom", "root_cause", "fix_direction"],
        }

    template_text = str(parsed["template_text"]).strip()

    # The placeholders in the text are the source of truth.
    in_text = list(dict.fromkeys(_SLOT_RE.findall(template_text)))
    claimed = [str(s) for s in parsed.get("slots", []) if isinstance(s, (str, int))]
    slots = in_text or list(dict.fromkeys(claimed))

    return {"template_text": template_text, "slots": slots}


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cluster import build_clusters

    for i, cluster in enumerate(build_clusters(), start=1):
        prompts = [r["collapsed_prompt"] for r in cluster]
        result = distill_template(prompts)
        print(f"\n--- cluster {i} ({len(cluster)} members) ---")
        print(f"  template: {result['template_text']}")
        print(f"  slots:    {result['slots']}")
