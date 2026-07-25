import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  codexJsonlToTraceSession,
  parseCodexJsonl,
} from "../lib/codex-jsonl-adapter.ts";
import { analyzeCase, summarizeTokenUsage } from "../lib/demo-engine.ts";

const jsonlUrl = new URL(
  "../examples/codex/codex-exec-sample.jsonl",
  import.meta.url,
);
const contextUrl = new URL(
  "../examples/codex/trace-context.json",
  import.meta.url,
);

async function fixture() {
  const [jsonl, context] = await Promise.all([
    readFile(jsonlUrl, "utf8"),
    readFile(contextUrl, "utf8").then(JSON.parse),
  ]);
  return { jsonl, context };
}

test("maps official Codex JSONL event shapes into Trace 1.1", async () => {
  const { jsonl, context } = await fixture();
  const raw = codexJsonlToTraceSession(jsonl, context);
  const input = parseCodexJsonl(jsonl, context);

  assert.equal(raw.schemaVersion, "1.1");
  assert.equal(input.trace.length, 4);
  assert.equal(input.trace[1].id, "item_2");
  assert.equal(input.trace[1].status, "fault");
  assert.equal(input.trace[2].status, "downstream");
  assert.equal(input.trace[3].kind, "evaluation");
});

test("keeps turn-level usage on an auditable summary step", async () => {
  const { jsonl, context } = await fixture();
  const input = parseCodexJsonl(jsonl, context);
  const summary = input.trace.at(-1);
  const totals = summarizeTokenUsage(input.trace);

  assert.deepEqual(summary.usage, {
    inputTokens: 24763,
    outputTokens: 122,
    cachedInputTokens: 24448,
    reasoningTokens: 18,
  });
  assert.equal(totals.totalTokens, 24885);
  assert.equal(totals.freshInputTokens, 315);
});

test("feeds Codex observations into the existing attribution engine", async () => {
  const { jsonl, context } = await fixture();
  const result = analyzeCase(parseCodexJsonl(jsonl, context));

  assert.equal(result.diagnosis.taxonomy, "Content Gap");
  assert.equal(result.diagnosis.ruleId, "ATTR-060");
  assert.equal(result.diagnosis.primaryFaultStep, "item_2");
});

test("rejects malformed JSONL and unmatched fault selectors", async () => {
  const { jsonl, context } = await fixture();

  assert.throws(
    () => parseCodexJsonl('{"type": invalid}', context),
    /Invalid Codex JSONL/,
  );
  assert.throws(
    () =>
      parseCodexJsonl(jsonl, {
        ...context,
        fault: { ...context.fault, itemId: "missing" },
      }),
    /matched 0/,
  );
});
