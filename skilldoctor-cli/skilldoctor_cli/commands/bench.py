from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..output.console import print_suite_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..quality import score_state
from ..workspace import default_report_path, load_jsonl, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser("bench", help="Run a JSONL case set through the backend diagnostic pipeline.")
    command.add_argument("cases", help="JSONL file; each line is DiagnosticCaseRequest or TraceIngestRequest shape.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--suite-id", default="cli-bench")
    command.add_argument("--name", default="CLI Bench Suite")
    command.add_argument("--include-tag", action="append", default=[])
    command.add_argument("--exclude-tag", action="append", default=[])
    command.add_argument("--include-flaky", action="store_true")
    command.add_argument("--fail-fast", action="store_true")
    command.add_argument("--include-default-cases", action="store_true")
    command.add_argument("--include-saved-cases", action="store_true")
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _as_case(modules: dict[str, Any], payload: dict[str, Any], index: int):
    DiagnosticCaseRequest = modules["DiagnosticCaseRequest"]
    if "trace" in payload:
        return DiagnosticCaseRequest.model_validate(payload)
    return DiagnosticCaseRequest.model_validate(
        {
            "case_id": payload.get("case_id") or f"cli-case-{index:03d}",
            "name": payload.get("name") or payload.get("task") or f"CLI case {index}",
            "description": payload.get("description", "Imported from CLI JSONL case set."),
            "source": payload.get("source", "custom"),
            "trace": payload,
            "expectation": payload.get("expectation", {}),
        }
    )


def _case_id(payload: dict[str, Any], index: int) -> str:
    return str(payload.get("case_id") or f"cli-case-{index:03d}")


def _case_metadata(payload: dict[str, Any], index: int) -> dict[str, Any]:
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return {
        "case_id": _case_id(payload, index),
        "schema_version": payload.get("schema_version") or payload.get("schemaVersion") or "1.0",
        "tags": sorted(str(item) for item in tags),
        "flaky": bool(payload.get("flaky", False)),
        "regression_risk": payload.get("regression_risk") or payload.get("regressionRisk"),
    }


