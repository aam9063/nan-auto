"""Clef questionnaire v1 and the bounded `state` string (spec 6.2, 6.3).

Kept in one module so the router has a single definition of what it asks and
how much text it ships. Budgets are expressed in approximate tokens.
"""

import json
import re

QUESTION_IDS: tuple[str, ...] = ("task", "difficulty", "needs_reasoning", "expected_output_length")

HEAD_TOKENS = 600
TAIL_TOKENS = 200
SYSTEM_TOKENS = 150
CHARS_PER_TOKEN = 4  # cheap estimate: the exact tokenizer is not worth the latency

_CODE_FENCE = re.compile(r"```")


def estimate_tokens(text: str) -> int:
    """Approximate the token count of `text` without a tokenizer."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _clip_head(text: str, max_tokens: int) -> str:
    limit = max_tokens * CHARS_PER_TOKEN
    return text if len(text) <= limit else text[:limit]


def _clip_tail(text: str, max_tokens: int) -> str:
    limit = max_tokens * CHARS_PER_TOKEN
    return text if len(text) <= limit else text[-limit:]


def build_questions() -> dict[str, dict]:
    """Return questionnaire v1: four questions, one Clef call (spec 6.3)."""
    return {
        "task": {
            "type": "choice",
            "instructions": (
                "What kind of task is the user asking for in their last message? "
                "Pick the single best label."
            ),
            "criteria": {
                "chat_casual": "Small talk, greetings, opinions, no deliverable.",
                "factual_qa": "A factual question with a short, checkable answer.",
                "coding_generate": "Write new code, scripts or tests.",
                "coding_debug_or_review": "Diagnose, fix or review existing code.",
                "math_or_logic": "Mathematics, proofs or formal reasoning.",
                "summarize_or_extract": "Condense text or extract structured facts.",
                "translate_or_rewrite": "Translate or rephrase existing text.",
                "creative_writing": "Stories, poems, marketing copy or dialogue.",
                "agentic_planning": "Plan a multi-step process or orchestrate tools.",
                "other": "None of the above fits.",
            },
        },
        "difficulty": {
            "type": "score",
            "instructions": ("How hard is this task for a language model to answer correctly?"),
            "criteria": ["Trivial", "Easy", "Medium", "Hard", "Expert"],
        },
        "needs_reasoning": {
            "type": "noul",
            "instructions": (
                "Does answering correctly require multi-step reasoning rather than recall?"
            ),
            "criteria": {
                "true": "The answer requires chaining steps of reasoning.",
                "false": "The answer can be produced directly from knowledge or copying.",
            },
        },
        "expected_output_length": {
            "type": "score",
            "instructions": "How long will the expected answer be?",
            "criteria": ["Very short", "Short", "Medium", "Long"],
        },
    }


def build_state(
    last_user_message: str,
    *,
    system_prompt: str = "",
    turns: int = 1,
    language_hint: str = "",
) -> str:
    """Build the bounded `state` string sent to Clef.

    Clef accepts a JSON string; the prompt is clipped head and tail so a huge
    message cannot turn into decision latency (spec 6.2).
    """
    if not last_user_message.strip():
        raise ValueError("last_user_message must not be empty")

    payload: dict = {
        "last_user_message_head": _clip_head(last_user_message, HEAD_TOKENS),
        "last_user_message_tail": _clip_tail(last_user_message, TAIL_TOKENS),
        "meta": {
            "turns": turns,
            "has_code_fences": bool(_CODE_FENCE.search(last_user_message)),
        },
    }
    if system_prompt.strip():
        payload["system_prompt_head"] = _clip_head(system_prompt, SYSTEM_TOKENS)
    if language_hint:
        payload["meta"]["language_hint"] = language_hint
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
