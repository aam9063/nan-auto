"""NAU-15: offline policy evaluation — baselines R0-R3 and Pareto data.

These tests are pure arithmetic over synthetic cache rows: no network, no
client. The evaluation must work even when the matrix is only partially
filled, reporting coverage honestly.
"""

# bench lives in eval/, outside the server root; pyright cannot follow it there.
# pyright: reportMissingImports=false

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from bench import matrix as bench_matrix  # noqa: E402
from bench import run as bench_run  # noqa: E402
from bench.cache import open_db, save_result  # noqa: E402

TIERS = {
    "fast-a": "t1_fast",
    "deepseek-v4-flash": "t2_generalist",
    "slow-big": "t3_max",
}


def seed(db, model: str, rows: list[tuple[str, int | None, float]]) -> None:
    for prompt_id, quality, latency in rows:
        save_result(
            db,
            prompt_id,
            model,
            latency_ms=latency,
            prompt_tokens=1,
            completion_tokens=1,
            answer=f"answer for {prompt_id}",
        )
        if quality is not None:
            db.execute(
                "UPDATE results SET quality_score = ?, judge_raw = 'stub' "
                "WHERE prompt_id = ? AND model = ?",
                (quality, prompt_id, model),
            )
    db.commit()


def two_prompts() -> list[bench_matrix.Intent]:
    return [
        bench_matrix.Intent(id="p0", prompt="hi", task="chat_casual", difficulty=0),
        bench_matrix.Intent(id="p1", prompt="bye", task="chat_casual", difficulty=0),
    ]


# --- R0: always deepseek-v4-flash ---------------------------------------------


def test_r0_reports_the_deepseek_rows_mean_quality_and_latency() -> None:
    db = open_db(Path(":memory:"))
    seed(
        db,
        "deepseek-v4-flash",
        [("p0", 3, 100.0), ("p1", 4, 300.0)],
    )
    report = bench_run.evaluate(db, two_prompts(), tiers=TIERS)
    r0 = report["baselines"]["R0"]
    assert r0["model"] == "deepseek-v4-flash"
    assert r0["quality"] == 3.5
    assert r0["latency_ms"] == 200.0
    assert r0["coverage"] == 1.0


def test_r0_coverage_is_honest_when_rows_are_missing() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "deepseek-v4-flash", [("p0", 3, 100.0)])  # p1 never answered
    report = bench_run.evaluate(db, two_prompts(), tiers=TIERS)
    assert report["baselines"]["R0"]["coverage"] == 0.5
    assert report["complete"] is False
    assert report["missing_calls"] >= 1


# --- R1: always the most capable ----------------------------------------------


def test_r1_picks_the_top_tier_model_from_the_card() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "fast-a", [("p0", 4, 50.0), ("p1", 4, 50.0)])
    seed(db, "slow-big", [("p0", 3, 900.0), ("p1", 3, 900.0)])
    report = bench_run.evaluate(db, two_prompts(), tiers=TIERS)
    assert report["baselines"]["R1"]["model"] == "slow-big", "t3_max is the top card tier"
    assert report["baselines"]["R1"]["quality"] == 3.0


def test_r1_falls_back_to_the_next_tier_when_the_top_tier_is_not_measured() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "fast-a", [("p0", 2, 50.0), ("p1", 2, 50.0)])
    report = bench_run.evaluate(db, two_prompts(), tiers=TIERS)
    # No t3_max model was ever measured: R1 must degrade to a measured model,
    # never report numbers for a model the cache does not hold.
    assert report["baselines"]["R1"]["model"] == "fast-a"


# --- R2: always the fastest measured -------------------------------------------


def test_r2_picks_the_model_with_the_lowest_mean_latency() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "fast-a", [("p0", 2, 40.0), ("p1", 2, 60.0)])  # mean 50
    seed(db, "slow-big", [("p0", 4, 800.0), ("p1", 4, 1000.0)])  # mean 900
    report = bench_run.evaluate(db, two_prompts(), tiers=TIERS)
    assert report["baselines"]["R2"]["model"] == "fast-a"
    assert report["baselines"]["R2"]["latency_ms"] == 50.0


# --- R3: the router policy, replayed from the labels ---------------------------


