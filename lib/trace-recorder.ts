import type {
  DemoCase,
  TokenUsage,
  TraceStep,
} from "./demo-engine.ts";
import {
  parseTraceSession,
  TRACE_SCHEMA_VERSION,
  type RawTraceSession,
  type TraceObservations,
} from "./trace-adapter.ts";

type RecorderSession = Omit<
  DemoCase,
  "signals" | "skill" | "trace"
>;

export type TraceRecorderConfig = {
  session: RecorderSession;
  skill: DemoCase["skill"];
  now?: () => number;
};

export type StepStart = Pick<
  TraceStep,
  "id" | "kind" | "title" | "model"
>;

export type StepFinish = Pick<
  TraceStep,
  "detail" | "status" | "usage"
> & {
  evidence?: string;
};

export type ProviderUsage = {
  inputTokens?: number;
  outputTokens?: number;
  cachedInputTokens?: number;
  reasoningTokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  input_tokens_details?: { cached_tokens?: number };
  output_tokens_details?: { reasoning_tokens?: number };
};

type RecordedStep = TraceStep & { sequence: number };

function finiteToken(value: unknown) {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    Number.isInteger(value) &&
    value >= 0
    ? value
    : 0;
}

export function normalizeTokenUsage(
  usage: ProviderUsage | undefined,
): TokenUsage {
  return {
    inputTokens: finiteToken(usage?.inputTokens ?? usage?.input_tokens),
    outputTokens: finiteToken(usage?.outputTokens ?? usage?.output_tokens),
    cachedInputTokens: finiteToken(
      usage?.cachedInputTokens ??
        usage?.input_tokens_details?.cached_tokens ??
        usage?.cache_read_input_tokens,
    ),
    reasoningTokens: finiteToken(
      usage?.reasoningTokens ??
        usage?.output_tokens_details?.reasoning_tokens,
    ),
  };
}

function formatElapsed(milliseconds: number) {
  const totalSeconds = Math.max(0, milliseconds) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds
    .toFixed(1)
    .padStart(4, "0")}`;
}

export class TraceRecorder {
  private readonly config: TraceRecorderConfig;
  private readonly now: () => number;
  private readonly startedAt: number;
  private readonly steps: RecordedStep[] = [];
  private readonly openStepIds = new Set<string>();
  private sequence = 0;
  private observations: TraceObservations = {
    routing: {
      applicableSkillId: null,
      candidateSkillIds: [],
      selectedSkillId: null,
    },
    loading: {
      loadedSkillIds: [],
      missingResources: [],
    },
    execution: {
      toolSchemaChecks: [],
      instructionChecks: [],
      requirementChecks: [],
    },
    externalErrors: [],
  };

  constructor(config: TraceRecorderConfig) {
    this.config = config;
    this.now = config.now ?? Date.now;
    this.startedAt = this.now();
  }

  observeRouting(input: TraceObservations["routing"]) {
    this.observations.routing = structuredClone(input);
    return this;
  }

  observeLoading(input: TraceObservations["loading"]) {
    this.observations.loading = structuredClone(input);
    return this;
  }

  addExecutionCheck(
    kind: "toolSchemaChecks" | "instructionChecks" | "requirementChecks",
    check: { id: string; passed: boolean },
  ) {
    this.observations.execution[kind].push({ ...check });
    return this;
  }

  addExternalError(
    error: TraceObservations["externalErrors"][number],
  ) {
    this.observations.externalErrors.push({ ...error });
    return this;
  }

  startStep(input: StepStart) {
    if (
      this.openStepIds.has(input.id) ||
      this.steps.some((step) => step.id === input.id)
    ) {
      throw new Error(`Trace step id already exists: ${input.id}`);
    }
    this.openStepIds.add(input.id);
    const startedAt = this.now();
    const sequence = this.sequence++;
    let finished = false;

    return {
      finish: (result: StepFinish) => {
        if (finished) {
          throw new Error(`Trace step already finished: ${input.id}`);
        }
        finished = true;
        this.openStepIds.delete(input.id);
        const step: RecordedStep = {
          ...input,
          ...result,
          sequence,
          at: formatElapsed(startedAt - this.startedAt),
          durationMs: Math.max(0, Math.round(this.now() - startedAt)),
        };
        this.steps.push(step);
        return step;
      },
    };
  }

  toTraceSession(): RawTraceSession {
    if (this.openStepIds.size > 0) {
      throw new Error(
        `Cannot export trace with unfinished steps: ${[
          ...this.openStepIds,
        ].join(", ")}`,
      );
    }
    return {
      schemaVersion: TRACE_SCHEMA_VERSION,
      session: { ...this.config.session },
      skill: structuredClone(this.config.skill),
      observations: structuredClone(this.observations),
      events: [...this.steps]
        .sort((left, right) => left.sequence - right.sequence)
        .map(
          (step) =>
            Object.fromEntries(
              Object.entries(step).filter(([key]) => key !== "sequence"),
            ) as TraceStep,
        ),
    };
  }

  validate(): DemoCase {
    return parseTraceSession(this.toTraceSession());
  }
}
