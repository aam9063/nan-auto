"""Tests for the NAU-13 decision log store.

The invariant (spec 12): the store is structurally unable to keep the user's
prompt, the response body or an API key. T4 proves the public API accepts no
free-form text anywhere; T5 plants a canary prompt and key in the inputs a
careless implementation might stash and sweeps every column of every table.
"""

import inspect
import sqlite3
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest

from nan_auto.gateway import log_store
from nan_auto.gateway.log_store import (
    Attempt,
    ClefAnswer,
    DecisionLogError,
    DecisionLogStore,
    DecisionRecord,
    StoredDecision,
)

NOW = datetime(2026, 12, 1, 12, 0, 0, tzinfo=UTC)
CANARY_PROMPT = "CANARY-PROMPT-never-store-7f3a"
CANARY_KEY = "sk-nan-CANARY-KEY-9c2d"

# Words whose appearance in a public parameter or field name means free-form
# content is being invited in. `prompt_tokens` survives because the sweep only
# flags names paired with a string-capable annotation.
CONTENT_WORDS = {
    "prompt",
    "content",
    "message",
    "body",
    "text",
    "key",
    "secret",
    "token",
    "response",
    "raw",
    "payload",
    "attachment",
    "completion",
}

ANSWERS = (
    ClefAnswer("task", "coding_generate", 0.92, 0.87),
    ClefAnswer("difficulty", "hard", 3.4, 0.71),
)
ATTEMPTS = (
    Attempt("glm5.3-flash", "http_error", 503, 1400),
    Attempt("gemma4", "success", 200, 980),
)


def make_record(**overrides: Any) -> DecisionRecord:
    """One valid decision with every spec-9 fact filled in."""
    values: dict[str, Any] = dict(
        request_id="req-1",
        conversation_hash="a" * 64,
        profile="balanced",
        reasoning_level="off",
        decision_source="clef",
        chosen_model="gemma4",
        clef_timeout=800,
        clef_latency_ms=120,
        ttft_ms=210,
        total_ms=980,
        prompt_tokens=57,
        completion_tokens=312,
        created_at=NOW,
    )
    values.update(overrides)
    return DecisionRecord(**values)


def make_store(tmp_path: Path) -> DecisionLogStore:
    return DecisionLogStore(tmp_path / "decisions.db")


def _query(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()


def _tables(db_path: Path) -> set[str]:
    rows = _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in rows if not row[0].startswith("sqlite_")}


def _columns(db_path: Path, table: str) -> list[str]:
    return [row[1] for row in _query(db_path, f"PRAGMA table_info({table})")]


def _foreign_keys(db_path: Path, table: str) -> list[tuple[str, str, str]]:
    return [
        (row[3], row[2], row[4]) for row in _query(db_path, f"PRAGMA foreign_key_list({table})")
    ]


def _count(db_path: Path, table: str) -> int:
    return int(_query(db_path, f"SELECT COUNT(*) FROM {table}")[0][0])


# --- T1: schema and idempotent open ------------------------------------------


async def test_open_twice_is_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.open()
    await store.open()
    assert {"REQUEST_LOG", "DECISION_ANSWER", "ATTEMPT"} <= _tables(tmp_path / "decisions.db")


