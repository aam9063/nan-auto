"""The SQLite cache of the quality matrix (spec 11.1, 11.6).

One row per (prompt_id, model): what the model answered, how long it took,
how many tokens it cost, and — after judging — the quality score together
with the raw judge reply, so any score stays reproducible and auditable.

Only rows with an actual answer count as done work: an errored row is kept
for audit but never satisfies the resume check, so a re-run retries it
instead of silently shipping a hole in the matrix.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    prompt_id         TEXT NOT NULL,
    model             TEXT NOT NULL,
    latency_ms        REAL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    answer            TEXT,
    error             TEXT,
    quality_score     INTEGER,
    judge_raw         TEXT,
    judge_model       TEXT,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (prompt_id, model)
);
"""


@dataclass(frozen=True)
class Row:
    """One cached (prompt, model) measurement."""

    prompt_id: str
    model: str
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    answer: str | None
    error: str | None
    quality_score: int | None
    judge_raw: str | None
    judge_model: str | None
    created_at: str


def open_db(path: Path | str) -> sqlite3.Connection:
    """Open (and create, with schema) the matrix cache."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(_SCHEMA)
    db.commit()
    return db


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_result(
    db: sqlite3.Connection,
    prompt_id: str,
    model: str,
    *,
    latency_ms: float | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    answer: str | None,
    error: str | None = None,
) -> None:
    """Insert or replace the measurement for one (prompt, model) pair.

    Overwriting an answer resets the judgement columns: a new answer cannot
    keep the score of the answer it replaced.
    """
    db.execute(
        """
        INSERT INTO results (
            prompt_id, model, latency_ms, prompt_tokens, completion_tokens,
            answer, error, quality_score, judge_raw, judge_model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
        ON CONFLICT(prompt_id, model) DO UPDATE SET
            latency_ms = excluded.latency_ms,
            prompt_tokens = excluded.prompt_tokens,
            completion_tokens = excluded.completion_tokens,
            answer = excluded.answer,
            error = excluded.error,
            quality_score = NULL,
            judge_raw = NULL,
            judge_model = NULL,
            created_at = excluded.created_at
        """,
        (prompt_id, model, latency_ms, prompt_tokens, completion_tokens, answer, error, _now()),
    )
    db.commit()


def _row(connection_row: sqlite3.Row) -> Row:
    return Row(
        prompt_id=connection_row["prompt_id"],
        model=connection_row["model"],
        latency_ms=connection_row["latency_ms"],
        prompt_tokens=connection_row["prompt_tokens"],
        completion_tokens=connection_row["completion_tokens"],
        answer=connection_row["answer"],
        error=connection_row["error"],
        quality_score=connection_row["quality_score"],
        judge_raw=connection_row["judge_raw"],
        judge_model=connection_row["judge_model"],
        created_at=connection_row["created_at"],
    )


def get_result(db: sqlite3.Connection, prompt_id: str, model: str) -> Row | None:
    """The cached row for one (prompt, model) pair, or None."""
    cursor = db.execute(
        "SELECT * FROM results WHERE prompt_id = ? AND model = ?", (prompt_id, model)
    )
    found = cursor.fetchone()
    return _row(found) if found is not None else None


def answered_pairs(db: sqlite3.Connection) -> set[tuple[str, str]]:
    """Every (prompt_id, model) pair that already has a real answer."""
    cursor = db.execute(
        "SELECT prompt_id, model FROM results WHERE answer IS NOT NULL AND error IS NULL"
    )
    return {(row["prompt_id"], row["model"]) for row in cursor}


def rows_with_answer(db: sqlite3.Connection) -> int:
    cursor = db.execute("SELECT COUNT(*) FROM results WHERE answer IS NOT NULL AND error IS NULL")
    return int(cursor.fetchone()[0])


def all_rows(db: sqlite3.Connection) -> list[Row]:
    cursor = db.execute("SELECT * FROM results ORDER BY prompt_id, model")
    return [_row(row) for row in cursor]


def set_judgement(
    db: sqlite3.Connection,
    prompt_id: str,
    model: str,
    *,
    score: int | None,
    raw: str,
    judge_model: str,
) -> None:
    """Record one judge verdict with its raw reply, for reproducible audits."""
    db.execute(
        """
        UPDATE results
        SET quality_score = ?, judge_raw = ?, judge_model = ?
        WHERE prompt_id = ? AND model = ?
        """,
        (score, raw, judge_model, prompt_id, model),
    )
    db.commit()
