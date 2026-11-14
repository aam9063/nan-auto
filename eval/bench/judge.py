"""The quality judge (NAU-14-T4, spec 11.1).

One stored answer at a time is scored against a fixed rubric on a documented
0-4 scale. The raw judge reply is kept in the cache next to the parsed score,
so every judgement is reproducible and auditable: anyone can re-read the raw
reply and re-apply `parse_score` to check the number.

Limitations, stated honestly: the judge is a single model accessed through the
same API with its own budget, and a single judge carries its own family bias —
scores are relative to this one judge, not an absolute truth, and the 10%
human re-review is out of scope (documented in eval/README.md).
"""

import re
import sqlite3
from collections.abc import Sequence

from bench.cache import all_rows, set_judgement
from bench.matrix import ChatCall, call_with_backoff

DEFAULT_JUDGE_MODEL = "glm5.3"  # top card tier (t3_max); the most capable measured model

# The documented scale. Anchors are fixed text; the judge prompt is built from
# this table, so the scale can never drift between runs.
RUBRIC: dict[str, object] = {
    "scale": {0: "unusable", 1: "poor", 2: "acceptable", 3: "good", 4: "excellent"},
    "anchors": {
        0: "Empty, refuses, or answers a question that was not asked; unusable.",
        1: "Mostly wrong or off-topic; the user would have to ask again.",
        2: "Partially correct with notable gaps or errors.",
        3: "Correct and complete for what was asked.",
        4: "Correct, complete, well structured, and concise.",
    },
}

# The judge must answer with the exact line `Score: N` (N in 0..4). Anything
# else parses to None and the answer stays unjudged rather than guessed.
_SCORE = re.compile(r"(?i)\bscore\s*[:=\-]?\s*([0-4])(?![0-9])")


def parse_score(raw: str) -> int | None:
    """The 0-4 score a judge reply states, or None when it states none.

    Only an explicit `Score:` line counts: a bare digit inside the prose
    ("the answer is 7 because 3 + 4") is never mistaken for a verdict.
    """
    match = _SCORE.search(raw)
    return int(match.group(1)) if match is not None else None


def judge_message(prompt: str, answer: str, *, task: str) -> str:
    """Build the judge prompt; the same inputs always build the same text."""
    scale = RUBRIC["scale"]
    anchors = RUBRIC["anchors"]
    lines = [
        "You are scoring the quality of one model answer, for a benchmark.",
        "Use exactly this scale and nothing else:",
        *(
            f"  {level} ({scale[level]}): {anchors[level]}"  # type: ignore[index]
            for level in sorted(anchors)  # type: ignore[union-attr]
        ),
        "",
        f"Task label: {task}",
        "",
        "User prompt:",
        prompt,
        "",
        "Answer to score:",
        answer,
        "",
        "Reply with exactly one line: Score: N  (N is a single digit 0-4).",
    ]
    return "\n".join(lines)


async def score_pending(
    db: sqlite3.Connection,
    *,
    prompts: Sequence,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    call: ChatCall,
    max_retries: int = 4,
    backoff_s: float = 0.5,
) -> int:
    """Judge every cached answer that has no score yet; return how many judged.

    Sequential on purpose: the judge works through its own budget and the
    volume is small (prompts x models). Rows without an answer, and rows whose
    prompt is not in `prompts`, are skipped. An unparseable judge reply keeps
    its raw text but no score, so it is retried on the next pass.
    """
    text_of = {intent.id: intent.prompt for intent in prompts}
    task_of = {intent.id: intent.task for intent in prompts}
    pending = [
        row
        for row in all_rows(db)
        if row.answer is not None
        and row.quality_score is None
        and row.prompt_id in text_of
    ]
    judged = 0
    for row in pending:
        result = await call_with_backoff(
            call,
            judge_message(text_of[row.prompt_id], row.answer, task=task_of[row.prompt_id]),
            judge_model,
            max_retries=max_retries,
            backoff_s=backoff_s,
        )
        raw = result.answer or ""
        score = parse_score(raw)
        set_judgement(
            db, row.prompt_id, row.model, score=score, raw=raw, judge_model=judge_model
        )
        if score is not None:
            judged += 1
    return judged
