export type LangGraphTokenUsage = {
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
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
  run_id: string;
  task: string;
  skill_id: string;
  skill_version: string;
  skill_content: string;
  executor: string;
  scenario: string;
  attempt: number;
  max_attempts: number;
  status: string;
  stop_reason: string;
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
  const apiBaseUrl =
    options.apiBaseUrl ??
    process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL ??
    "http://localhost:8010";
  const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal: options.signal,
  });
  await consumeNdjson<LangGraphState>(response, onState);
}
