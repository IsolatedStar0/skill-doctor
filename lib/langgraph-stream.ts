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
  scenario: "content-gap" | "loading-miss" | "platform-error" | "network-error";
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
  scenario: "content-gap" | "loading-miss" | "platform-error" | "network-error";
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

export type DiagnosticCaseReport = {
  case_id: string;
  name: string;
  description: string;
  source: "built-in" | "custom" | "saved_run";
  passed: boolean;
  category: "healthy" | "skill" | "non_skill";
  repairable: boolean;
  run_id: string;
  status: string;
  stop_reason: string;
  skill_id: string;
  agent_source: "llm" | "rule-based" | "none";
  attribution: {
    taxonomy: string;
    cause: string;
    fault_type: string;
    action: string;
    confidence: number;
    explanation: string;
  };
  repair: {
    kind: string;
    revision_type: string;
    principle: string;
  } | null;
  verification: {
    decision: string;
    pass_rate_delta: number;
    regression_rate: number;
    reasons: string[];
  };
  checks: Array<{
    name: string;
    expected: unknown;
    actual: unknown;
    passed: boolean;
  }>;
};

export type DiagnosticSuiteReport = {
  schema_version: "1.0";
  suite_id: string;
  name: string;
  generated_at: string;
  status: "passed" | "failed";
  summary: {
    total: number;
    passed: number;
    failed: number;
    pass_rate: number;
    repairable: number;
    non_skill: number;
    llm_authored: number;
    saved_cases?: number;
  };
  cases: DiagnosticCaseReport[];
  markdown: string;
};

export type SavedDiagnosticCaseResponse = {
  status: "saved";
  path: string;
  case: {
    case_id: string;
    name: string;
    description: string;
    source: "saved_run";
  };
};

export type RepairPreview = {
  schema_version: "1.0";
  run_id: string;
  skill_id: string;
  status: "preview_only";
  repair_type: "skill_revision" | "loader_revision" | "manual_triage";
  can_apply: boolean;
  risk: "low" | "medium" | "high" | string;
  diagnosis: string;
  principle: string;
  attribution: {
    taxonomy: string;
    cause: string;
    fault_type: string;
    action: string;
    confidence: number;
    agent_source: "llm" | "rule-based" | "none";
    t_star?: number | null;
    fault_chain: number[];
  };
  suggested_patch: {
    summary: string;
    before: string;
    after: string;
    diff: string;
  };
  verification_plan: string[];
  notes: string[];
  can_apply_reason: string;
};

export type RepairVerificationRunSummary = {
  run_id: string;
  skill_id: string;
  status: string;
  stop_reason: string;
  pass_rate: number;
  regression_rate: number;
  passed: boolean;
  summary: string;
  attribution: {
    cause: string;
    fault_type: string;
    action: string;
  };
};

export type RepairVerificationReport = {
  schema_version: "1.0";
  status: "verified";
  decision: "ADOPT" | "REJECT";
  policy: "strict" | "balanced";
  baseline: RepairVerificationRunSummary;
  candidate: RepairVerificationRunSummary;
  delta: {
    pass_rate_delta: number;
    regression_rate_delta: number;
    status_changed: boolean;
  };
  checks: Array<{
    name: string;
    label: string;
    expected: unknown;
    actual: unknown;
    passed: boolean;
  }>;
  reasons: string[];
  saved_cases: {
    included: boolean;
    count: number;
  };
  attribution: {
    baseline_cause: string;
    baseline_fault_type: string;
    candidate_cause: string;
    candidate_fault_type: string;
  };
  markdown: string;
};

export type CandidateSkillResponse = {
  status: "created";
  path: string;
  candidate: {
    schema_version: "1.0";
    candidate_id: string;
    status: "candidate_only";
    created_from_run_id: string;
    skill_id: string;
    base_version: string;
    candidate_version: string;
    repair_type: string;
    risk: string;
    diagnosis: string;
    principle: string;
    skill_content_before: string;
    skill_content_after: string;
    rejection_memory?: RejectionMemorySummary;
  };
};

export type RejectionMemoryRecord = {
  rejection_id: string;
  candidate_id: string;
  created_at: string;
  skill_id: string;
  fault_type?: string | null;
  action?: string | null;
  decision: "REJECT" | string;
  failed_checks: string[];
  reasons: string[];
  regressed_cases: string[];
  patch_summary: string;
  match_reason?: string;
};

export type RejectionMemorySummary = {
  matched_count: number;
  constraints: string[];
  matches: RejectionMemoryRecord[];
  recorded?: {
    rejection_id: string;
    path: string;
    failed_checks: string[];
  } | null;
};

export type RejectionHistoryResponse = {
  schema_version: "1.0";
  skill_id: string | null;
  count: number;
  records: RejectionMemoryRecord[];
};

