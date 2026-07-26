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
};

export type LangGraphState = {
  run_id: string;
  skill_id: string;
  skill_version: string;
  executor: string;
  attempt: number;
  max_attempts: number;
  status: string;
  stop_reason: string;
  events: LangGraphEvent[];
  execution?: LangGraphExecution;
  baseline_execution?: LangGraphExecution;
  attribution?: {
    taxonomy: string;
    cause: string;
    confidence: number;
    explanation: string;
  };
  repair_patch?: {
    patch_id: string;
    kind: string;
    base_version: string;
    next_version: string;
    before: string;
    after: string;
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
