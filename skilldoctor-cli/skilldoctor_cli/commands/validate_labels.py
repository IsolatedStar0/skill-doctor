from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from ..exit_codes import EXIT_OK, EXIT_QUALITY_GATE_FAILED
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..quality import score_state
from ..workspace import default_report_path, load_jsonl, utc_now


def register(subcommands) -> None:
    command = subcommands.add_parser(
        "validate-labels",
        help="Validate labeled puck-rule-rca results against manual labels.",
    )
    command.add_argument("labels", help="JSONL file generated from manually labeled results.")
    command.add_argument("--project-root", type=Path)
    command.add_argument("--domain-quality-threshold", type=float, default=0.75)
    command.add_argument("--min-prediction-accuracy", type=float)
    command.add_argument("--min-quality-accuracy", type=float)
    command.add_argument("--max-false-accept-rate", type=float)
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "是", "应该降噪", "是噪声"}:
            return True
        if text in {"false", "0", "no", "n", "否", "不应该降噪", "不是噪声"}:
            return False
    return None


def _prediction(record: dict[str, Any]) -> bool | None:
    expectation = record.get("expectation") or {}
    agent_output = record.get("agent_output") or {}
    raw = record.get("raw") or {}
    for value in (
        expectation.get("predicted_filter"),
        agent_output.get("filter"),
        agent_output.get("rca_filter"),
        raw.get("predicted_filter"),
        raw.get("rca_filter"),
        agent_output.get("skill_noise_result"),
    ):
        result = _as_bool(value)
        if result is not None:
            return result
    return None


def _expected(record: dict[str, Any]) -> bool | None:
    expectation = record.get("expectation") or {}
    manual = record.get("manual_label") or {}
    raw = record.get("raw") or {}
    for value in (
        expectation.get("expected_filter"),
        manual.get("should_filter"),
        raw.get("should_filter"),
        manual.get("should_filter_text"),
    ):
        result = _as_bool(value)
        if result is not None:
            return result
    return None


def _expected_good(record: dict[str, Any], prediction_correct: bool | None) -> bool | None:
    expectation = record.get("expectation") or {}
    manual = record.get("manual_label") or {}
    business_quality = str(expectation.get("business_quality") or "").strip().lower()
    if business_quality in {"good", "pass", "passed", "accurate"}:
        return True
    if business_quality in {"bad", "fail", "failed", "inaccurate"}:
        return False
    accuracy = str(manual.get("result_accuracy") or "").strip().lower()
    if accuracy in {"accurate", "准确"}:
        return True
    if accuracy in {"inaccurate", "不准确"}:
        return False
    return prediction_correct