export type CandidateValidationReport = {
  schema_version: "1.0";
  status: "validated";
  decision: "ADOPT" | "REJECT";
  policy: "strict" | "balanced";
  candidate_id: string;
  skill_id: string;
  base_version: string;
  candidate_version: string;
  baseline: DiagnosticSuiteReport["summary"];
  candidate: DiagnosticSuiteReport["summary"];
  delta: {
    pass_rate_delta: number;
    fixed_cases: string[];
    regressed_cases: string[];
  };
  checks: Array<{
    name: string;
    label: string;
    expected: unknown;
    actual: unknown;
    passed: boolean;
  }>;
  reasons: string[];
  rejection_memory: RejectionMemorySummary;
  markdown: string;
};

export type RunRegistryEvent = {
  type: "run.updated";
  updated_at: string;
  state: LangGraphState | BenchmarkState;
};

export type BenchmarkRunRequest = {
  executor: "fixture" | "replay" | "codex";
  scenario: "content-gap" | "loading-miss" | "platform-error" | "network-error";
  skill_id: string;
  task?: string;
  skill_content?: string;
  codex_timeout_ms?: number;
};

export type ScenarioCatalogItem = {
  id: LangGraphRunRequest["scenario"];
  name: string;
  summary: string;
  category: "skill" | "loader" | "platform" | "tool";
  skill_id: string;
  task: string;
  expected: string;
  actual: string;
  executor: "fixture" | "replay" | "codex";
  repair_action: string;
};

export type ScenarioCatalogResponse = {
  schema_version: "1.0";
  scenarios: ScenarioCatalogItem[];
};

function apiBaseUrl(value?: string) {
  return (
    value ??
    process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL ??
    "http://localhost:8010"
  ).replace(/\/$/, "");
}

export async function listAgentRuns(value?: string, limit = 200) {
  const response = await fetch(`${apiBaseUrl(value)}/runs?limit=${limit}`);
  if (!response.ok) {
    throw new Error(`Run list request failed with ${response.status}.`);
  }
  const payload = (await response.json()) as { runs: RunSummary[] };
  return payload.runs;
}

export async function listScenarios(value?: string) {
  const response = await fetch(`${apiBaseUrl(value)}/scenarios`);
  if (!response.ok) {
    throw new Error(`Scenario catalog request failed with ${response.status}.`);
  }
  return (await response.json()) as ScenarioCatalogResponse;
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

export async function runDefaultDiagnostics(value?: string) {
  const response = await fetch(`${apiBaseUrl(value)}/diagnostics/default`);
  if (!response.ok) {
    throw new Error(`Diagnostic request failed with ${response.status}.`);
  }
  return (await response.json()) as DiagnosticSuiteReport;
}

export async function saveRunAsDiagnosticCase(runId: string, value?: string) {
  const response = await fetch(
    `${apiBaseUrl(value)}/diagnostics/cases/from-run/${encodeURIComponent(runId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    },
  );
  if (!response.ok) {
    throw new Error(`Save diagnostic case failed with ${response.status}.`);
  }
  return (await response.json()) as SavedDiagnosticCaseResponse;
}

export async function createRepairPreview(runId: string, value?: string) {
  const response = await fetch(
    `${apiBaseUrl(value)}/repairs/preview/${encodeURIComponent(runId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_full_skill: false }),
    },
  );
  if (!response.ok) {
    throw new Error(`Repair preview failed with ${response.status}.`);
  }
  return (await response.json()) as RepairPreview;
}

export async function verifyRepair(
  baselineRunId: string,
  candidateRunId: string,
  value?: string,
) {
  const response = await fetch(`${apiBaseUrl(value)}/repairs/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      baseline_run_id: baselineRunId,
      candidate_run_id: candidateRunId,
      include_saved_cases: true,
      decision_policy: "strict",
    }),
  });
  if (!response.ok) {
    throw new Error(`Repair verification failed with ${response.status}.`);
  }
  return (await response.json()) as RepairVerificationReport;
}

export async function createCandidateSkill(runId: string, value?: string) {
  const response = await fetch(
    `${apiBaseUrl(value)}/repairs/candidates/from-run/${encodeURIComponent(runId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_full_skill: true }),
    },
  );
  if (!response.ok) {
    throw new Error(`Candidate skill creation failed with ${response.status}.`);
  }
  return (await response.json()) as CandidateSkillResponse;
}

export async function validateCandidateSkill(
  candidateId: string,
  value?: string,
) {
  const response = await fetch(
    `${apiBaseUrl(value)}/repairs/candidates/${encodeURIComponent(candidateId)}/validate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        include_default_cases: true,
        include_saved_cases: true,
        decision_policy: "strict",
      }),
    },
  );
  if (!response.ok) {
    throw new Error(`Candidate validation failed with ${response.status}.`);
  }
  return (await response.json()) as CandidateValidationReport;
}

export async function listRejectionHistory(skillId: string, value?: string) {
  const response = await fetch(
    `${apiBaseUrl(value)}/repairs/rejections/${encodeURIComponent(skillId)}`,
  );
  if (!response.ok) {
    throw new Error(`Rejection history request failed with ${response.status}.`);
  }
  return (await response.json()) as RejectionHistoryResponse;
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
