import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptLangGraphState,
  runEvidenceId,
} from "../lib/run-view-adapter.ts";

const usage = {
  input_tokens: 120,
  output_tokens: 30,
  cached_input_tokens: 20,
  reasoning_tokens: 10,
};

function execution(passRate, passed) {
  return {
    executor: "fixture",
    condition: "with_skill",
    passed,
    pass_rate: passRate,
    duration_ms: 80,
    usage,
    regression_rate: 0,
    summary: passed ? "all checks passed" : "skill check failed",
    assertions: [
      {
        id: "full-input",
        source: "skill",
        passed,
        detail: "process every input row",
      },
    ],
    artifacts: { pytest: "artifacts/pytest.txt" },
    error: null,
  };
}

test("adapts one backend Run into every frontend view model", () => {
  const state = {
    run_id: "run-unified-001",
    task: "Summarize all rows.",
    skill_id: "spreadsheet-summary",
    skill_version: "1.2.1",
    skill_content: "Read the complete input.",
    executor: "fixture",
    scenario: "content-gap",
    attempt: 1,
    max_attempts: 2,
    status: "passed",
    stop_reason: "repair_verified",
    events: [
      {
        sequence: 1,
        stage: "execute",
        status: "completed",
        attempt: 0,
        message: "baseline failed",
        usage,
        metadata: { pass_rate: 0.5 },
      },
      {
        sequence: 2,
        stage: "collect_evidence",
        status: "completed",
        attempt: 0,
        message: "snapshot created",
        usage: null,
        metadata: { execution_sha256: "abc123" },
      },
      {
        sequence: 3,
        stage: "attribute",
        status: "completed",
        attempt: 0,
        message: "content gap",
        usage: null,
        metadata: { taxonomy: "Content Gap" },
      },
      {
        sequence: 4,
        stage: "repair",
        status: "completed",
        attempt: 0,
        message: "patch created",
        usage: null,
        metadata: {},
      },
      {
        sequence: 5,
        stage: "execute",
        status: "completed",
        attempt: 1,
        message: "candidate passed",
        usage,
        metadata: { pass_rate: 1 },
      },
      {
        sequence: 6,
        stage: "verify",
        status: "completed",
        attempt: 1,
        message: "adopted",
        usage: null,
        metadata: { pass_rate_delta: 0.5 },
      },
    ],
    baseline_execution: execution(0.5, false),
    execution: execution(1, true),
    evidence_snapshot: {
      schema_version: "1.0",
      run_id: "run-unified-001",
      attempt: 0,
      skill_id: "spreadsheet-summary",
      condition: "with_skill",
      execution_sha256: "abc123",
      assertion_sha256: "def456",
      artifact_refs: ["artifacts/pytest.txt"],
    },
    attribution: {
      taxonomy: "Content Gap",
      cause: "skill",
      confidence: 0.91,
      responsibility: 0.92,
      action: "patch_skill",
      evidence_refs: ["def456", "full-input"],
      explanation: "Skill-owned checks failed.",
    },
    repair_patch: {
      patch_id: "patch-001",
      kind: "skill_patch",
      base_version: "1.2.0",
      next_version: "1.2.1",
      before: "Preview rows.",
      after: "Preview rows.\nRead the complete input.",
      evidence_refs: ["def456"],
      rollback_ref: "spreadsheet-summary@1.2.0",
    },
    verification: {
      decision: "ADOPT",
      baseline_pass_rate: 0.5,
      candidate_pass_rate: 1,
      pass_rate_delta: 0.5,
      regression_rate: 0,
      reasons: ["Pass rate changed by +50.0%."],
    },
  };

  const result = adaptLangGraphState(state);

  assert.equal(result.input.id, state.run_id);
  assert.equal(result.input.trace.length, state.events.length);
  assert.equal(result.usage.totalTokens, 300);
  assert.equal(result.diagnosis.taxonomy, "Content Gap");
  assert.equal(result.diagnosis.evidenceRefs[0], "def456");
  assert.equal(result.repair.kind, "skill_patch");
  assert.equal(result.validation.decision, "ADOPT");
  assert.deepEqual(result.validation.originalReplay, {
    before: 0.5,
    after: 1,
  });
  assert.equal(runEvidenceId(state), "abc123");
});
