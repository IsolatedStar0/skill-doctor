from __future__ import annotations

from argparse import Namespace
import json
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
    command.add_argument(
        "--failures-out",
        type=Path,
        help="Write quality/prediction failure triage rows as JSONL for rerun or debugging.",
    )
    command.add_argument(
        "--include-domain-details",
        action="store_true",
        help="Include full domain_quality details in each case row.",
    )
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


def _nested_value(payload: Any, key: str) -> Any:
    """Return the first non-empty value for key from a native puck payload."""
    if isinstance(payload, dict):
        value = payload.get(key)
        if value is not None and value != "":
            return value
        for nested_key in ("rca_detail", "details", "extra"):
            nested = payload.get(nested_key)
            value = _nested_value(nested, key)
            if value is not None and value != "":
                return value
    elif isinstance(payload, list):
        for item in payload:
            value = _nested_value(item, key)
            if value is not None and value != "":
                return value
    return None


def _confidence(agent_output: dict[str, Any], raw: dict[str, Any]) -> float | None:
    value = _nested_value(agent_output, "confidence")
    if value is None:
        value = _nested_value(raw, "confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _detail_items(agent_output: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    for payload in (agent_output, raw):
        details = payload.get("details")
        if not isinstance(details, list):
            rca_detail = payload.get("rca_detail")
            if isinstance(rca_detail, list):
                details = rca_detail
            elif isinstance(rca_detail, dict):
                details = rca_detail.get("details")
        if isinstance(details, list):
            return [item for item in details if isinstance(item, dict)]
    return []


def _existing_path(value: Any, base_dir: Path | None) -> str | None:
    if value is None or value == "":
        return None
    path = Path(str(value)).expanduser()
    candidates = [path]
    if not path.is_absolute() and base_dir is not None:
        candidates = [base / path for base in (base_dir, *base_dir.parents)] + candidates
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def _load_native_evidence_record(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _native_trace_refs(
    record: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> tuple[dict[str, str], list[str]]:
    artifacts: dict[str, str] = {}
    refs: list[str] = []
    native_path = _existing_path(_nested_value(record, "native_evidence_path"), base_dir)
    if native_path:
        artifacts["native_evidence_path"] = native_path
        refs.append(f"native_evidence:{native_path}")

    native_record = _load_native_evidence_record(native_path)
    bridge = native_record.get("bridge_status") or {}
    integration = native_record.get("integration_status") or {}
    skilldoctor_bridge = integration.get("skilldoctor_bridge") if isinstance(integration, dict) else {}
    if not isinstance(skilldoctor_bridge, dict):
        skilldoctor_bridge = {}
    trace_dir = bridge.get("trace_dir") or skilldoctor_bridge.get("trace_dir")
    trace_dir_path = _existing_path(trace_dir, base_dir)
    if trace_dir_path:
        artifacts["trace_dir"] = trace_dir_path
        refs.append(f"trace_dir:{trace_dir_path}")

    native_evidence = native_record.get("native_evidence")
    if isinstance(native_evidence, dict):
        windows = native_evidence.get("windows")
        if isinstance(windows, dict):
            window_names = ",".join(str(name) for name in windows.keys())
            refs.append(f"native_evidence:windows={window_names}")
            for name, window in windows.items():
                if not isinstance(window, dict):
                    continue
                raw_path = _existing_path(window.get("raw_mcp_output_path"), base_dir)
                if raw_path:
                    artifacts[f"native_series_{name}"] = raw_path
    return artifacts, refs


def _state_from_label(record: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, Any]:
    agent_output = record.get("agent_output") or {}
    raw = record.get("raw") or {}
    prediction = _prediction(record)
    confidence = _confidence(agent_output, raw)
    rationale = str(
        agent_output.get("rationale")
        or agent_output.get("reason")
        or agent_output.get("rca_content")
        or _nested_value(agent_output.get("rca_detail"), "rca_content")
        or raw.get("rationale")
        or raw.get("rca_content")
        or ""
    ).strip()
    chart_url = _nested_value(agent_output, "chart_url") or _nested_value(raw, "chart_url")
    details = _detail_items(agent_output, raw)
    if chart_url and not details:
        details.append(
            {
                "name": "chart_evidence",
                "status": "pass",
                "reason": rationale or "提供了图表证据。",
            }
        )
    raw_business_result = {**raw, **agent_output}
    raw_business_result.setdefault("rca_filter", prediction)
    raw_business_result.setdefault("rca_content", rationale)
    raw_business_result.setdefault("confidence", confidence)
    raw_business_result.setdefault("rca_detail", details)
    if chart_url:
        raw_business_result.setdefault("chart_url", chart_url)
    artifacts = {"chart_url": chart_url} if chart_url else {}
    native_artifacts, native_refs = _native_trace_refs(record, base_dir=base_dir)
    artifacts.update(native_artifacts)
    runtime_events = []
    if chart_url or native_refs:
        runtime_events.append(
            {
                "stage": "agent.analyze.tool_calls",
                "status": "completed",
                "metadata": {"total": 1 + len(native_refs), "failed": 0},
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
            "extra": {"raw_business_result": raw_business_result},
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
            "evidence_refs": (["artifact:chart_url"] if chart_url else []) + native_refs,
        },
    }


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _failure_type(
    *,
    prediction_correct: bool,
    predicted: bool,
    expected: bool,
    expected_good: bool | None,
    predicted_good: bool,
) -> str | None:
    if expected_good is False and predicted_good:
        return "false_accept"
    if expected_good is True and not predicted_good:
        return "false_reject"
    if not prediction_correct and predicted and not expected:
        return "filter_false_positive"
    if not prediction_correct and not predicted and expected:
        return "filter_false_negative"
    return None


def _failure_triage(
    *,
    row: dict[str, Any],
    domain_quality: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any] | None:
    failure = row.get("failure_type")
    if not failure:
        return None
    evidence_score = domain_quality.get("evidence_score") or {}
    missing_required = evidence_score.get("missing_required_checks") or []
    missing_artifacts = evidence_score.get("missing_artifacts") or []
    failed_critical = evidence_score.get("failed_critical_checks") or []
    failed_checks = evidence_score.get("failed_checks") or []
    evidence_refs = quality.get("evidence_refs") or []
    native_refs = [ref for ref in evidence_refs if str(ref).startswith("native_evidence:")]

    root_cause = "manual_inspection_required"
    recommended_action = "inspect_domain_quality"
    if failure == "false_accept" and (missing_required or missing_artifacts) and not native_refs:
        root_cause = "native_evidence_missing"
        recommended_action = "rerun_with_native_evidence"
    elif failure == "false_accept" and (failed_critical or failed_checks):
        root_cause = "scorer_not_strict_enough"
        recommended_action = "tighten_profile_or_domain_scorer"
    elif failure == "false_reject":
        root_cause = "scorer_too_strict"
        recommended_action = "inspect_false_reject_before_relaxing_rules"
    elif failure.startswith("filter_"):
        root_cause = "business_decision_mismatch"
        recommended_action = "rerun_or_fix_skill_business_logic"

    required_evidence = list(missing_required)
    if missing_artifacts:
        required_evidence.extend(f"artifact:{item}" for item in missing_artifacts)
    return {
        "case_id": row["case_id"],
        "failure_type": failure,
        "root_cause": root_cause,
        "recommended_action": recommended_action,
        "predicted_filter": row["predicted_filter"],
        "expected_filter": row["expected_filter"],
        "expected_quality_good": row["expected_quality_good"],
        "domain_quality_score": row["domain_quality_score"],
        "domain_quality_passed": row["domain_quality_passed"],
        "missing_required_checks": list(missing_required),
        "missing_artifacts": list(missing_artifacts),
        "failed_checks": list(failed_checks),
        "failed_critical_checks": list(failed_critical),
        "required_evidence": required_evidence,
        "findings": row.get("findings") or [],
        "evidence_refs": evidence_refs,
        "domain_quality": domain_quality,
    }


def _failure_counts(failures: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for failure in failures:
        key = str(failure.get("failure_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _write_jsonl_report(rows: list[dict[str, Any]], path: Path) -> str:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return str(target)


def _evaluate_records(
    records: list[dict[str, Any]],
    *,
    domain_quality_threshold: float,
    label_base_dir: Path | None = None,
    include_domain_details: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
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

        quality = score_state(_state_from_label(record, base_dir=label_base_dir))
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

        quality_prediction_correct = expected_good is None or expected_good == predicted_good
        row = {
            "case_id": case_id,
            "predicted_filter": predicted,
            "expected_filter": expected,
            "prediction_correct": prediction_correct,
            "expected_quality_good": expected_good,
            "domain_quality_score": round(domain_score, 4),
            "domain_quality_passed": predicted_good,
            "quality_prediction_correct": quality_prediction_correct,
            "failure_type": _failure_type(
                prediction_correct=prediction_correct,
                predicted=predicted,
                expected=expected,
                expected_good=expected_good,
                predicted_good=predicted_good,
            ),
            "rule_name": (record.get("input") or {}).get("rule_name"),
            "group_detail_name": (record.get("input") or {}).get("group_detail_name"),
            "findings": domain_quality.get("findings") or [],
            "evidence_refs": quality.get("evidence_refs") or [],
        }
        if include_domain_details:
            row["domain_quality"] = domain_quality
        triage = _failure_triage(row=row, domain_quality=domain_quality, quality=quality)
        if triage:
            failures.append(triage)
        rows.append(row)

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
    return {
        "summary": summary,
        "cases": rows,
        "skipped": skipped,
        "failure_analysis": {
            "total": len(failures),
            "by_type": _failure_counts(failures),
            "failures": failures if include_domain_details else [],
        },
    }, rows


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
    report, _ = _evaluate_records(
        records,
        domain_quality_threshold=args.domain_quality_threshold,
        label_base_dir=Path(args.labels).expanduser().parent,
        include_domain_details=args.include_domain_details,
    )
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
    if args.failures_out:
        failures = report.get("failure_analysis", {}).get("failures") or []
        if not failures:
            detailed_report, _ = _evaluate_records(
                records,
                domain_quality_threshold=args.domain_quality_threshold,
                label_base_dir=Path(args.labels).expanduser().parent,
                include_domain_details=True,
            )
            failures = detailed_report.get("failure_analysis", {}).get("failures") or []
        report["failures_path"] = _write_jsonl_report(failures, args.failures_out)
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
