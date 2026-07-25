import assert from "node:assert/strict";
import test from "node:test";
import {
  TraceRecorder,
  normalizeTokenUsage,
} from "../lib/trace-recorder.ts";
import { analyzeCase } from "../lib/demo-engine.ts";

function createRecorder() {
  let clock = 1000;
  return {
    recorder: new TraceRecorder({
      session: {
        id: "recorded-001",
        name: "Recorded trace",
        summary: "Provider-neutral recorder test",
        task: "Analyze all rows",
        expected: "Complete result",
        actual: "Preview-only result",
      },
      skill: {
        id: "spreadsheet-summary",
        version: "1.2.0",
        retrievalScore: 0.9,
        loaded: true,
        before: ["one", "two", "three", "four"],
      },
      now: () => clock,
    }),
    tick: (milliseconds) => {
      clock += milliseconds;
    },
  };
}

test("normalizes common provider token usage shapes", () => {
  assert.deepEqual(
    normalizeTokenUsage({
      input_tokens: 100,
      output_tokens: 30,
      input_tokens_details: { cached_tokens: 40 },
      output_tokens_details: { reasoning_tokens: 12 },
    }),
    {
      inputTokens: 100,
      outputTokens: 30,
      cachedInputTokens: 40,
      reasoningTokens: 12,
    },
  );
  assert.equal(
    normalizeTokenUsage({ cache_read_input_tokens: 55 })
      .cachedInputTokens,
    55,
  );
});

test("records a real step lifecycle and produces an analyzable trace", () => {
  const { recorder, tick } = createRecorder();
  recorder
    .observeRouting({
      applicableSkillId: "spreadsheet-summary",
      candidateSkillIds: ["spreadsheet-summary"],
      selectedSkillId: "spreadsheet-summary",
    })
    .observeLoading({
      loadedSkillIds: ["spreadsheet-summary"],
      missingResources: [],
    })
    .addExecutionCheck("toolSchemaChecks", {
      id: "tool",
      passed: true,
    })
    .addExecutionCheck("instructionChecks", {
      id: "instruction",
      passed: true,
    })
    .addExecutionCheck("requirementChecks", {
      id: "coverage",
      passed: false,
    });

  const step = recorder.startStep({
    id: "step-01",
    kind: "decision",
    title: "Uses preview rows",
    model: "gpt-5-mini",
  });
  tick(275);
  step.finish({
    detail: "The full dataset was not loaded.",
    status: "fault",
    usage: normalizeTokenUsage({
      input_tokens: 800,
      output_tokens: 120,
      input_tokens_details: { cached_tokens: 300 },
      output_tokens_details: { reasoning_tokens: 60 },
    }),
    evidence: "decision#1",
  });

  const input = recorder.validate();
  const result = analyzeCase(input);
  assert.equal(input.trace[0].durationMs, 275);
  assert.equal(input.trace[0].at, "00:00.0");
  assert.equal(result.diagnosis.taxonomy, "Content Gap");
  assert.equal(result.usage.totalTokens, 920);
});

test("rejects duplicate or unfinished steps", () => {
  const { recorder } = createRecorder();
  const first = recorder.startStep({
    id: "step-01",
    kind: "decision",
    title: "Pending",
    model: "gpt-5-mini",
  });

  assert.throws(
    () =>
      recorder.startStep({
        id: "step-01",
        kind: "decision",
        title: "Duplicate",
        model: "gpt-5-mini",
      }),
    /already exists/,
  );
  assert.throws(() => recorder.toTraceSession(), /unfinished steps/);

  first.finish({
    detail: "Finished once",
    status: "fault",
    usage: normalizeTokenUsage({ input_tokens: 1, output_tokens: 1 }),
  });
  assert.throws(
    () =>
      first.finish({
        detail: "Finished twice",
        status: "fault",
        usage: normalizeTokenUsage({}),
      }),
    /already finished/,
  );
});
