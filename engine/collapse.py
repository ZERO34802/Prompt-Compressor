"""Conversation collapsing -- the core IP.

`collapse_conversation` takes a multi-turn conversation and returns the single
specific prompt the developer *should* have opened with. Everything routes
through one swappable wrapper, `call_llm`, so the rest of the pipeline never
touches a provider SDK directly.

Running without credentials
---------------------------
`USE_REAL_LLM` below is the single switch. It defaults to "real if a key is
present, deterministic mock otherwise", so the whole pipeline runs end-to-end
offline. Set it to a hard `True`/`False` to pin the behaviour.

The mock is not a stub that returns a constant -- it synthesises a plausible
one-shot prompt by regex-extracting the concrete details (file names, function
names, exception types, line numbers, fix direction) out of the last user and
last assistant turns and templating them. That makes the downstream stages
(clustering, distillation) exercise realistic, varied, *clusterable* text rather
than 15 identical strings.
"""

from __future__ import annotations

import json
import os
import re

# ---------------------------------------------------------------------------
# THE SWITCH. Flip to True once ANTHROPIC_API_KEY is set (or just export the key
# and leave this alone -- the default auto-detects). Set to False to force the
# offline mock even when a key is available.
# ---------------------------------------------------------------------------
USE_REAL_LLM: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-haiku-4-5-20251001"

# The collapse prompt, verbatim. {conversation_text} is the only placeholder.
COLLAPSE_PROMPT = """You are analyzing a multi-turn conversation between a developer and a coding assistant. It started vague and became specific over several turns until the task was resolved. Write the SINGLE specific first prompt the developer SHOULD have written to resolve this in ONE turn — including every detail that only emerged later (file names, line numbers, function names, root cause, fix direction). Output ONLY the ideal prompt text. Be concrete. 1-3 sentences max. Do not include the solution.

Conversation:
{conversation_text}

Ideal one-shot prompt:"""


# ---------------------------------------------------------------------------
# LLM wrapper
# ---------------------------------------------------------------------------

_client = None


def _anthropic_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def call_llm(prompt: str, max_tokens: int = 300) -> str:
    """Single swappable LLM entry point for the whole project.

    Returns the model's text. When `USE_REAL_LLM` is False, returns a
    deterministic mock response derived from the prompt (see `_mock_llm`) so no
    stage of the pipeline is blocked on credentials.
    """
    if not USE_REAL_LLM:
        return _mock_llm(prompt, max_tokens=max_tokens)

    client = _anthropic_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


# ---------------------------------------------------------------------------
# Deterministic offline mock
# ---------------------------------------------------------------------------

_FILE_RE = re.compile(r"\b((?:[\w.\-]+/)*[\w.\-]+\.(?:py|js|jsx|mjs|ts|tsx))\b")
# Require the opening paren -- otherwise prose like "define a function with the
# following signature" yields a function named "with".
_DEF_RE = re.compile(r"\b(?:async\s+)?(?:def|function)\s+([a-zA-Z_]\w*)\s*\(")
_ASSIGN_FN_RE = re.compile(
    r"\b(?:exports\.|module\.exports\.|const\s+|let\s+|var\s+)([a-zA-Z_]\w*)\s*=\s*(?:async\s*)?\("
)
_FUNC_RE = re.compile(r"\b([a-zA-Z_]\w*)\s*\(\)")
_ERROR_RE = re.compile(r"\b([A-Z]\w*(?:Error|Exception))\b")
_LINE_RE = re.compile(r"\bline\s+(\d+)\b", re.IGNORECASE)
_STATUS_RE = re.compile(r"\b(?:returns?|gets?|with)\s+(?:a\s+)?([45]\d{2})\b")

# Words that look like calls but never name the function under discussion.
_FUNC_STOPWORDS = {
    "return", "if", "for", "while", "switch", "catch", "function", "async",
    "await", "print", "len", "str", "int", "dict", "list", "require", "import",
    # chained methods that show up inside the pasted snippet but aren't the subject
    "json", "then", "map", "fetch", "all", "push", "log", "next", "copy", "keys",
}