def test_r3_replays_the_router_choice_per_prompt() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "fast-a", [("p0", 4, 50.0), ("p1", 4, 50.0)])
    seed(db, "deepseek-v4-flash", [("p0", 3, 150.0), ("p1", 3, 150.0)])
    intents = [
        bench_matrix.Intent(id="p0", prompt="hi", task="chat_casual", difficulty=0),
        # Hard coding: the policy wants glm5.3 by id, but glm5.3 has no cached
        # answer, so the router must fall through to a measured candidate
        # ranked next (deepseek-v4-flash) — or report the prompt uncovered.
        bench_matrix.Intent(id="p1", prompt="code", task="coding_generate", difficulty=3),
    ]
    report = bench_run.evaluate(db, intents, tiers=TIERS)
    r3 = report["baselines"]["R3"]
    assert r3["model"] == "router"
    # No network, no judgement: R3 quality is exactly the mean of the rows the
    # policy selected, prompt by prompt.
    chosen = [row for _, row in report["debug_r3_choices"].items()]
    assert chosen[0] == "fast-a", "a trivial chat goes to t1_fast"
    assert chosen[1] == "deepseek-v4-flash", "hard coding degrades to the next ranked candidate"
    assert r3["coverage"] == 1.0
    assert r3["quality"] == (4.0 + 3.0) / 2


def test_r3_leaves_uncovered_prompts_out_of_the_average() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "fast-a", [("p0", 4, 50.0)])  # p1 (coding, hard) has no matching row
    intents = [
        bench_matrix.Intent(id="p0", prompt="hi", task="chat_casual", difficulty=0),
        bench_matrix.Intent(id="p1", prompt="code", task="coding_generate", difficulty=3),
    ]
    report = bench_run.evaluate(db, intents, tiers=TIERS)
    r3 = report["baselines"]["R3"]
    assert r3["coverage"] == 0.5
    assert r3["quality"] == 4.0, "the mean must not be diluted by uncovered prompts"


# --- Pareto data and the emitted report ----------------------------------------


def test_pareto_has_one_point_per_model_and_per_policy() -> None:
    db = open_db(Path(":memory:"))
    seed(db, "fast-a", [("p0", 4, 50.0), ("p1", 2, 70.0)])
    seed(db, "slow-big", [("p0", 4, 800.0), ("p1", 4, 800.0)])
    seed(db, "deepseek-v4-flash", [("p0", 3, 150.0), ("p1", 3, 150.0)])
    report = bench_run.evaluate(db, two_prompts(), tiers=TIERS)

    model_points = {point["model"]: point for point in report["pareto"]["models"]}
    assert set(model_points) == {"fast-a", "slow-big", "deepseek-v4-flash"}
    assert model_points["fast-a"]["quality"] == 3.0
    assert model_points["fast-a"]["latency_ms"] == 60.0
    assert model_points["slow-big"]["latency_ms"] == 800.0

    policy_points = report["pareto"]["policies"]
    assert {point["policy"] for point in policy_points} == {"R0", "R1", "R2", "R3"}
    for point in policy_points:
        assert "quality" in point and "latency_ms" in point


def test_run_main_emits_json_and_a_readable_table(capsys, tmp_path) -> None:
    db = open_db(tmp_path / "matrix.sqlite3")
    seed(db, "deepseek-v4-flash", [("p0", 3, 100.0), ("p1", 4, 300.0)])
    seed(db, "fast-a", [("p0", 4, 40.0), ("p1", 4, 60.0)])
    db.close()
    code = bench_run.main(["--db", str(tmp_path / "matrix.sqlite3")])
    assert code == 0
    out = capsys.readouterr().out
    assert "R0" in out and "R3" in out, "the table names the baselines"
    assert '"R0"' in out, "the JSON report is printed too"
    assert "deepseek-v4-flash" in out


def test_dry_run_prints_the_plan_and_costs_nothing(capsys, tmp_path) -> None:
    db = open_db(tmp_path / "matrix.sqlite3")
    save_result(db, "p0", "fast-a", latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="a")
    db.close()
    code = bench_run.main(["--dry-run", "--db", str(tmp_path / "matrix.sqlite3")])
    assert code == 0
    out = capsys.readouterr().out
    assert "2" in out, "the plan shows the remaining call count (1 prompt x 2 models - 1 cached)"
    assert "remaining" in out.lower()
