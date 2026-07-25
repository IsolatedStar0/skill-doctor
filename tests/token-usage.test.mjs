import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  demoCase,
  stepTokenTotal,
  summarizeTokenUsage,
} from "../lib/demo-engine.ts";
import {
  parseTraceSession,
  TraceValidationError,
} from "../lib/trace-adapter.ts";

const fixtureUrl = new URL(
  "../examples/traces/content-gap.json",
  import.meta.url,
);

test("aggregates token usage without double-counting cache or reasoning", () => {
  const summary = summarizeTokenUsage(demoCase.trace);

  assert.equal(summary.inputTokens, 4440);
  assert.equal(summary.outputTokens, 582);
  assert.equal(summary.totalTokens, 5022);
  assert.equal(summary.cachedInputTokens, 2610);
  assert.equal(summary.reasoningTokens, 224);
  assert.equal(summary.freshInputTokens, 1830);
  assert.equal(summary.visibleOutputTokens, 358);
  assert.equal(summary.durationMs, 3670);
  assert.equal(summary.hottestStepId, "step-03");
  assert.equal(summary.hottestStepTokens, 1474);
});

test("keeps per-step total equal to input plus output", () => {
  for (const step of demoCase.trace) {
    assert.equal(
      stepTokenTotal(step),
      step.usage.inputTokens + step.usage.outputTokens,
    );
  }
});

test("imports token metrics from Trace 1.1", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  const input = parseTraceSession(payload);
  const summary = summarizeTokenUsage(input.trace);

  assert.equal(input.trace[2].model, "gpt-5-mini");
  assert.equal(input.trace[2].durationMs, 1180);
  assert.equal(input.trace[2].usage.reasoningTokens, 136);
  assert.ok(summary.totalTokens > 0);
});

test("rejects impossible token accounting", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  payload.events[0].usage.cachedInputTokens =
    payload.events[0].usage.inputTokens + 1;
  payload.events[1].usage.reasoningTokens =
    payload.events[1].usage.outputTokens + 1;

  assert.throws(
    () => parseTraceSession(payload),
    (error) =>
      error instanceof TraceValidationError &&
      error.issues.some((issue) =>
        issue.includes("cachedInputTokens cannot exceed inputTokens"),
      ) &&
      error.issues.some((issue) =>
        issue.includes("reasoningTokens cannot exceed outputTokens"),
      ),
  );
});

test("requires token metrics for Trace 1.1 events", async () => {
  const payload = JSON.parse(await readFile(fixtureUrl, "utf8"));
  delete payload.events[0].usage;
  delete payload.events[0].durationMs;

  assert.throws(
    () => parseTraceSession(payload),
    (error) =>
      error instanceof TraceValidationError &&
      error.issues.some((issue) => issue.includes("events[0].usage")) &&
      error.issues.some((issue) => issue.includes("events[0].durationMs")),
  );
});
