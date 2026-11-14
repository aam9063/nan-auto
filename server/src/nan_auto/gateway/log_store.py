"""Decision log store: what the router decided and why, never what was said.

NAU-13, spec 9 (tables) and spec 12 (privacy). The repository persists ids,
enums, numbers, the Clef answers (label, value, confidence) and timings —
never the user's prompt, the response body or an API key. The guard is
structural: no public function here accepts free-form text, so there is no
parameter a later edit could accidentally feed the prompt into, and the
conversation enters only as the caller-supplied hash of the conversation key,
because the client chooses that header and it could carry user text.
"""

import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite

__all__ = [
    "Attempt",
    "ClefAnswer",
    "DecisionLogEntry",
    "DecisionLogError",
    "DecisionLogStore",
    "DecisionRecord",
    "StoredDecision",
]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS REQUEST_LOG (
        id TEXT PRIMARY KEY,
        conversation_hash TEXT NOT NULL,
        profile TEXT NOT NULL,
        chosen_model TEXT,
        reasoning_level TEXT NOT NULL,
        decision_source TEXT NOT NULL,
        clef_timeout INTEGER NOT NULL,
        clef_latency_ms INTEGER NOT NULL,
        ttft_ms INTEGER,
        total_ms INTEGER,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS DECISION_ANSWER (
        request_id TEXT NOT NULL REFERENCES REQUEST_LOG(id) ON DELETE CASCADE,
        question_id TEXT NOT NULL,
        label TEXT NOT NULL,
        value REAL NOT NULL,
        confidence REAL NOT NULL,
        PRIMARY KEY (request_id, question_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ATTEMPT (
        request_id TEXT NOT NULL REFERENCES REQUEST_LOG(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        outcome TEXT NOT NULL,
        http_status INTEGER,
        latency_ms INTEGER NOT NULL
    )
    """,
)

_INSERT_REQUEST = (
    "INSERT INTO REQUEST_LOG (id, conversation_hash, profile, chosen_model,"
    " reasoning_level, decision_source, clef_timeout, clef_latency_ms, ttft_ms,"
    " total_ms, prompt_tokens, completion_tokens, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_ANSWER = (
    "INSERT INTO DECISION_ANSWER (request_id, question_id, label, value, confidence)"
    " VALUES (?, ?, ?, ?, ?)"
)
_INSERT_ATTEMPT = (
    "INSERT INTO ATTEMPT (request_id, model, outcome, http_status, latency_ms)"
    " VALUES (?, ?, ?, ?, ?)"
)


class DecisionLogError(RuntimeError):
    """Base class for decision-log failures the gateway can degrade without."""


@dataclass(frozen=True)
class ClefAnswer:
    """One Clef answer: the label it chose, its position and its confidence.

    `value` is the raw score for `score` questions and the probability of yes
    for `noul` ones; `label` is the chosen label or the scale's legend entry.
    """

    question_id: str
    label: str
    value: float
    confidence: float


@dataclass(frozen=True)
class Attempt:
    """One upstream delivery attempt for a request (spec 9's ATTEMPT).

    `outcome` is a short tag such as `success` or `http_error`, never text
    from the request or the response.
    """

    model: str
    outcome: str
    http_status: int | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class DecisionRecord:
    """What the router decided for one request, as typed facts only.

    `conversation_hash` must arrive already hashed: the raw conversation key
    is client-chosen text and never enters this module. `created_at` defaults
    to the current UTC time. The timeout and latencies are milliseconds.
    """

    request_id: str
    conversation_hash: str
    profile: str
    reasoning_level: Literal["off", "low", "medium", "high"]
    decision_source: str  # a short tag such as `clef` or `fallback`
    chosen_model: str | None = None
    clef_timeout: int = 0
    clef_latency_ms: int = 0
    ttft_ms: int | None = None
    total_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class StoredDecision:
    """One REQUEST_LOG row as read back (`created_at` is the stored ISO text)."""

    request_id: str
    conversation_hash: str
    profile: str
    chosen_model: str | None
    reasoning_level: str
    decision_source: str
    clef_timeout: int
    clef_latency_ms: int
    ttft_ms: int | None
    total_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: str


@dataclass(frozen=True)
class DecisionLogEntry:
    """One REQUEST_LOG row with its Clef answers and delivery attempts."""

    decision: StoredDecision
    answers: tuple[ClefAnswer, ...]
    attempts: tuple[Attempt, ...]


def _request_row(record: DecisionRecord) -> tuple[object, ...]:
    created = record.created_at or datetime.now(UTC)
    return (
        record.request_id,
        record.conversation_hash,
        record.profile,
        record.chosen_model,
        record.reasoning_level,
        record.decision_source,
        record.clef_timeout,
        record.clef_latency_ms,
        record.ttft_ms,
        record.total_ms,
        record.prompt_tokens,
        record.completion_tokens,
        created.isoformat(),
    )


def _decision_from_row(row: aiosqlite.Row) -> StoredDecision:
    return StoredDecision(
        request_id=row["id"],
        conversation_hash=row["conversation_hash"],
        profile=row["profile"],
        chosen_model=row["chosen_model"],
        reasoning_level=row["reasoning_level"],
        decision_source=row["decision_source"],
        clef_timeout=row["clef_timeout"],
        clef_latency_ms=row["clef_latency_ms"],
        ttft_ms=row["ttft_ms"],
        total_ms=row["total_ms"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        created_at=row["created_at"],
    )


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    for statement in _SCHEMA:
        await conn.execute(statement)


class DecisionLogStore:
    """Async repository over the decision-log SQLite file.

    Stateless by design: every operation opens its own connection, ensures
    the schema (`CREATE TABLE IF NOT EXISTS`, so opening twice is idempotent)
    and closes it in a `finally` — on every path, errors included, and always
    off the event loop.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await aiosqlite.connect(self._path)
        try:
            await conn.execute("PRAGMA foreign_keys = ON")
            await _ensure_schema(conn)
            await conn.commit()
            yield conn
        finally:
            await conn.close()

    async def open(self) -> None:
        """Create the schema if needed; safe to call any number of times."""
        async with self._connection():
            return None

    async def record_decision(
        self,
        record: DecisionRecord,
        *,
        answers: Sequence[ClefAnswer] = (),
        attempts: Sequence[Attempt] = (),
    ) -> None:
        """Write one request row plus its answers and attempts atomically.

        Any failure (a repeated `question_id`, a repeated `request_id`, a
        closed file) rolls the whole transaction back and raises
        `DecisionLogError`: the log never keeps a half-written decision.
        """
        async with self._connection() as db:
            try:
                await db.execute(_INSERT_REQUEST, _request_row(record))
                if answers:
                    await db.executemany(
                        _INSERT_ANSWER,
                        [
                            (record.request_id, a.question_id, a.label, a.value, a.confidence)
                            for a in answers
                        ],
                    )
                if attempts:
                    await db.executemany(
                        _INSERT_ATTEMPT,
                        [
                            (record.request_id, a.model, a.outcome, a.http_status, a.latency_ms)
                            for a in attempts
                        ],
                    )
                await db.commit()
            except sqlite3.Error as exc:
                await db.rollback()
                raise DecisionLogError(f"failed to record {record.request_id!r}: {exc}") from exc

    async def list_decisions(self, limit: int = 50) -> tuple[DecisionLogEntry, ...]:
        """Newest decisions first: by timestamp, then by insertion order."""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM REQUEST_LOG ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            entries = [await self._entry(db, row) for row in rows]
            return tuple(entries)

    async def get_decision(self, request_id: str) -> DecisionLogEntry | None:
        """One decision with its answers and attempts, or None if unknown."""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM REQUEST_LOG WHERE id = ?", (request_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return await self._entry(db, row)

    async def _entry(self, db: aiosqlite.Connection, row: aiosqlite.Row) -> DecisionLogEntry:
        request_id = row["id"]
        answers_cursor = await db.execute(
            "SELECT question_id, label, value, confidence FROM DECISION_ANSWER"
            " WHERE request_id = ? ORDER BY rowid",
            (request_id,),
        )
        answers = tuple(
            ClefAnswer(
                question_id=answer[0],
                label=answer[1],
                value=answer[2],
                confidence=answer[3],
            )
            for answer in await answers_cursor.fetchall()
        )
        attempts_cursor = await db.execute(
            "SELECT model, outcome, http_status, latency_ms FROM ATTEMPT"
            " WHERE request_id = ? ORDER BY rowid",
            (request_id,),
        )
        attempts = tuple(
            Attempt(
                model=attempt[0],
                outcome=attempt[1],
                http_status=attempt[2],
                latency_ms=attempt[3],
            )
            for attempt in await attempts_cursor.fetchall()
        )
        return DecisionLogEntry(
            decision=_decision_from_row(row), answers=answers, attempts=attempts
        )
