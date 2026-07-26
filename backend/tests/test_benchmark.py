from pathlib import Path

from backend.skilldoctor.benchmark import BenchmarkService
from backend.skilldoctor.models import BenchmarkRequest
from backend.skilldoctor.service import RunService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dynamic_pair_creates_parent_and_controlled_children(
    tmp_path: Path,
) -> None:
    runs = RunService(PROJECT_ROOT)
    runs.report_directory = tmp_path / "langgraph"
    benchmarks = BenchmarkService(runs)

    state = benchmarks.run(
        BenchmarkRequest(
            executor="fixture",
            skill_id="tdd-workflow",
        )
    )

    assert state["run_kind"] == "benchmark"
    assert state["status"] == "completed"
    assert state["control_run_id"].startswith("lg-")
    assert state["treatment_run_id"].startswith("lg-")
    assert state["control"]["condition"] == "without_skill"
    assert state["treatment"]["condition"] == "with_skill"
    assert state["report"]["summary"]["completedPairs"] == 1
    assert state["report"]["summary"]["averagePassRateDelta"] == 0.5

    control = runs.get(state["control_run_id"])
    treatment = runs.get(state["treatment_run_id"])
    assert control["parent_run_id"] == state["run_id"]
    assert treatment["parent_run_id"] == state["run_id"]
    assert control["task"] == treatment["task"]
    assert control["repair_enabled"] is False
    assert treatment["repair_enabled"] is False
    assert "repair_patch" not in control
    assert "repair_patch" not in treatment


def test_dynamic_pair_calculates_token_duration_and_regression(
    tmp_path: Path,
) -> None:
    runs = RunService(PROJECT_ROOT)
    runs.report_directory = tmp_path / "langgraph"
    state = BenchmarkService(runs).run(
        BenchmarkRequest(
            executor="fixture",
            skill_id="spreadsheet-summary",
        )
    )
    comparison = state["report"]["pairs"][0]["comparison"]

    assert comparison["tokenDelta"] == 450
    assert comparison["tokenOverheadRate"] == 450 / 1230
    assert comparison["durationDeltaMs"] == 220
    assert comparison["regressionRate"] == 0
    assert comparison["outcome"] == "improved"


def test_benchmark_stream_publishes_parent_updates_to_registry(
    tmp_path: Path,
) -> None:
    runs = RunService(PROJECT_ROOT)
    runs.report_directory = tmp_path / "langgraph"
    benchmarks = BenchmarkService(runs)

    states = list(
        benchmarks.stream(
            BenchmarkRequest(
                executor="fixture",
                skill_id="tdd-workflow",
            )
        )
    )
    listed = benchmarks.list()

    assert states[0]["status"] == "pending"
    assert states[-1]["status"] == "completed"
    assert len(states[-1]["events"]) == 6
    assert listed[0]["run_kind"] == "benchmark"
    assert listed[0]["run_id"] == states[-1]["run_id"]
    assert benchmarks.get(states[-1]["run_id"])["report"]["pairs"]


def test_benchmark_finishes_safely_when_one_condition_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runs = RunService(PROJECT_ROOT)
    runs.report_directory = tmp_path / "langgraph"
    original_run = runs.run

    def fail_treatment(request):
        if request.condition == "with_skill":
            raise RuntimeError("synthetic treatment failure")
        return original_run(request)

    monkeypatch.setattr(runs, "run", fail_treatment)
    state = BenchmarkService(runs).run(
        BenchmarkRequest(
            task="Keep the parent benchmark recoverable.",
            skill_id="demo-skill",
            executor="fixture",
        )
    )

    assert state["status"] == "failed"
    assert state["control_run_id"]
    assert state["treatment_run_id"] is None
    assert state["report"]["summary"]["completedPairs"] == 0
    assert "synthetic treatment failure" in state["error"]
