import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  analyzeCase,
  demoCase,
  diagnose,
  proposePatch,
} from "../lib/demo-engine.ts";

const manifestUrl = new URL(
  "./fixtures/attribution-regression.json",
  import.meta.url,
);

function buildRegressionCase(entry) {
  const input = structuredClone(demoCase);
  input.id = entry.id;
  input.signals = {
    applicableSkillKnown: true,
    correctSkillInCandidates: true,
    correctSkillSelected: true,
    skillLoaded: true,
    toolSchemaValid: true,
    instructionFollowed: true,
    skillCoversRequirement: true,
    externalFailure: null,
    ...entry.signals,
  };
  input.skill.loaded = input.signals.skillLoaded;
  return input;
}

test("keeps the seven-class regression manifest stable", async () => {
  const manifest = JSON.parse(await readFile(manifestUrl, "utf8"));
  assert.equal(manifest.schemaVersion, "1.0");
  assert.equal(manifest.cases.length, 7);

  for (const entry of manifest.cases) {
    const result = analyzeCase(buildRegressionCase(entry));
    assert.equal(
      result.diagnosis.taxonomy,
      entry.expected.taxonomy,
      `${entry.id}: taxonomy`,
    );
    assert.equal(
      result.diagnosis.ruleId,
      entry.expected.ruleId,
      `${entry.id}: rule`,
    );
    assert.equal(
      result.repair.kind,
      entry.expected.repairKind,
      `${entry.id}: repair`,
    );
    assert.equal(
      result.validation.decision,
      entry.expected.decision,
      `${entry.id}: decision`,
    );
  }
});

test("refuses an unregistered Tool Misuse patch strategy", () => {
  const input = buildRegressionCase({
    id: "tool-review",
    signals: { toolSchemaValid: false },
  });
  const diagnosis = diagnose(input);

  assert.equal(diagnosis.action, "patch_skill");
  assert.throws(
    () => proposePatch(input, diagnosis),
    /No verified patch strategy/,
  );

  const result = analyzeCase(input);
  assert.equal(result.repair.kind, "review_action");
  assert.equal(result.repair.mutationPolicy, "NO_SKILL_MUTATION");
  assert.equal(result.validation.decision, "NEEDS_REVIEW");
});

test("refuses an unregistered Instruction Violation patch strategy", () => {
  const input = buildRegressionCase({
    id: "instruction-review",
    signals: { instructionFollowed: false },
  });
  const result = analyzeCase(input);

  assert.equal(result.diagnosis.taxonomy, "Instruction Violation");
  assert.equal(result.repair.kind, "review_action");
  assert.equal(result.validation.decision, "NEEDS_REVIEW");
});
