from pathlib import Path

from backend.skilldoctor.models import RunRequest
from backend.skilldoctor.service import RunService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_content_gap_is_repaired_and_promoted(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path

    result = service.run(
        RunRequest(
            skill_id="spreadsheet-summary",
            executor="fixture",
            scenario="content-gap",
        )
    )

    assert result["status"] == "passed"
    assert result["attempt"] == 1
    assert result["stop_reason"] == "repair_verified"
    assert result["attribution"]["taxonomy"] == "Content Gap"
    assert result["verification"]["decision"] == "ADOPT"
    assert result["verification"]["pass_rate_delta"] == 0.5
    assert result["skill_version"] == "1.0.1"
    assert [event["stage"] for event in result["events"]] == [
        "prepare",
        "execute",
        "collect_evidence",
        "attribute",
        "repair",
        "execute",
        "collect_evidence",
        "verify",
        "promote",
    ]
    assert (tmp_path / f"{result['run_id']}.json").is_file()


def test_platform_failure_never_mutates_skill(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path

    result = service.run(
        RunRequest(
            executor="fixture",
            scenario="network-error",
        )
    )

    assert result["status"] == "failed"
    assert result["attempt"] == 0
    assert result["attribution"]["cause"] == "platform"
    assert "repair_patch" not in result
    assert result["skill_version"] == "1.0.0"


def test_real_codex_report_can_be_replayed_through_graph(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path

    result = service.run(
        RunRequest(
            executor="replay",
            skill_id="tdd-workflow",
        )
    )

    assert result["status"] == "passed"
    assert result["baseline_execution"]["condition"] == "without_skill"
    assert result["execution"]["condition"] == "with_skill"
    assert result["baseline_execution"]["pass_rate"] == 0.25
    assert result["execution"]["pass_rate"] == 1.0
    assert result["verification"]["pass_rate_delta"] == 0.75
