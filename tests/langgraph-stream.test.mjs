import assert from "node:assert/strict";
import test from "node:test";
import {
  consumeNdjson,
  createCandidateSkill,
  getAgentRun,
  getBenchmarkRun,
  listScenarios,
  listRejectionHistory,
  listAgentRuns,
  NdjsonParser,
  streamBenchmarkRun,
  subscribeAgentRuns,
  validateCandidateSkill,
  verifyRepair,
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

test("loads scenario catalog", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    assert.equal(String(url), "http://api.test/scenarios");
    return new Response(
      JSON.stringify({
        schema_version: "1.0",
        scenarios: [
          {
            id: "content-gap",
            name: "内容缺口",
            summary: "Skill 已加载但遗漏关键约束。",
            category: "skill",
            skill_id: "spreadsheet-summary",
            task: "读取全部订单。",
            expected: "全量汇总",
            actual: "预览汇总",
            executor: "fixture",
            repair_action: "patch_skill",
          },
        ],
      }),
      { status: 200 },
    );
  };

  try {
    const catalog = await listScenarios("http://api.test");

    assert.equal(catalog.scenarios[0].id, "content-gap");
    assert.equal(catalog.scenarios[0].skill_id, "spreadsheet-summary");
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

test("posts repair verification request", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), init });
    return new Response(
      JSON.stringify({
        schema_version: "1.0",
        status: "verified",
        decision: "ADOPT",
        policy: "strict",
        baseline: { run_id: "lg-base001", pass_rate: 0.5 },
        candidate: { run_id: "lg-cand001", pass_rate: 1 },
        delta: { pass_rate_delta: 0.5, regression_rate_delta: 0 },
        checks: [],
        reasons: [],
        saved_cases: { included: true, count: 0 },
        attribution: {},
        markdown: "# Skill Doctor Repair Verification\n",
      }),
      { status: 200 },
    );
  };

  try {
    const report = await verifyRepair(
      "lg-base001",
      "lg-cand001",
      "http://api.test/",
    );

    assert.equal(report.decision, "ADOPT");
    assert.equal(requests[0].url, "http://api.test/repairs/verify");
    assert.equal(requests[0].init.method, "POST");
    assert.deepEqual(JSON.parse(requests[0].init.body), {
      baseline_run_id: "lg-base001",
      candidate_run_id: "lg-cand001",
      include_saved_cases: true,
      decision_policy: "strict",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("creates and validates candidate skill", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), init });
    const body = String(url).includes("/validate")
      ? {
          schema_version: "1.0",
          status: "validated",
          decision: "ADOPT",
          candidate_id: "cand-demo001",
          skill_id: "trace-skill",
          base_version: "1.0.0",
          candidate_version: "1.0.0+candidate.1",
          baseline: { pass_rate: 0.5 },
          candidate: { pass_rate: 1 },
          delta: { pass_rate_delta: 0.5, fixed_cases: [], regressed_cases: [] },
          checks: [],
          reasons: [],
          rejection_memory: { matched_count: 0, constraints: [], matches: [], recorded: null },
          markdown: "# Skill Doctor Candidate Validation\n",
        }
      : {
          status: "created",
          path: "candidate_skills/cand-demo001.json",
          candidate: {
            candidate_id: "cand-demo001",
            status: "candidate_only",
            skill_id: "trace-skill",
            base_version: "1.0.0",
            candidate_version: "1.0.0+candidate.1",
          },
        };
    return new Response(JSON.stringify(body), { status: 200 });
  };

  try {
    const created = await createCandidateSkill("lg-source001", "http://api.test");
    const validation = await validateCandidateSkill(
      created.candidate.candidate_id,
      "http://api.test",
    );

    assert.equal(created.candidate.candidate_id, "cand-demo001");
    assert.equal(validation.decision, "ADOPT");
    assert.equal(
      requests[0].url,
      "http://api.test/repairs/candidates/from-run/lg-source001",
    );
    assert.equal(
      requests[1].url,
      "http://api.test/repairs/candidates/cand-demo001/validate",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("loads rejection history for a skill", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return new Response(
      JSON.stringify({
        schema_version: "1.0",
        skill_id: "trace-skill",
        count: 1,
        records: [
          {
            rejection_id: "rej-demo001",
            candidate_id: "cand-demo001",
            created_at: "2026-07-28T00:00:00Z",
            skill_id: "trace-skill",
            decision: "REJECT",
            failed_checks: ["candidate_passed"],
            reasons: ["未通过"],
            regressed_cases: [],
            patch_summary: "demo",
          },
        ],
      }),
      { status: 200 },
    );
  };

  try {
    const history = await listRejectionHistory("trace-skill", "http://api.test/");

    assert.equal(history.count, 1);
    assert.equal(history.records[0].rejection_id, "rej-demo001");
    assert.equal(requests[0], "http://api.test/repairs/rejections/trace-skill");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
