import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function readJson(path) {
  return JSON.parse(await readFile(new URL(path, import.meta.url), "utf8"));
}

test("puck-rule-rca real trace dataset and report stay aligned", async () => {
  const dataset = await readJson(
    "../benchmarks/aime-traces/puck-rule-rca-real-2026-07-30.json",
  );
  const report = await readJson(
    "../reports/evaluations/puck-rule-rca-real-2026-07-30.json",
  );

  assert.equal(dataset.schema_version, "1.0");
  assert.equal(dataset.dataset_id, "puck-rule-rca-real-2026-07-30");
  assert.equal(dataset.cases.length, 9);
  assert.equal(dataset.summary.sample_count, dataset.cases.length);
  assert.equal(dataset.summary.doctor_passed, 7);
  assert.equal(dataset.summary.doctor_failed, 2);
  assert.equal(dataset.summary.doctor_pass_rate, 0.7778);
  assert.deepEqual(dataset.summary.business_verdict_distribution, {
    fail: 4,
    pass: 1,
    warning: 4,
  });
  assert.equal(report.dataset_id, dataset.dataset_id);
  assert.equal(report.summary.sample_count, dataset.summary.sample_count);
  assert.equal(report.case_table.length, dataset.cases.length);
  assert.deepEqual(
    report.quality_metrics.failed_case_ids,
    ["puck-rule-rca-real-002", "puck-rule-rca-real-007"],
  );
});

test("puck-rule-rca real traces are persisted as diagnostic cases", async () => {
  const dataset = await readJson(
    "../benchmarks/aime-traces/puck-rule-rca-real-2026-07-30.json",
  );

  for (const item of dataset.cases) {
    const diagnosticCase = await readJson(
      `../diagnostic_cases/${item.case_id}.json`,
    );

    assert.equal(diagnosticCase.case_id, item.case_id);
    assert.equal(diagnosticCase.source, "saved_run");
    assert.equal(diagnosticCase.trace.parent_run_id, item.run_id);
    assert.equal(diagnosticCase.trace.skill_id, "puck-rule-rca");
    assert.equal(diagnosticCase.expectation.status, item.doctor_result.status);
    assert.equal(diagnosticCase.expectation.cause, item.attribution.cause);
    assert.equal(diagnosticCase.expectation.fault_type, item.attribution.fault_type);
  }
});
