import assert from "node:assert/strict";
import test from "node:test";
import {
  compareBenchmarkRuns,
  summarizePairedResults,
} from "../lib/benchmark-engine.ts";
import { summarizeBenchmarkReports } from "../lib/benchmark-summary.ts";

function run(condition, passRate, tokens, passedIds) {
  const assertions = ["task", "skill"].map((id) => ({
    id,
    label: id,
    source: id === "task" ? "task" : "skill",
    passed: passedIds.includes(id),
    matched: passedIds.includes(id) ? id : null,
  }));
  return {
    id: condition,
    condition,
    status: "completed",
    executor: "codex-sdk",
    taskKind: "knowledge-probe",
    startedAt: "2026-07-25T00:00:00.000Z",
    durationMs: condition === "with_skill" ? 1200 : 1000,
    usage: {
      inputTokens: tokens - 10,
      outputTokens: 10,
      cachedInputTokens: 0,
      reasoningTokens: 0,
      totalTokens: tokens,
    },
    verifier: {
      framework: "pytest",
      passed: Math.round(passRate * assertions.length),
      failed: assertions.length - Math.round(passRate * assertions.length),
      total: assertions.length,
      passRate,
      assertions,
    },
    artifacts: {
      evidenceSnapshot: "evidence.json",
      codexJsonl: "codex.jsonl",
      pytestOutput: "pytest.txt",
      gitDiff: "git.diff",
    },
    error: null,
  };
}

test("computes pass-rate, token, duration and regression deltas", () => {
  const pair = compareBenchmarkRuns(
    "demo",
    "Demo",
    "test",
    run("without_skill", 0.5, 100, ["task"]),
    run("with_skill", 1, 125, ["task", "skill"]),
  );

  assert.equal(pair.comparison.outcome, "improved");
  assert.equal(pair.comparison.passRateDelta, 0.5);
  assert.equal(pair.comparison.tokenDelta, 25);
  assert.equal(pair.comparison.tokenOverheadRate, 0.25);
  assert.equal(pair.comparison.durationDeltaMs, 200);
  assert.equal(pair.comparison.regressionRate, 0);
});

test("marks a lost control assertion as a regression", () => {
  const pair = compareBenchmarkRuns(
    "demo",
    "Demo",
    "test",
    run("without_skill", 0.5, 100, ["task"]),
    run("with_skill", 0.5, 100, ["skill"]),
  );

  assert.equal(pair.comparison.outcome, "regressed");
  assert.deepEqual(pair.comparison.regressedAssertionIds, ["task"]);
  assert.equal(pair.comparison.regressionRate, 1);
});

test("summarizes only completed pairs", () => {
  const pair = compareBenchmarkRuns(
    "demo",
    "Demo",
    "test",
    run("without_skill", 0.5, 100, ["task"]),
    run("with_skill", 1, 125, ["task", "skill"]),
  );
  const incomplete = structuredClone(pair);
  incomplete.skillId = "blocked";
  incomplete.treatment.status = "blocked";
  incomplete.comparison.outcome = "incomplete";
  incomplete.comparison.passRateDelta = null;

  const summary = summarizePairedResults([pair, incomplete]);
  assert.equal(summary.pairs, 2);
  assert.equal(summary.completedPairs, 1);
  assert.equal(summary.improved, 1);
  assert.equal(summary.averagePassRateDelta, 0.5);
});

test("summarizes persisted benchmark reports for resume metrics", () => {
  const pair = compareBenchmarkRuns(
    "demo",
    "Demo",
    "test",
    run("without_skill", 0.5, 100, ["task"]),
    run("with_skill", 1, 125, ["task", "skill"]),
  );
  const summary = summarizeBenchmarkReports([
    {
      run_kind: "benchmark",
      run_id: "bm-test001",
      scenario: "content-gap",
      status: "completed",
      report: {
        schemaVersion: "1.0",
        runId: "bm-test001",
        generatedAt: "2026-07-28T00:00:00Z",
        executor: "fixture",
        taskKind: "knowledge-probe",
        isModelResult: false,
        dataset: { name: "test", sha256: "", selectedSkills: ["demo"] },
        summary: summarizePairedResults([pair]),
        pairs: [pair],
      },
    },
  ]);

  assert.equal(summary.sourceCount, 1);
  assert.equal(summary.completedPairs, 1);
  assert.equal(summary.repairSuccessRate, 1);
  assert.equal(summary.averagePassRateDelta, 0.5);
  assert.equal(summary.scenarioBreakdown[0].scenario, "content-gap");
});
