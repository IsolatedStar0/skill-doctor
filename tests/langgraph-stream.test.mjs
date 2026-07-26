import assert from "node:assert/strict";
import test from "node:test";
import {
  consumeNdjson,
  getAgentRun,
  getBenchmarkRun,
  listAgentRuns,
  NdjsonParser,
  streamBenchmarkRun,
  subscribeAgentRuns,
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

test("loads run registry summaries and selected snapshots", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return new Response(
      JSON.stringify(
        String(url).endsWith("/runs")
          ? { runs: [{ run_id: "lg-list001" }] }
          : { run_id: "lg-list001", events: [] },
      ),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };

  try {
    const runs = await listAgentRuns("http://api.test/");
    const state = await getAgentRun("lg-list001", "http://api.test/");

    assert.equal(runs[0].run_id, "lg-list001");
    assert.equal(state.run_id, "lg-list001");
    assert.deepEqual(requests, [
      "http://api.test/runs",
      "http://api.test/runs/lg-list001",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("subscribes to cross-run SSE registry events", () => {
  const OriginalEventSource = globalThis.EventSource;
  class FakeEventSource {
    static latest;

    constructor(url) {
      this.url = url;
      FakeEventSource.latest = this;
    }

    close() {
      this.closed = true;
    }
  }
  globalThis.EventSource = FakeEventSource;
  const received = [];
  const statuses = [];

  try {
    const unsubscribe = subscribeAgentRuns(
      (event) => received.push(event.state.run_id),
      (status) => statuses.push(status),
      "http://api.test",
    );
    FakeEventSource.latest.onopen();
    FakeEventSource.latest.onmessage({
      data: JSON.stringify({
        type: "run.updated",
        updated_at: "2026-07-26T00:00:00Z",
        state: { run_id: "lg-sse001" },
      }),
    });

    assert.equal(FakeEventSource.latest.url, "http://api.test/runs/events");
    assert.deepEqual(statuses, ["connected"]);
    assert.deepEqual(received, ["lg-sse001"]);
    unsubscribe();
    assert.equal(FakeEventSource.latest.closed, true);
  } finally {
    globalThis.EventSource = OriginalEventSource;
  }
});

test("streams dynamic paired benchmark states", async () => {
  const originalFetch = globalThis.fetch;
  const encoder = new TextEncoder();
  const states = [
    {
      run_kind: "benchmark",
      run_id: "bm-stream001",
      status: "pending",
      events: [],
    },
    {
      run_kind: "benchmark",
      run_id: "bm-stream001",
      status: "completed",
      events: [{ stage: "benchmark.completed" }],
    },
  ];
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "http://api.test/benchmarks/stream");
    assert.equal(init.method, "POST");
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(`${states.map(JSON.stringify).join("\n")}\n`),
        );
        controller.close();
      },
    });
    return new Response(stream, { status: 200 });
  };
  const received = [];

  try {
    await streamBenchmarkRun(
      {
        executor: "fixture",
        scenario: "content-gap",
        skill_id: "tdd-workflow",
      },
      (state) => received.push(state.status),
      { apiBaseUrl: "http://api.test" },
    );
    assert.deepEqual(received, ["pending", "completed"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("loads a persisted benchmark parent Run", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        run_kind: "benchmark",
        run_id: "bm-saved001",
        status: "completed",
      }),
      { status: 200 },
    );

  try {
    const state = await getBenchmarkRun(
      "bm-saved001",
      "http://api.test",
    );
    assert.equal(state.run_id, "bm-saved001");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