async def test_tables_match_spec_nine_columns(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.open()
    db = tmp_path / "decisions.db"
    assert _columns(db, "REQUEST_LOG") == [
        "id",
        "conversation_hash",
        "profile",
        "chosen_model",
        "reasoning_level",
        "decision_source",
        "clef_timeout",
        "clef_latency_ms",
        "ttft_ms",
        "total_ms",
        "prompt_tokens",
        "completion_tokens",
        "created_at",
    ]
    assert _columns(db, "DECISION_ANSWER") == [
        "request_id",
        "question_id",
        "label",
        "value",
        "confidence",
    ]
    assert _columns(db, "ATTEMPT") == [
        "request_id",
        "model",
        "outcome",
        "http_status",
        "latency_ms",
    ]


async def test_answers_and_attempts_reference_the_request(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.open()
    db = tmp_path / "decisions.db"
    for table in ("DECISION_ANSWER", "ATTEMPT"):
        assert _foreign_keys(db, table) == [("request_id", "REQUEST_LOG", "id")]


# --- T2: record_decision writes one transaction -------------------------------


async def test_record_decision_writes_request_answers_and_attempts(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.record_decision(make_record(), answers=ANSWERS, attempts=ATTEMPTS)
    db = tmp_path / "decisions.db"
    assert _count(db, "REQUEST_LOG") == 1
    assert _count(db, "DECISION_ANSWER") == 2
    assert _count(db, "ATTEMPT") == 2


async def test_record_decision_without_extras_writes_one_row(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.record_decision(make_record())
    db = tmp_path / "decisions.db"
    assert _count(db, "REQUEST_LOG") == 1
    assert _count(db, "DECISION_ANSWER") == 0
    assert _count(db, "ATTEMPT") == 0


async def test_a_failed_answer_rolls_back_the_whole_transaction(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    duplicate = (ClefAnswer("task", "a", 1.0, 0.5), ClefAnswer("task", "b", 0.0, 0.5))
    with pytest.raises(DecisionLogError):
        await store.record_decision(make_record(), answers=duplicate, attempts=ATTEMPTS)
    db = tmp_path / "decisions.db"
    assert _count(db, "REQUEST_LOG") == 0
    assert _count(db, "DECISION_ANSWER") == 0
    assert _count(db, "ATTEMPT") == 0


# --- T3: reads for the dashboard ----------------------------------------------


async def test_list_decisions_returns_newest_first(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.record_decision(make_record(request_id="req-old", created_at=NOW))
    await store.record_decision(
        make_record(request_id="req-new", created_at=NOW + timedelta(minutes=1))
    )
    entries = await store.list_decisions()
    assert [entry.decision.request_id for entry in entries] == ["req-new", "req-old"]


async def test_list_decisions_breaks_ties_by_insertion_order(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.record_decision(make_record(request_id="req-1", created_at=NOW))
    await store.record_decision(make_record(request_id="req-2", created_at=NOW))
    entries = await store.list_decisions()
    assert [entry.decision.request_id for entry in entries] == ["req-2", "req-1"]


async def test_list_decisions_honours_the_limit(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.record_decision(make_record(request_id="req-1", created_at=NOW))
    await store.record_decision(make_record(request_id="req-2", created_at=NOW))
    entries = await store.list_decisions(limit=1)
    assert [entry.decision.request_id for entry in entries] == ["req-2"]


async def test_get_decision_roundtrips_every_written_fact(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.record_decision(make_record(), answers=ANSWERS, attempts=ATTEMPTS)
    entry = await store.get_decision("req-1")
    assert entry is not None
    assert entry.decision == StoredDecision(
        request_id="req-1",
        conversation_hash="a" * 64,
        profile="balanced",
        chosen_model="gemma4",
        reasoning_level="off",
        decision_source="clef",
        clef_timeout=800,
        clef_latency_ms=120,
        ttft_ms=210,
        total_ms=980,
        prompt_tokens=57,
        completion_tokens=312,
        created_at=NOW.isoformat(),
    )
    assert entry.answers == ANSWERS
    assert entry.attempts == ATTEMPTS


async def test_get_decision_unknown_id_returns_none(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.open()
    assert await store.get_decision("missing") is None


# --- T4: the public API accepts no free-form text ------------------------------


def _name_words(name: str) -> set[str]:
    words = {word for word in name.lower().split("_") if word}
    return {word.rstrip("s") or word for word in words}


def _annotation_allows_str(annotation: object) -> bool:
    if annotation is str:
        return True
    origin = get_origin(annotation)
    return origin in (Union, UnionType) and str in get_args(annotation)


def _sweep() -> list[str]:
    """Every public parameter or field whose name invites content and whose
    annotation can carry a string: each one is a place a prompt could hide."""
    offenders: list[str] = []

    def check(owner: str, name: str, annotation: object) -> None:
        if _name_words(name) & CONTENT_WORDS and _annotation_allows_str(annotation):
            offenders.append(f"{owner}.{name}")

    for export in log_store.__all__:
        obj = getattr(log_store, export)
        if inspect.isclass(obj):
            for name, member in inspect.getmembers(obj, inspect.isfunction):
                if name.startswith("_") and name != "__init__":
                    continue
                hints = get_type_hints(member)
                signature = inspect.signature(member)
                for param_name in signature.parameters:
                    if param_name in ("self", "cls"):
                        continue
                    check(export, param_name, hints.get(param_name))
            hints = get_type_hints(obj)
            if is_dataclass(obj):
                for field in dataclass_fields(obj):
                    check(export, field.name, hints.get(field.name))
        elif callable(obj):
            hints = get_type_hints(obj)
            for param_name in inspect.signature(obj).parameters:
                check(export, param_name, hints.get(param_name))
    return offenders


def test_public_surface_is_pinned() -> None:
    assert tuple(log_store.__all__) == (
        "Attempt",
        "ClefAnswer",
        "DecisionLogEntry",
        "DecisionLogError",
        "DecisionLogStore",
        "DecisionRecord",
        "StoredDecision",
    )


def test_record_decision_takes_only_typed_values() -> None:
    method = DecisionLogStore.record_decision
    assert list(inspect.signature(method).parameters) == ["self", "record", "answers", "attempts"]
    hints = get_type_hints(method)
    assert hints["record"] is DecisionRecord
    assert get_args(hints["answers"])[0] is ClefAnswer
    assert get_args(hints["attempts"])[0] is Attempt


def test_no_public_name_takes_free_form_text() -> None:
    assert _sweep() == []


# --- T5: the canary ------------------------------------------------------------


async def test_canary_prompt_and_key_never_reach_the_database(tmp_path: Path) -> None:
    db_path = tmp_path / "canary.db"
    store = DecisionLogStore(db_path)
    # The inputs a careless implementation might stash verbatim: the raw
    # conversation key (client-chosen, it may carry user text) plus canary
    # strings in every surrounding slot that is not a typed fact.
    conversation_key = f"{CANARY_PROMPT}\nAuthorization: Bearer {CANARY_KEY}"
    careless_inputs = {
        "conversation_key": conversation_key,
        "model": f"{CANARY_PROMPT}-model",
        "profile": f"{CANARY_KEY}-profile",
    }
    bait = " ".join(careless_inputs.values())
    assert CANARY_PROMPT in bait and CANARY_KEY in bait

    record = make_record(
        request_id="req-canary",
        conversation_hash=sha256(conversation_key.encode("utf-8")).hexdigest(),
        chosen_model="gemma4",
        profile="balanced",
    )
    await store.record_decision(
        record,
        answers=(ClefAnswer("task", "chat_casual", 0.98, 0.94),),
        attempts=(Attempt("gemma4", "success", 200, 900),),
    )

    digest = record.conversation_hash
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        ]
        found: list[str] = []
        for table in tables:
            cursor = conn.execute(f"SELECT * FROM {table}")
            columns = [description[0] for description in cursor.description]
            for row in cursor.fetchall():
                for column, value in zip(columns, row, strict=True):
                    text = f"{table}.{column}={value!r}"
                    if CANARY_PROMPT in text or CANARY_KEY in text:
                        found.append(text)
        request_rows = list(conn.execute("SELECT * FROM REQUEST_LOG"))
    finally:
        conn.close()

    assert found == []
    assert tables == ["REQUEST_LOG", "DECISION_ANSWER", "ATTEMPT"]
    # Positive control: the decision is really there, keyed by the hash.
    assert any(digest in str(row) for row in request_rows)
