import assert from "node:assert/strict";
import test from "node:test";
import {
  analyzeCase,
  demoCase,
  demoCases,
  diagnose,
  proposePatch,
  proposeRepair,
  runDemo,
  validatePatch,
} from "../lib/demo-engine.ts";

test("attributes the replayable CSV failure to a Content Gap", () => {
  const diagnosis = diagnose(demoCase);
  assert.equal(diagnosis.primaryFaultStep, "step-03");
  assert.equal(diagnosis.taxonomy, "Content Gap");
  assert.equal(diagnosis.action, "patch_skill");
  assert.ok(diagnosis.evidenceRefs.length >= 3);
});

test("produces a one-line scoped and reversible skill patch", () => {
  const diagnosis = diagnose(demoCase);
  const patch = proposePatch(demoCase, diagnosis);
  const changed = patch.before.filter(
    (line, index) => line !== patch.after[index],
  );
  assert.equal(patch.kind, "skill_patch");
  assert.equal(changed.length, 1);
  assert.equal(patch.scope, "procedure");
  assert.equal(patch.rollbackRef, "spreadsheet-summary@1.2.0");
});

test("adopts a skill patch only after replay improves without regression", () => {
  const diagnosis = diagnose(demoCase);
  const patch = proposePatch(demoCase, diagnosis);
  const result = validatePatch(demoCase, diagnosis, patch);
  assert.equal(result.originalReplay.after, 1);
  assert.equal(result.similarCases.after, 1);
  assert.equal(result.regression.after, result.regression.before);
  assert.equal(result.decision, "ADOPT");
});

test("detects a Loading Miss and routes it without changing the skill", () => {
  const input = demoCases.find((item) => item.id === "case-loader-017");
  assert.ok(input);
  const diagnosis = diagnose(input);
  const repair = proposeRepair(input, diagnosis);

  assert.equal(diagnosis.taxonomy, "Loading Miss");
  assert.equal(diagnosis.action, "patch_loader");
  assert.equal(repair.kind, "routing_action");
  assert.equal(repair.target, "loader");
  assert.equal(repair.mutationPolicy, "NO_SKILL_MUTATION");
});

test("prioritizes an external failure as Non-Skill Cause", () => {
  const input = demoCases.find((item) => item.id === "case-platform-009");
  assert.ok(input);
  const result = analyzeCase(input);

  assert.equal(result.diagnosis.taxonomy, "Non-Skill Cause");
  assert.equal(result.diagnosis.action, "split_non_skill");
  assert.equal(result.diagnosis.responsibility, 0.02);
  assert.equal(result.repair.kind, "routing_action");
  assert.equal(result.repair.target, "platform");
  assert.equal(result.validation.decision, "ROUTE");
});

test("refuses to create a Skill patch for non-Skill diagnoses", () => {
  const input = demoCases.find((item) => item.id === "case-loader-017");
  assert.ok(input);
  const diagnosis = diagnose(input);

  assert.throws(
    () => proposePatch(input, diagnosis),
    /Only skill-scoped diagnoses/,
  );
});

test("all bundled scenarios run deterministically", () => {
  const firstPass = demoCases.map((item) => runDemo(item.id));
  const secondPass = demoCases.map((item) => runDemo(item.id));

  assert.deepEqual(firstPass, secondPass);
  assert.deepEqual(
    firstPass.map((item) => item.diagnosis.taxonomy),
    ["Content Gap", "Loading Miss", "Non-Skill Cause"],
  );
  assert.deepEqual(
    firstPass.map((item) => item.validation.decision),
    ["ADOPT", "ROUTE", "ROUTE"],
  );
});
