import type { PairedBenchmarkReport } from "./benchmark-engine.ts";

export type LangGraphTokenUsage = {
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
};

export type BusinessResultDetail = {
  name: string;
  status: "pass" | "fail" | "warning";
  reason: string;
};

export type BusinessResult = {
  verdict: string;
  verdict_type: "pass" | "fail" | "warning";
  confidence?: number | null;
  details: BusinessResultDetail[];
  extra: Record<string, unknown>;
};

export type LangGraphEvent = {
  sequence: number;
  stage: string;
  status: "started" | "completed" | "failed" | "skipped";
  attempt: number;
  message: string;
  usage: LangGraphTokenUsage | null;
  metadata: Record<string, string | number | boolean | null>;
};

export type LangGraphExecution = {
  executor: string;
  condition: string;
  passed: boolean;
  pass_rate: number;
  duration_ms: number;
  usage: LangGraphTokenUsage;
  regression_rate: number;
  summary: string;
  assertions: {
    id: string;
    source: "task" | "skill" | "system";
    passed: boolean;
    detail?: string | null;
  }[];
  artifacts: Record<string, string>;
  error?: string | null;
};

export type LangGraphState = {
  run_kind?: "agent";
  run_id: string;
  parent_run_id?: string | null;
  task: string;
  skill_id: string;
  skill_version: string;
  skill_content: string;
  executor: string;
  scenario: string;
  condition?: "standard" | "without_skill" | "with_skill";
  repair_enabled?: boolean;
  attempt: number;
  max_attempts: number;
  status: string;
  stop_reason: string;
  business_result?: BusinessResult | null;
  observability?: {
    provider: "langsmith";
    enabled: boolean;
    status: "disabled" | "active" | "completed" | "failed" | "degraded";
    project: string;
    trace_id?: string;
    trace_url?: string;
    error?: string;
  };
  events: LangGraphEvent[];
  execution?: LangGraphExecution;
  baseline_execution?: LangGraphExecution;
  evidence_snapshot?: {
    schema_version: "1.0";
    run_id: string;
    attempt: number;
    skill_id: string;
    condition: string;
    execution_sha256: string;
    assertion_sha256: string;
    artifact_refs: string[];
  };
  attribution?: {
    taxonomy: string;
    cause: "skill" | "routing" | "loader" | "tool" | "platform";
    confidence: number;
    responsibility: number;
    action:
      | "patch_skill"
      | "patch_routing"
      | "patch_loader"
      | "split_non_skill";
    evidence_refs: string[];
    explanation: string;
    fault_type?: "skill_wrong" | "skill_missing" | "reasoning_wrong" | "unknown";
    t_star?: number | null;
    fault_chain?: number[];
    improvement_principle?: string;
    skill_attributions?: Array<Record<string, unknown>>;
    agent_conclusion?: string;
    agent_reason?: string;
    agent_source?: "llm" | "rule-based" | "none";
  };
  repair_patch?: {
    patch_id: string;
    kind: string;
    base_version: string;
    next_version: string;
    before: string;
    after: string;
    evidence_refs: string[];
    rollback_ref: string;
  };
  verification?: {
    decision: "ADOPT" | "REJECT";
    baseline_pass_rate: number;
    candidate_pass_rate: number;
    pass_rate_delta: number;
    regression_rate: number;
    reasons: string[];
  };
};

export type LangGraphRunRequest = {
  executor: "fixture" | "replay" | "codex";
  scenario: "content-gap" | "network-error";
  skill_id: string;
  stream_delay_ms?: number;
  codex_timeout_ms?: number;
};

export type RunSummary = {
  run_kind: "agent" | "benchmark";
  run_id: string;
  parent_run_id: string | null;
  skill_id: string;
  skill_version: string;
  executor: string;
  scenario: string;
  condition: string;
  attempt: number;
  max_attempts: number;
  status: string;
  stop_reason: string;
  event_count: number;
  updated_at: string;
};

export type BenchmarkState = {
  run_kind: "benchmark";
  run_id: string;
  parent_run_id: null;
  skill_id: string;
  skill_version: string;
  executor: "fixture" | "replay" | "codex";
  scenario: "content-gap" | "network-error";
  condition: "paired";
  attempt: 0;
  max_attempts: 0;
  status: "pending" | "running" | "completed" | "failed";
  stop_reason: string;
  task: string;
  events: LangGraphEvent[];
  control_run_id: string | null;
  treatment_run_id: string | null;
  control: PairedBenchmarkReport["pairs"][number]["control"] | null;
  treatment: PairedBenchmarkReport["pairs"][number]["treatment"] | null;
  report: PairedBenchmarkReport | null;
  error: string | null;
};