# Root-cause signatures, checked in order. Each maps a phrase that shows up in the
# assistant's explanation onto (root_cause, fix_direction).
_CAUSE_SIGNATURES: list[tuple[str, str, str]] = [
    ("naive local datetime", "it compares a timezone-aware value against a naive local datetime",
     "compare against datetime.now(timezone.utc)"),
    ("offset-naive", "it compares a timezone-aware value against a naive local datetime",
     "compare against datetime.now(timezone.utc)"),
    ("dropna", "dropna() with no subset drops rows with a null in any column",
     "scope the drop with subset="),
    ("mutable default", "the mutable default argument is shared across all calls",
     "use None as the sentinel and allocate inside the function"),
    ("default `[]`", "the mutable default argument is shared across all calls",
     "use None as the sentinel and allocate inside the function"),
    ("n+1", "each loop iteration lazily triggers another query",
     "batch the related fetches with select_related/prefetch_related"),
    ("visibility timeout", "the job outlives the queue visibility timeout so a second worker picks it up",
     "raise the timeout and add an idempotency guard"),
    ("promise.all", "unbounded concurrency opens one socket per item at once",
     "bound it with a worker pool"),
    ("res.json(orders)", "the response is sent before the async query resolves",
     "await the query and return its rows"),
    ("runs synchronously, before", "the response is sent before the async query resolves",
     "await the query and return its rows"),
    ("object literal on every render", "a new object identity every render invalidates the effect dependency",
     "depend on primitive values or memoise the object"),
    ("fresh object literal", "a new object identity every render invalidates the effect dependency",
     "depend on primitive values or memoise the object"),
    ("scope='module'", "the module-scoped fixture is shared mutable state across tests",
     "make the fixture function-scoped"),
    ("preventdefault", "the native form submission is not prevented",
     "handle onSubmit and call preventDefault"),
    ("entire upload into", "the whole payload is read into memory before upload",
     "stream it with a multipart upload"),
    ("only flush", "the final unterminated segment is never flushed",
     "flush the remaining buffer after the loop"),
    ("off-by-one", "the final unterminated segment is never flushed",
     "flush the remaining buffer after the loop"),
    ("local zone", "local-time getters are used on a UTC instant",
     "format the date in UTC"),
    ("applies to every", "the package-wide module type reclassifies existing CommonJS files",
     "convert the file to ESM or scope the change to one file"),
    ("unique constraint", "the database unique-violation error escapes the handler uncaught",
     "catch the violation and return a 409"),
    ("nothing catches it", "the rejected promise escapes the handler uncaught",
     "catch it and map it to the right status code"),
]

_SYMPTOMS = [
    (("off by one day", "off-by-one day", "dates are off"), "computes the wrong calendar day"),
    (("re-render", "freezes"), "re-renders infinitely"),
    (("oom", "killed", "out of memory"), "gets OOM killed"),
    (("reload",), "reloads the page instead of submitting"),
    (("empty array", "empty result"), "returns an empty result"),
    (("every other record", "accumulates"), "leaks state between calls"),
    (("times out", "timed out", "etimedout"), "times out"),
    (("500", "502", "503"), "returns the wrong status code"),
    (("twice", "duplicate emails"), "processes the same work twice"),
    (("lose the last", "loses the last", "drops the last", "off-by-one"), "drops the last element"),
    (("too low", "wrong"), "produces wrong output"),
]


def _turns_by_role(text: str, role: str) -> list[str]:
    """Pull every USER:/ASSISTANT: block out of a rendered conversation_text."""
    blocks = re.split(r"^(USER|ASSISTANT):\s*", text, flags=re.MULTILINE)
    # re.split with a capturing group yields [pre, tag, body, tag, body, ...]
    pairs = list(zip(blocks[1::2], blocks[2::2]))
    return [body.strip() for tag, body in pairs if tag == role]


def _last_turn(text: str, role: str) -> str:
    turns = _turns_by_role(text, role)
    return turns[-1] if turns else ""


def _first(pattern: re.Pattern, *texts: str) -> str | None:
    for text in texts:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def _pick_function(*texts: str, strict: bool = False) -> str | None:
    """Prefer a real declaration over an incidental `something()` mention.

    `strict=True` accepts only genuine declarations. Prose is full of things that
    look like calls once you allow bare `word()`, so the loose pattern is only
    safe when the text is known to be a code paste.
    """
    for pattern in (_DEF_RE, _ASSIGN_FN_RE):
        name = _first(pattern, *texts)
        if name and name.lower() not in _FUNC_STOPWORDS:
            return name
    if strict:
        return None
    for text in texts:
        for name in _FUNC_RE.findall(text):
            if name.lower() not in _FUNC_STOPWORDS and len(name) > 2:
                return name
    return None


_FALLBACK_CAUSE = "the root cause only surfaced after several rounds of back-and-forth"


def _pick_cause(assistant_text: str) -> tuple[str, str]:
    low = assistant_text.lower()
    for needle, cause, fix in _CAUSE_SIGNATURES:
        if needle.lower() in low:
            return cause, fix
    return _FALLBACK_CAUSE, "correct the faulty logic"