def _filter_payloads(
    payloads: list[dict[str, Any]],
    *,
    include_tags: list[str],
    exclude_tags: list[str],
    include_flaky: bool,
) -> tuple[list[tuple[int, dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    selected: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []
    include = {item for item in include_tags if item}
    exclude = {item for item in exclude_tags if item}
    for index, payload in enumerate(payloads, start=1):
        metadata = _case_metadata(payload, index)
        tags = set(metadata["tags"])
        reason = ""
        if include and tags.isdisjoint(include):
            reason = "include_tag_filter"
        elif exclude and not tags.isdisjoint(exclude):
            reason = "exclude_tag_filter"
        elif metadata["flaky"] and not include_flaky:
            reason = "flaky_excluded"
        if reason:
            skipped.append({**metadata, "reason": reason})
            continue
        selected.append((index, payload, metadata))
    return selected, skipped


def _summary_from_cases(cases: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in cases if item["passed"])
    failed = len(cases) - passed
    saved = sum(1 for item in cases if item.get("source") == "saved_run")
    return {
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / len(cases) if cases else 1.0,
        "repairable": sum(1 for item in cases if item["repairable"]),
        "non_skill": sum(1 for item in cases if item["category"] == "non_skill"),
        "llm_authored": sum(1 for item in cases if item["agent_source"] == "llm"),
        "saved_cases": saved,
        "skipped": len(skipped),
        "flaky": sum(1 for item in cases if item.get("flaky")),
    }


def _markdown_for_cases(name: str, reports: list[dict[str, Any]]) -> str:
    lines = [f"# {name}", "", f"- 用例总数：{len(reports)}"]
    for item in reports:
        mark = "✅" if item.get("passed") else "❌"
        tags = ", ".join(item.get("tags") or []) or "none"
        lines.extend(
            [
                "",
                f"## {mark} {item.get('name')}",
                "",
                f"- Case：`{item.get('case_id')}`",
                f"- Run：`{item.get('run_id')}`",
                f"- Tags：{tags}",
                f"- Flaky：`{item.get('flaky', False)}`",
                f"- 状态：{item.get('status')} / {item.get('stop_reason')}",
                f"- 分类：{item.get('category')}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _annotate_cases(report: dict[str, Any], metadata_by_id: dict[str, dict[str, Any]]) -> None:
    for case in report.get("cases") or []:
        metadata = metadata_by_id.get(str(case.get("case_id")))
        if metadata:
            case.update(
                {
                    "schema_version": metadata["schema_version"],
                    "tags": metadata["tags"],
                    "flaky": metadata["flaky"],
                    "regression_risk": metadata["regression_risk"],
                }
            )


def _run_fail_fast_suite(
    service: Any,
    *,
    suite_id: str,
    name: str,
    cases: list[Any],
    metadata_by_id: dict[str, dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    stopped_early = False
    for case in cases:
        result = service._run_diagnostic_case(case)
        metadata = metadata_by_id.get(str(result.get("case_id")))
        if metadata:
            result.update(
                {
                    "schema_version": metadata["schema_version"],
                    "tags": metadata["tags"],
                    "flaky": metadata["flaky"],
                    "regression_risk": metadata["regression_risk"],
                }
            )
        reports.append(result)
        if not result.get("passed"):
            stopped_early = True
            break
    return {
        "schema_version": "1.0",
        "suite_id": suite_id,
        "name": name,
        "generated_at": utc_now(),
        "status": "passed" if all(item.get("passed") for item in reports) else "failed",
        "summary": _summary_from_cases(reports, skipped),
        "cases": reports,
        "markdown": _markdown_for_cases(name, reports),
        "fail_fast": {"enabled": True, "stopped_early": stopped_early},
    }


def handle(args: Namespace) -> int:
    modules = backend_modules(args.project_root)
    payloads = load_jsonl(args.cases)
    selected, skipped = _filter_payloads(
        payloads,
        include_tags=args.include_tag,
        exclude_tags=args.exclude_tag,
        include_flaky=args.include_flaky,
    )
    metadata_by_id = {metadata["case_id"]: metadata for _, _, metadata in selected}
    cases = [_as_case(modules, payload, index) for index, payload, _ in selected]
    request = modules["DiagnosticSuiteRequest"](
        suite_id=args.suite_id,
        name=args.name,
        include_default_cases=args.include_default_cases,
        include_saved_cases=args.include_saved_cases,
        cases=cases,
    )
    service = new_run_service(args.project_root)
    if args.fail_fast and not args.include_default_cases and not args.include_saved_cases:
        report = _run_fail_fast_suite(
            service,
            suite_id=args.suite_id,
            name=args.name,
            cases=cases,
            metadata_by_id=metadata_by_id,
            skipped=skipped,
        )
    else:
        report = service.run_diagnostic_suite(request)
        _annotate_cases(report, metadata_by_id)
    quality_scores: list[float] = []
    for case in report.get("cases", []):
        run_id = case.get("run_id")
        if not run_id:
            continue
        try:
            quality_scores.append(score_state(service.get(run_id))["overall_score"])
        except (AttributeError, FileNotFoundError, ValueError, KeyError):
            continue
    if quality_scores:
        report["summary"]["quality_average"] = round(sum(quality_scores) / len(quality_scores), 4)
    report["summary"]["skipped"] = len(skipped)
    report["summary"]["flaky"] = sum(1 for item in report.get("cases") or [] if item.get("flaky"))
    report["kind"] = "bench"
    report["case_set_path"] = str(Path(args.cases).expanduser())
    report["case_set"] = {
        "schema_versions": sorted({metadata["schema_version"] for metadata in metadata_by_id.values()}),
        "include_tags": args.include_tag,
        "exclude_tags": args.exclude_tag,
        "include_flaky": args.include_flaky,
        "skipped": skipped,
    }
    report.setdefault("fail_fast", {"enabled": bool(args.fail_fast), "stopped_early": False})
    report["generated_at"] = report.get("generated_at") or utc_now()
    json_path = args.json_out or default_report_path(args.project_root, "bench")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="bench"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_suite_summary(report, title="Skill Doctor Bench")
        print(f"report: {report['report_path']}")
    return 0 if report.get("status") == "passed" else 30
