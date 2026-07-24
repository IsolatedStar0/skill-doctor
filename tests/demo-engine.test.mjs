import assert from "node:assert/strict";
import test from "node:test";
import {
  demoCase,
  diagnose,
  proposePatch,
  runDemo,
  validatePatch,
} from "../lib/demo-engine.ts";

test("localizes the first actionable fault and attributes it to a content gap", () => {
  const diagnosis = diagnose(demoCase);
  assert.equal(diagnosis.primaryFaultStep, "step-03");
  assert.equal(diagnosis.taxonomy, "Content Gap");
  assert.equal(diagnosis.action, "patch_skill");
  assert.ok(diagnosis.evidenceRefs.length >= 3);
});

test("produces a one-line scoped and reversible patch", () => {
  const diagnosis = diagnose(demoCase);
  const patch = proposePatch(demoCase, diagnosis);
  const changed = patch.before.filter((line, index) => line !== patch.after[index]);
  assert.equal(changed.length, 1);
  assert.equal(patch.scope, "procedure");
  assert.equal(patch.rollbackRef, "spreadsheet-summary@1.2.0");
});

test("adopts only after replay improves without regression", () => {
  const diagnosis = diagnose(demoCase);
  const patch = proposePatch(demoCase, diagnosis);
  const result = validatePatch(demoCase, diagnosis, patch);
  assert.equal(result.originalReplay.after, 1);
  assert.equal(result.similarCases.after, 1);
  assert.equal(result.regression.after, result.regression.before);
  assert.equal(result.decision, "ADOPT");
});

test("runs the complete deterministic demo", () => {
  const result = runDemo();
  assert.equal(result.input.id, "case-revenue-042");
  assert.equal(result.diagnosis.taxonomy, "Content Gap");
  assert.equal(result.validation.decision, "ADOPT");
});