def _confidence(agent_output: dict[str, Any], raw: dict[str, Any]) -> float | None:
    value = agent_output.get("confidence")
    if value is None:
        value = raw.get("confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _state_from_label(record: dict[str, Any]) -> dict[str, Any]:
    agent_output = record.get("agent_output") or {}
    raw = record.get("raw") or {}
    prediction = _prediction(record)
    confidence = _confidence(agent_output, raw)
    rationale = str(
        agent_output.get("rationale")
        or agent_output.get("reason")
        or raw.get("rationale")
        or raw.get("rca_content")
        or ""
    ).strip()
    chart_url = agent_output.get("chart_url") or raw.get("chart_url")
    details = []
    if chart_url:
        details.append(
            {
                "name": "chart_evidence",
                "status": "pass",
                "reason": rationale or "提供了图表证据。",
            }
        )
    artifacts = {"chart_url": chart_url} if chart_url else {}
    runtime_events = []
    if chart_url:
        runtime_events.append(
            {
                "stage": "agent.analyze.tool_calls",
                "status": "completed",
                "metadata": {"total": 1, "failed": 0},
            }
        )
    return {
        "run_id": record.get("case_id"),
        "status": "passed",
        "skill_id": str(record.get("skill_id") or "puck-rule-rca"),
        "skill_version": "labeled",
        "business_result": {
            "verdict": rationale or agent_output.get("skill_noise_result") or "",
            "verdict_type": "pass" if prediction else "warning",
            "confidence": confidence,
            "details": details,
            "extra": {
                "raw_business_result": {
                    "rca_filter": prediction,
                    "rca_content": rationale,
                    "confidence": confidence,
                    "rca_detail": details,
                    "chart_url": chart_url,
                }
            },
        },
        "execution": {
            "passed": True,
            "pass_rate": 1.0,
            "duration_ms": int(
                float(agent_output.get("elapsed_seconds") or raw.get("elapsed_seconds") or 0) * 1000
            ),
            "assertions": [],
            "artifacts": artifacts,
            "runtime_events": runtime_events,
        },
        "attribution": {
            "cause": "none",
            "action": "none",
            "evidence_refs": ["artifact:chart_url"] if chart_url else [],
        },
    }


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _evaluate_records(
    records: list[dict[str, Any]],
    *,
    domain_quality_threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    confusion = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    quality_confusion = {
        "true_accept": 0,
        "true_reject": 0,
        "false_accept": 0,
        "false_reject": 0,
    }

    for index, record in enumerate(records, start=1):
        case_id = str(record.get("case_id") or f"label-case-{index:03d}")
        predicted = _prediction(record)
        expected = _expected(record)
        if predicted is None or expected is None:
            skipped.append({"case_id": case_id, "reason": "missing_prediction_or_expected_label"})
            continue
        prediction_correct = predicted == expected
        if predicted and expected:
            confusion["tp"] += 1
        elif not predicted and not expected:
            confusion["tn"] += 1
        elif predicted and not expected:
            confusion["fp"] += 1
        else:
            confusion["fn"] += 1

        quality = score_state(_state_from_label(record))
        domain_quality = quality.get("domain_quality") or {}
        domain_score = float(domain_quality.get("score") or 0.0)
        predicted_good = domain_score >= domain_quality_threshold
        expected_good = _expected_good(record, prediction_correct)
        if expected_good is True and predicted_good:
            quality_confusion["true_accept"] += 1
        elif expected_good is False and not predicted_good:
            quality_confusion["true_reject"] += 1
        elif expected_good is False and predicted_good:
            quality_confusion["false_accept"] += 1
        elif expected_good is True and not predicted_good:
            quality_confusion["false_reject"] += 1

        rows.append(
            {
                "case_id": case_id,
                "predicted_filter": predicted,
                "expected_filter": expected,
                "prediction_correct": prediction_correct,
                "expected_quality_good": expected_good,
                "domain_quality_score": round(domain_score, 4),
                "domain_quality_passed": predicted_good,
                "quality_prediction_correct": expected_good is None or expected_good == predicted_good,
                "rule_name": (record.get("input") or {}).get("rule_name"),
                "group_detail_name": (record.get("input") or {}).get("group_detail_name"),
                "findings": domain_quality.get("findings") or [],
            }
        )

    total = len(rows)
    prediction_correct_total = sum(1 for row in rows if row["prediction_correct"])
    quality_labeled = [row for row in rows if row["expected_quality_good"] is not None]
    quality_correct_total = sum(1 for row in quality_labeled if row["quality_prediction_correct"])
    false_accept_rate = _rate(quality_confusion["false_accept"], len(quality_labeled))
    summary = {
        "total": total,
        "skipped": len(skipped),
        "prediction_correct": prediction_correct_total,
        "prediction_incorrect": total - prediction_correct_total,
        "prediction_accuracy": _rate(prediction_correct_total, total),
        "filter_confusion": confusion,
        "false_positive_rate": _rate(confusion["fp"], total),
        "false_negative_rate": _rate(confusion["fn"], total),
        "quality_labeled": len(quality_labeled),
        "quality_correct": quality_correct_total,
        "quality_accuracy": _rate(quality_correct_total, len(quality_labeled)),
        "quality_confusion": quality_confusion,
        "false_accept_rate": false_accept_rate,
    }
    return {"summary": summary, "cases": rows, "skipped": skipped}, rows


def _gate_failures(report: dict[str, Any], args: Namespace) -> list[dict[str, Any]]:
    summary = report["summary"]
    failures: list[dict[str, Any]] = []
    thresholds = {
        "prediction_accuracy": args.min_prediction_accuracy,
        "quality_accuracy": args.min_quality_accuracy,
    }
    for name, threshold in thresholds.items():
        if threshold is None:
            continue
        actual = float(summary.get(name) or 0.0)
        if actual < float(threshold):
            failures.append(
                {
                    "name": name,
                    "expected": float(threshold),
                    "actual": actual,
                    "message": f"{name} {actual:.4f} is below required {float(threshold):.4f}.",
                }
            )
    if args.max_false_accept_rate is not None:
        actual = float(summary.get("false_accept_rate") or 0.0)
        threshold = float(args.max_false_accept_rate)
        if actual > threshold:
            failures.append(
                {
                    "name": "false_accept_rate",
                    "expected": threshold,
                    "actual": actual,
                    "message": f"false_accept_rate {actual:.4f} is above allowed {threshold:.4f}.",
                }
            )
    return failures


def handle(args: Namespace) -> int:
    payloads = load_jsonl(args.labels)
    records = [item for item in payloads if isinstance(item, dict)]
    report, _ = _evaluate_records(records, domain_quality_threshold=args.domain_quality_threshold)
    report.update(
        {
            "schema_version": "1.0",
            "kind": "validate_labels",
            "label_set_path": str(Path(args.labels).expanduser()),
            "domain_quality_threshold": args.domain_quality_threshold,
            "generated_at": utc_now(),
        }
    )
    gate_failures = _gate_failures(report, args)
    report["quality_gate"] = {"passed": not gate_failures, "failures": gate_failures}
    json_path = args.json_out or default_report_path(args.project_root, "validate-labels")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="validate_labels"))
        write_json_report(report, json_path)
    if not args.quiet:
        summary = report["summary"]
        print("Skill Doctor Label Validation")
        print(f"total: {summary['total']}")
        print(f"prediction_accuracy: {summary['prediction_accuracy']:.2%}")
        print(f"quality_accuracy: {summary['quality_accuracy']:.2%}")
        print(f"report: {report['report_path']}")
    return EXIT_OK if not gate_failures else EXIT_QUALITY_GATE_FAILED
