import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  deriveSignals,
  parseTraceSession,
  serializeTraceSession,
  TraceValidationError,
} from "../lib/trace-adapter.ts";
import { analyzeCase } from "../lib/demo-engine.ts";

const fixtureUrl = new URL(
  "../examples/traces/content-gap.json",
  import.meta.url,
);

test("imports and analyzes a canonical JSON trace", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  const input = parseTraceSession(payload);
  const result = analyzeCase(input);

  assert.equal(input.id, "imported-revenue-001");
  assert.equal(result.diagnosis.taxonomy, "Content Gap");
  assert.equal(result.validation.decision, "ADOPT");
  assert.equal(input.signals.correctSkillSelected, true);
  assert.equal(input.signals.skillCoversRequirement, false);
});

test("round-trips a validated trace without losing evidence", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  const first = parseTraceSession(payload);
  const second = parseTraceSession(serializeTraceSession(first));

  assert.deepEqual(second, first);
  assert.equal(second.trace[2].evidence, "decision#23 + skill.procedure:3");
});

test("rejects malformed traces before attribution", () => {
  assert.throws(
    () =>
      parseTraceSession({
        schemaVersion: "0.1",
        session: {},
        skill: { loaded: true },
        signals: { skillLoaded: false },
        events: [],
      }),
    (error) =>
      error instanceof TraceValidationError &&
      error.issues.some((issue) => issue.includes("schemaVersion")) &&
      error.issues.some((issue) => issue.includes("events")),
  );
});

test("rejects traces with ambiguous fault boundaries", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  payload.events[0].status = "fault";

  assert.throws(
    () => parseTraceSession(payload),
    /exactly one actionable fault/,
  );
});

test("derives attribution signals from runtime observations", () => {
  const signals = deriveSignals({
    routing: {
      applicableSkillId: "release",
      candidateSkillIds: ["release"],
      selectedSkillId: "release",
    },
    loading: {
      loadedSkillIds: [],
      missingResources: ["references/policy.md"],
    },
    execution: {
      toolSchemaChecks: [{ id: "publish", passed: true }],
      instructionChecks: [{ id: "procedure", passed: true }],
      requirementChecks: [{ id: "coverage", passed: true }],
    },
    externalErrors: [],
  });

  assert.equal(signals.correctSkillSelected, true);
  assert.equal(signals.skillLoaded, false);
  assert.equal(signals.externalFailure, null);
});

test("ignores caller-supplied signals in schema 1.1", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  payload.signals = {
    externalFailure: "permission",
    skillCoversRequirement: true,
  };
  const result = analyzeCase(parseTraceSession(payload));

  assert.equal(result.diagnosis.taxonomy, "Content Gap");
  assert.equal(result.diagnosis.ruleId, "ATTR-060");
});
