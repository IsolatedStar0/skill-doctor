from __future__ import annotations


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DIAGNOSIS_FAILED = 10
EXIT_QUALITY_GATE_FAILED = 20
EXIT_BENCH_FAILED = 30
EXIT_COMPARE_REJECTED = 40
EXIT_INTERRUPTED = 130


EXIT_CODE_DESCRIPTIONS = {
    EXIT_OK: "success",
    EXIT_ERROR: "general_error",
    EXIT_DIAGNOSIS_FAILED: "diagnosis_failed",
    EXIT_QUALITY_GATE_FAILED: "quality_gate_failed",
    EXIT_BENCH_FAILED: "bench_failed",
    EXIT_COMPARE_REJECTED: "compare_rejected",
    EXIT_INTERRUPTED: "interrupted",
}
