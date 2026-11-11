"""Answer verification: every figure in the reply must trace back to a tool result or the user's own message."""
import re

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(n):
    n = n.replace(",", "").rstrip(".")
    return n.lstrip("0") or "0"


def _nums(text):
    return {_norm(m) for m in NUM.findall(text)}


def grounding_issues(reply, facts, user_texts):
    allowed = set()
    for t in [*facts, *user_texts]:
        allowed |= _nums(t)
    return sorted(n for n in _nums(reply) if len(n.replace(".", "")) >= 2 and n not in allowed)


def repair_prompt(issues):
    return ("Verification failed: your answer contains figures that do not appear in any tool result or in the "
            f"user's message: {', '.join(issues)}. Re-check with the tools, or restate the answer using only "
            "figures returned by tools. Do not guess or compute new numbers.")
