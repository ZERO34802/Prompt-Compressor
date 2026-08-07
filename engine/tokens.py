"""Token counting and honest multi-turn billing.

The billing model here is the crux of the whole argument, so it is worth being
explicit about it. In a real chat session every request re-sends the entire
transcript so far as input. So a 6-turn conversation does not bill the transcript
once -- it bills a growing prefix of it, once per assistant reply:

    assistant turn 1  ->  input = turn 0
    assistant turn 2  ->  input = turns 0..2
    assistant turn 3  ->  input = turns 0..4

That quadratic-ish growth is what makes collapsing a conversation valuable, and
counting it this way keeps the savings figure defensible rather than inflated.
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken

ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _encoder():
    """cl100k_base encoder, loaded once and reused across threads."""
    return tiktoken.get_encoding(ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Number of cl100k_base tokens in `text`."""
    if not text:
        return 0
    return len(_encoder().encode(text))


def billed_cost(turns: list[dict]) -> dict:
    """Model real multi-turn billing, including re-sent context.

    For every assistant turn, the input is every preceding turn in the
    conversation and the output is that turn's own content. Summed over all
    assistant turns.

    Returns {"billed_input_tokens", "billed_output_tokens", "billed_total_tokens"}.
    """
    per_turn = [count_tokens(t.get("content", "")) for t in turns]

    billed_input = 0
    billed_output = 0

    for i, turn in enumerate(turns):
        if turn.get("role") != "assistant":
            continue
        billed_input += sum(per_turn[:i])   # the whole transcript re-sent as context
        billed_output += per_turn[i]        # what the model generated this turn

    return {
        "billed_input_tokens": billed_input,
        "billed_output_tokens": billed_output,
        "billed_total_tokens": billed_input + billed_output,
    }


if __name__ == "__main__":
    from seed import make_seed

    assert count_tokens("") == 0
    assert count_tokens("hello world") > 0

    # Hand-checkable case: one assistant reply bills only the single user turn.
    simple = [{"role": "user", "content": "abc"}, {"role": "assistant", "content": "def"}]
    got = billed_cost(simple)
    assert got["billed_input_tokens"] == count_tokens("abc"), got
    assert got["billed_output_tokens"] == count_tokens("def"), got

    # Two assistant replies: the first user turn is billed twice (re-sent context).
    four = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    t = [count_tokens(x["content"]) for x in four]
    got = billed_cost(four)
    assert got["billed_input_tokens"] == t[0] + (t[0] + t[1] + t[2]), got
    assert got["billed_output_tokens"] == t[1] + t[3], got
    print("billing unit checks passed")

    for convo in make_seed()[:5]:
        raw = sum(count_tokens(t["content"]) for t in convo["turns"])
        b = billed_cost(convo["turns"])
        print(f"  {convo['conv_id']}  turns={len(convo['turns'])}  raw={raw:5d}  "
              f"billed={b['billed_total_tokens']:6d}  "
              f"(in={b['billed_input_tokens']}, out={b['billed_output_tokens']})  "
              f"multiplier={b['billed_total_tokens'] / raw:.2f}x")