def _pick_symptom(user_text: str, assistant_text: str) -> str:
    low = (user_text + " " + assistant_text).lower()
    for needles, symptom in _SYMPTOMS:
        if any(n in low for n in needles):
            return symptom
    return "misbehaves"


_CONSTRAINT_RE = re.compile(
    r"((?:without using|without|must|should|ensure that|make sure|handle|in O\([^)]+\))[^.;\n]{0,90})",
    re.IGNORECASE,
)
_LANG_HINTS = (
    "python", "javascript", "typescript", "ruby", "java", "c++", "c#", "go",
    "rust", "php", "sql", "scala", "swift", "kotlin", "html", "css", "bash",
)


def _trim_words(text: str, limit: int) -> str:
    """Truncate to `limit` chars without cutting a word in half."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,;.")


def _guess_lang(text: str) -> str | None:
    low = text.lower()
    for lang in _LANG_HINTS:
        if lang in low:
            return lang
    return None


def _mock_task_collapse(conversation_text: str) -> str:
    """Fallback shape for conversations that build something rather than fix a bug.

    Not every multi-turn thread is a bug report -- plenty are "write X", then
    several rounds of refinement. For those, the ideal one-shot prompt is the
    original objective plus the constraints and structure that only emerged later.
    """
    users = _turns_by_role(conversation_text, "USER")
    first_user = users[0] if users else ""
    last_assistant = _last_turn(conversation_text, "ASSISTANT")
    all_user = "\n".join(users)

    # The objective is the developer's opening ask, first sentence only, with the
    # boilerplate lead-in peeled off so it reads as a noun phrase.
    objective = re.split(r"(?<=[.?!])\s", first_user.strip())[0].strip()
    objective = re.sub(r"^(?:please|kindly|hi[,!]?|hello[,!]?)\s+", "", objective, flags=re.IGNORECASE)
    objective = re.sub(
        r"^you are tasked with\s+(?:implementing|writing|creating|building)\s+"
        r"(?:an?|the)?\s*(?:function|method|class|program|script)?\s*(?:that|which|to)?\s*",
        "", objective, flags=re.IGNORECASE,
    )
    objective = re.sub(r"^(?:write|create|implement|build|design|generate|develop)\s+"
                       r"(?:an\b|a\b|the\b)?\s*", "", objective, flags=re.IGNORECASE)
    # "Ruby code to convert ...", "Python program that illustrates ..." -> drop the lead-in.
    objective = re.sub(
        r"^(?:" + "|".join(_LANG_HINTS).replace("+", r"\+") + r")\s+"
        r"(?:code|program|script|function|snippet|application|class|module|library|tool)\s+"
        r"(?:to|that|which|for)\s+",
        "", objective, flags=re.IGNORECASE,
    )
    objective = re.sub(r"^(?:code|program|script|function)\s+(?:to|that|which|for)\s+",
                       "", objective, flags=re.IGNORECASE)
    objective = _trim_words(re.sub(r"\s+", " ", objective).rstrip(". "), 180)
    objective = objective or "the requested functionality"

    lang = _guess_lang(first_user) or _guess_lang(last_assistant) or "the target language"
    # Prose only -- accept a name only if it is genuinely declared in the answer.
    func = _pick_function(last_assistant, strict=True)

    # Keep the two most specific constraints; dedupe while preserving order.
    seen: list[str] = []
    for match in _CONSTRAINT_RE.findall(all_user):
        c = _trim_words(re.sub(r"\s+", " ", match).strip().rstrip(".,;"), 90)
        if len(c) > 12 and c.lower() not in {s.lower() for s in seen}:
            seen.append(c)

    # Colon rather than "to"/"for": the extracted objective may be a noun phrase
    # ("an array using numpy...") or a verb phrase ("convert a linked list..."),
    # and a colon reads correctly for both.
    body = f"Write {lang} code for: {objective}"
    if func:
        body += f", exposing it as {func}()"
    body += "."
    if seen:
        body += f" Constraints: {'; '.join(seen[:2])}."
    body += " Include the edge cases and error handling I would otherwise have to ask for in follow-ups."
    return body


def _mock_collapse(conversation_text: str) -> str:
    """Synthesise a plausible ideal one-shot prompt from the conversation's tail.

    Deterministic: same conversation in, same prompt out. Templates are chosen by
    which concrete signals are present, which means conversations with the same
    bug shape produce structurally similar prompts -- exactly what the clustering
    stage needs to find.
    """
    last_user = _last_turn(conversation_text, "USER")
    last_assistant = _last_turn(conversation_text, "ASSISTANT")
    # Concrete identifiers come from the final specific exchange, but the symptom
    # is usually stated a turn or two earlier -- before the developer pastes code
    # -- so scan all of their turns for that one.
    all_user = "\n".join(_turns_by_role(conversation_text, "USER"))

    filename = _first(_FILE_RE, last_user, last_assistant)
    # Only the developer's own paste -- the assistant's turn contains the *fix*,
    # and pulling a name out of that would credit a helper that doesn't exist yet.
    func = _pick_function(last_user)
    error = _first(_ERROR_RE, last_user)      # the exception the developer actually reported
    line = _first(_LINE_RE, last_user, last_assistant)
    status = _first(_STATUS_RE, all_user)

    cause, fix = _pick_cause(last_assistant)
    symptom = _pick_symptom(all_user, "")

    # If none of the debugging signals landed, this thread is building something
    # rather than fixing something. "The affected module misbehaves because the
    # root cause only surfaced later" would say nothing, so switch templates.
    # A symptom keyword alone is too weak -- it fires on prose. Require a concrete
    # anchor: a real file, a reported exception, or a recognised root cause.
    if not (filename or error or cause != _FALLBACK_CAUSE):
        return _mock_task_collapse(conversation_text)

    filename = filename or "the affected module"
    where = f"In {filename}"
    if line:
        where += f" line {line}"

    target = f"{func}()" if func else "the affected function"

    if error:
        body = (
            f"{where}, {target} raises {error} because {cause}; "
            f"{fix} and keep the existing behaviour for the passing cases."
        )
    elif status:
        body = (
            f"{where}, {target} returns a {status} instead of a proper error response "
            f"because {cause}; {fix}."
        )
    else:
        body = (
            f"{where}, {target} {symptom} because {cause}; "
            f"{fix} without changing the function's signature."
        )

    return body


def _mock_distill(prompt: str) -> str:
    """Return valid JSON for the distillation prompt.

    Generic-but-real template: the slot set is derived from which signals
    actually appear across the cluster's prompts, so `slots` is never empty and
    `template_text` genuinely reflects the shared structure.
    """
    body = prompt.split("Prompts:", 1)[-1]

    slots = ["file", "function", "root_cause", "fix_direction"]
    if _ERROR_RE.search(body):
        slots.insert(2, "error_type")
    if _LINE_RE.search(body):
        slots.insert(1, "line_number")
    if _STATUS_RE.search(body):
        slots.insert(2, "status_code")

    pieces = ["In {file}"]
    if "line_number" in slots:
        pieces.append(" line {line_number}")
    pieces.append(", {function}() ")
    if "error_type" in slots:
        pieces.append("raises {error_type} ")
    elif "status_code" in slots:
        pieces.append("returns {status_code} ")
    else:
        pieces.append("misbehaves ")
    pieces.append("because {root_cause}; {fix_direction}.")

    return json.dumps({"template_text": "".join(pieces), "slots": slots})


def _mock_llm(prompt: str, max_tokens: int = 300) -> str:
    """Route a prompt to the right deterministic mock by recognising its shape."""
    if prompt.rstrip().endswith("Ideal one-shot prompt:"):
        conversation_text = prompt.split("Conversation:", 1)[-1]
        conversation_text = conversation_text.rsplit("Ideal one-shot prompt:", 1)[0]
        return _mock_collapse(conversation_text)

    if prompt.rstrip().endswith("JSON:"):
        return _mock_distill(prompt)

    return "[mock] no handler for this prompt shape"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_conversation(convo: dict) -> str:
    """Render turns as 'USER: ...\\nASSISTANT: ...\\n'."""
    return "".join(
        f"{turn['role'].upper()}: {turn['content']}\n" for turn in convo["turns"]
    )


def collapse_conversation(convo: dict) -> str:
    """Return the ideal single prompt that would have resolved `convo` in one turn."""
    conversation_text = render_conversation(convo)
    prompt = COLLAPSE_PROMPT.replace("{conversation_text}", conversation_text)
    return call_llm(prompt, max_tokens=300).strip()


if __name__ == "__main__":
    from seed import make_seed

    print(f"USE_REAL_LLM = {USE_REAL_LLM}  (model={MODEL})\n")
    for convo in make_seed():
        collapsed = collapse_conversation(convo)
        print(f"{convo['conv_id']} [{convo['language']}]\n  {collapsed}\n")
