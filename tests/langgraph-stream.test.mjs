import assert from "node:assert/strict";
import test from "node:test";
import {
  consumeNdjson,
  NdjsonParser,
} from "../lib/langgraph-stream.ts";

test("parses NDJSON split across arbitrary chunks", () => {
  const values = [];
  const parser = new NdjsonParser((value) => values.push(value));

  parser.feed('{"run_id":"lg-1","events":[');
  parser.feed("]}\n\n");
  parser.feed('{"run_id":"lg-2","events":[]}');
  parser.finish();

  assert.deepEqual(
    values.map((value) => value.run_id),
    ["lg-1", "lg-2"],
  );
});

test("consumes a browser Response stream incrementally", async () => {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('{"sequence":1}\n{"seq'));
      controller.enqueue(encoder.encode('uence":2}\n'));
      controller.close();
    },
  });
  const values = [];

  await consumeNdjson(new Response(stream), (value) => values.push(value));

  assert.deepEqual(
    values.map((value) => value.sequence),
    [1, 2],
  );
});

test("surfaces streamed server errors", () => {
  const parser = new NdjsonParser(() => {});
  assert.throws(
    () => parser.feed('{"error":"invalid skill"}\n'),
    /invalid skill/,
  );
});