export type RunRegistryEvent = {
  type: "run.updated";
  updated_at: string;
  state: LangGraphState | BenchmarkState;
};

export type BenchmarkRunRequest = {
  executor: "fixture" | "replay" | "codex";
  scenario: "content-gap" | "network-error";
  skill_id: string;
  task?: string;
  skill_content?: string;
  codex_timeout_ms?: number;
};

function apiBaseUrl(value?: string) {
  return (
    value ??
    process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL ??
    "http://localhost:8010"
  ).replace(/\/$/, "");
}

export async function listAgentRuns(value?: string) {
  const response = await fetch(`${apiBaseUrl(value)}/runs`);
  if (!response.ok) {
    throw new Error(`Run list request failed with ${response.status}.`);
  }
  const payload = (await response.json()) as { runs: RunSummary[] };
  return payload.runs;
}

export async function getAgentRun(runId: string, value?: string) {
  const response = await fetch(
    `${apiBaseUrl(value)}/runs/${encodeURIComponent(runId)}`,
  );
  if (!response.ok) {
    throw new Error(`Run request failed with ${response.status}.`);
  }
  return (await response.json()) as LangGraphState;
}

export async function getBenchmarkRun(runId: string, value?: string) {
  const response = await fetch(
    `${apiBaseUrl(value)}/benchmarks/${encodeURIComponent(runId)}`,
  );
  if (!response.ok) {
    throw new Error(`Benchmark request failed with ${response.status}.`);
  }
  return (await response.json()) as BenchmarkState;
}

export async function streamBenchmarkRun(
  request: BenchmarkRunRequest,
  onState: (state: BenchmarkState) => void,
  options: {
    signal?: AbortSignal;
    apiBaseUrl?: string;
  } = {},
) {
  const response = await fetch(
    `${apiBaseUrl(options.apiBaseUrl)}/benchmarks/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: options.signal,
    },
  );
  await consumeNdjson<BenchmarkState>(response, onState);
}

export function subscribeAgentRuns(
  onEvent: (event: RunRegistryEvent) => void,
  onStatus: (status: "connected" | "reconnecting") => void,
  value?: string,
) {
  const source = new EventSource(`${apiBaseUrl(value)}/runs/events`);
  source.onopen = () => onStatus("connected");
  source.onerror = () => onStatus("reconnecting");
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as RunRegistryEvent);
    } catch {
      // Ignore malformed events and let EventSource keep the subscription alive.
    }
  };
  return () => source.close();
}

export class NdjsonParser<T> {
  private buffer = "";
  private readonly onValue: (value: T) => void;

  constructor(onValue: (value: T) => void) {
    this.onValue = onValue;
  }

  feed(chunk: string) {
    this.buffer += chunk;
    const lines = this.buffer.split(/\r?\n/);
    this.buffer = lines.pop() ?? "";
    for (const line of lines) this.parse(line);
  }

  finish() {
    if (this.buffer.trim()) this.parse(this.buffer);
    this.buffer = "";
  }

  private parse(line: string) {
    if (!line.trim()) return;
    const value = JSON.parse(line) as T & { error?: string };
    if (value && typeof value === "object" && value.error) {
      throw new Error(value.error);
    }
    this.onValue(value);
  }
}

export async function consumeNdjson<T>(
  response: Response,
  onValue: (value: T) => void,
) {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Stream request failed with ${response.status}.`);
  }
  if (!response.body) throw new Error("The server returned no response stream.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new NdjsonParser<T>(onValue);
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.feed(decoder.decode(value, { stream: true }));
  }
  parser.feed(decoder.decode());
  parser.finish();
}

export async function streamLangGraphRun(
  request: LangGraphRunRequest,
  onState: (state: LangGraphState) => void,
  options: {
    signal?: AbortSignal;
    apiBaseUrl?: string;
  } = {},
) {
  const response = await fetch(`${apiBaseUrl(options.apiBaseUrl)}/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal: options.signal,
  });
  await consumeNdjson<LangGraphState>(response, onState);
}
