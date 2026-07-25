import assert from "node:assert/strict";
import test from "node:test";
import {
  ATTRIBUTION_RULESET_VERSION,
  evaluateAttribution,
  listAttributionRules,
} from "../lib/attribution-engine.ts";
import { demoCase } from "../lib/demo-engine.ts";

function withSignals(signals) {
  return {
    ...structuredClone(demoCase),
    signals: {
      applicableSkillKnown: true,
      correctSkillInCandidates: true,
      correctSkillSelected: true,
      skillLoaded: true,
      toolSchemaValid: true,
      instructionFollowed: true,
      skillCoversRequirement: true,
      externalFailure: null,
      ...signals,
    },
  };
}

const taxonomyCases = [
  ["Non-Skill Cause", { externalFailure: "network" }, "ATTR-001"],
  ["Skill Recall Failure", { correctSkillInCandidates: false }, "ATTR-010"],
  ["Selection Error", { correctSkillSelected: false }, "ATTR-020"],
  ["Loading Miss", { skillLoaded: false }, "ATTR-030"],
  ["Tool Misuse", { toolSchemaValid: false }, "ATTR-040"],
  ["Instruction Violation", { instructionFollowed: false }, "ATTR-050"],
  ["Content Gap", { skillCoversRequirement: false }, "ATTR-060"],
];

for (const [taxonomy, signals, ruleId] of taxonomyCases) {
  test(`selects ${ruleId} for ${taxonomy}`, () => {
    const result = evaluateAttribution(withSignals(signals));
    assert.equal(result.taxonomy, taxonomy);
    assert.equal(result.ruleId, ruleId);
    assert.equal(result.ruleVersion, ATTRIBUTION_RULESET_VERSION);
    assert.equal(
      result.ruleEvaluations.filter((item) => item.selected).length,
      1,
    );
  });
}

test("uses deterministic priority when multiple signals fail", () => {
  const result = evaluateAttribution(
    withSignals({
      externalFailure: "permission",
      skillLoaded: false,
      skillCoversRequirement: false,
    }),
  );

  assert.equal(result.taxonomy, "Non-Skill Cause");
  assert.equal(result.ruleId, "ATTR-001");
  assert.equal(result.ruleEvaluations[0].priority, 10);
});

test("publishes a stable ordered rule catalog", () => {
  const catalog = listAttributionRules();
  assert.equal(catalog.length, 7);
  assert.deepEqual(
    catalog.map((item) => item.priority),
    [10, 20, 30, 40, 50, 60, 70],
  );
});
