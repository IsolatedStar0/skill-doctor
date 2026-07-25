import type {
  CaseSignals,
  DemoCase,
  TokenUsage,
  TraceStep,
} from "./demo-engine.ts";

export const TRACE_SCHEMA_VERSION = "1.1";
export const LEGACY_TRACE_SCHEMA_VERSION = "1.0";

export type TraceObservations = {
  routing: {
    applicableSkillId: string | null;
    candidateSkillIds: string[];
    selectedSkillId: string | null;
  };
  loading: {
    loadedSkillIds: string[];
    missingResources: string[];
  };
  execution: {
    toolSchemaChecks: Array<{ id: string; passed: boolean }>;
    instructionChecks: Array<{ id: string; passed: boolean }>;
    requirementChecks: Array<{ id: string; passed: boolean }>;
  };
  externalErrors: Array<{
    category: Exclude<CaseSignals["externalFailure"], null>;
    evidence: string;
  }>;
};

export type RawTraceSession = {
  schemaVersion: typeof TRACE_SCHEMA_VERSION;
  session: {
    id: string;
    name: string;
    summary: string;
    task: string;
    expected: string;
    actual: string;
  };
  skill: DemoCase["skill"];
  observations: TraceObservations;
  events: TraceStep[];
};

export class TraceValidationError extends Error {
  readonly issues: string[];

  constructor(issues: string[]) {
    super(`Invalid trace session:\n- ${issues.join("\n- ")}`);
    this.name = "TraceValidationError";
    this.issues = issues;
  }
}

const traceKinds = new Set(["skill", "tool", "decision", "evaluation"]);
const traceStatuses = new Set(["ok", "fault", "downstream"]);
const externalFailures = new Set(["permission", "network", "service", null]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(
  value: unknown,
  path: string,
  issues: string[],
): Record<string, unknown> {
  if (!isRecord(value)) {
    issues.push(`${path} must be an object`);
    return {};
  }
  return value;
}

function requireString(
  value: unknown,
  path: string,
  issues: string[],
): string {
  if (typeof value !== "string" || value.trim() === "") {
    issues.push(`${path} must be a non-empty string`);
    return "";
  }
  return value;
}

function requireBoolean(
  value: unknown,
  path: string,
  issues: string[],
): boolean {
  if (typeof value !== "boolean") {
    issues.push(`${path} must be a boolean`);
    return false;
  }
  return value;
}

function requireNumber(
  value: unknown,
  path: string,
  issues: string[],
): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    issues.push(`${path} must be a finite number`);
    return 0;
  }
  return value;
}

function requireNonnegativeInteger(
  value: unknown,
  path: string,
  issues: string[],
): number {
  const parsed = requireNumber(value, path, issues);
  if (!Number.isInteger(parsed) || parsed < 0) {
    issues.push(`${path} must be a non-negative integer`);
    return 0;
  }
  return parsed;
}

function parseTokenUsage(
  value: unknown,
  path: string,
  issues: string[],
  required: boolean,
): TokenUsage {
  if (!isRecord(value)) {
    if (required) issues.push(`${path} must be an object`);
    return {
      inputTokens: 0,
      outputTokens: 0,
      cachedInputTokens: 0,
      reasoningTokens: 0,
    };
  }
  const usage = {
    inputTokens: requireNonnegativeInteger(
      value.inputTokens,
      `${path}.inputTokens`,
      issues,
    ),
    outputTokens: requireNonnegativeInteger(
      value.outputTokens,
      `${path}.outputTokens`,
      issues,
    ),
    cachedInputTokens: requireNonnegativeInteger(
      value.cachedInputTokens,
      `${path}.cachedInputTokens`,
      issues,
    ),
    reasoningTokens: requireNonnegativeInteger(
      value.reasoningTokens,
      `${path}.reasoningTokens`,
      issues,
    ),
  };
  if (usage.cachedInputTokens > usage.inputTokens) {
    issues.push(`${path}.cachedInputTokens cannot exceed inputTokens`);
  }
  if (usage.reasoningTokens > usage.outputTokens) {
    issues.push(`${path}.reasoningTokens cannot exceed outputTokens`);
  }
  return usage;
}

function optionalStringOrNull(
  value: unknown,
  path: string,
  issues: string[],
): string | null {
  if (value === null) return null;
  return requireString(value, path, issues);
}

function parseStringArray(
  value: unknown,
  path: string,
  issues: string[],
): string[] {
  if (
    !Array.isArray(value) ||
    value.some((item) => typeof item !== "string")
  ) {
    issues.push(`${path} must be an array of strings`);
    return [];
  }
  return value;
}

function parseChecks(
  value: unknown,
  path: string,
  issues: string[],
): Array<{ id: string; passed: boolean }> {
  if (!Array.isArray(value) || value.length === 0) {
    issues.push(`${path} must be a non-empty array`);
    return [];
  }
  return value.map((item, index) => {
    const source = requireRecord(item, `${path}[${index}]`, issues);
    return {
      id: requireString(source.id, `${path}[${index}].id`, issues),
      passed: requireBoolean(
        source.passed,
        `${path}[${index}].passed`,
        issues,
      ),
    };
  });
}

function parseSignals(
  value: unknown,
  issues: string[],
): CaseSignals {
  const source = requireRecord(value, "signals", issues);
  const externalFailure = source.externalFailure;
  if (!externalFailures.has(externalFailure as never)) {
    issues.push(
      "signals.externalFailure must be permission, network, service, or null",
    );
  }

  return {
    applicableSkillKnown: requireBoolean(
      source.applicableSkillKnown,
      "signals.applicableSkillKnown",
      issues,
    ),
    correctSkillInCandidates: requireBoolean(
      source.correctSkillInCandidates,
      "signals.correctSkillInCandidates",
      issues,
    ),
    correctSkillSelected: requireBoolean(
      source.correctSkillSelected,
      "signals.correctSkillSelected",
      issues,
    ),
    skillLoaded: requireBoolean(
      source.skillLoaded,
      "signals.skillLoaded",
      issues,
    ),
    toolSchemaValid: requireBoolean(
      source.toolSchemaValid,
      "signals.toolSchemaValid",
      issues,
    ),
    instructionFollowed: requireBoolean(
      source.instructionFollowed,
      "signals.instructionFollowed",
      issues,
    ),
    skillCoversRequirement: requireBoolean(
      source.skillCoversRequirement,
      "signals.skillCoversRequirement",
      issues,
    ),
    externalFailure: externalFailures.has(externalFailure as never)
      ? (externalFailure as CaseSignals["externalFailure"])
      : null,
  };
}

function parseObservations(
  value: unknown,
  issues: string[],
): TraceObservations {
  const source = requireRecord(value, "observations", issues);
  const routing = requireRecord(
    source.routing,
    "observations.routing",
    issues,
  );
  const loading = requireRecord(
    source.loading,
    "observations.loading",
    issues,
  );
  const execution = requireRecord(
    source.execution,
    "observations.execution",
    issues,
  );
  const externalErrors = source.externalErrors;
  if (!Array.isArray(externalErrors)) {
    issues.push("observations.externalErrors must be an array");
  }

  return {
    routing: {
      applicableSkillId: optionalStringOrNull(
        routing.applicableSkillId,
        "observations.routing.applicableSkillId",
        issues,
      ),
      candidateSkillIds: parseStringArray(
        routing.candidateSkillIds,
        "observations.routing.candidateSkillIds",
        issues,
      ),
      selectedSkillId: optionalStringOrNull(
        routing.selectedSkillId,
        "observations.routing.selectedSkillId",
        issues,
      ),
    },
    loading: {
      loadedSkillIds: parseStringArray(
        loading.loadedSkillIds,
        "observations.loading.loadedSkillIds",
        issues,
      ),
      missingResources: parseStringArray(
        loading.missingResources,
        "observations.loading.missingResources",
        issues,
      ),
    },
    execution: {
      toolSchemaChecks: parseChecks(
        execution.toolSchemaChecks,
        "observations.execution.toolSchemaChecks",
        issues,
      ),
      instructionChecks: parseChecks(
        execution.instructionChecks,
        "observations.execution.instructionChecks",
        issues,
      ),
      requirementChecks: parseChecks(
        execution.requirementChecks,
        "observations.execution.requirementChecks",
        issues,
      ),
    },
    externalErrors: Array.isArray(externalErrors)
      ? externalErrors.map((item, index) => {
          const path = `observations.externalErrors[${index}]`;
          const error = requireRecord(item, path, issues);
          if (
            !externalFailures.has(error.category as never) ||
            error.category === null
          ) {
            issues.push(`${path}.category is not supported`);
          }
          return {
            category:
              error.category === "permission" ||
              error.category === "network" ||
              error.category === "service"
                ? error.category
                : "service",
            evidence: requireString(
              error.evidence,
              `${path}.evidence`,
              issues,
            ),
          };
        })
      : [],
  };
}

export function deriveSignals(
  observations: TraceObservations,
): CaseSignals {
  const applicableSkillId = observations.routing.applicableSkillId;
  const selectedSkillId = observations.routing.selectedSkillId;
  return {
    applicableSkillKnown: applicableSkillId !== null,
    correctSkillInCandidates:
      applicableSkillId !== null &&
      observations.routing.candidateSkillIds.includes(applicableSkillId),
    correctSkillSelected:
      applicableSkillId !== null && selectedSkillId === applicableSkillId,
    skillLoaded:
      selectedSkillId !== null &&
      observations.loading.loadedSkillIds.includes(selectedSkillId) &&
      observations.loading.missingResources.length === 0,
    toolSchemaValid: observations.execution.toolSchemaChecks.every(
      (check) => check.passed,
    ),
    instructionFollowed: observations.execution.instructionChecks.every(
      (check) => check.passed,
    ),
    skillCoversRequirement: observations.execution.requirementChecks.every(
      (check) => check.passed,
    ),
    externalFailure: observations.externalErrors[0]?.category ?? null,
  };
}

function observationsFromSignals(
  input: DemoCase,
): TraceObservations {
  const applicableSkillId = input.signals.applicableSkillKnown
    ? input.skill.id
    : null;
  const selectedSkillId = input.signals.correctSkillSelected
    ? input.skill.id
    : "other-skill";
  return {
    routing: {
      applicableSkillId,
      candidateSkillIds: input.signals.correctSkillInCandidates
        ? [input.skill.id]
        : [],
      selectedSkillId,
    },
    loading: {
      loadedSkillIds: input.signals.skillLoaded ? [selectedSkillId] : [],
      missingResources: input.signals.skillLoaded
        ? []
        : ["unresolved-resource"],
    },
    execution: {
      toolSchemaChecks: [
        { id: "tool-schema", passed: input.signals.toolSchemaValid },
      ],
      instructionChecks: [
        { id: "instruction", passed: input.signals.instructionFollowed },
      ],
      requirementChecks: [
        {
          id: "requirement",
          passed: input.signals.skillCoversRequirement,
        },
      ],
    },
    externalErrors: input.signals.externalFailure
      ? [
          {
            category: input.signals.externalFailure,
            evidence: "serialized-signal",
          },
        ]
      : [],
  };
}

function parseSkill(
  value: unknown,
  issues: string[],
): DemoCase["skill"] {
  const source = requireRecord(value, "skill", issues);
  const before = source.before;
  if (
    !Array.isArray(before) ||
    before.some((line) => typeof line !== "string")
  ) {
    issues.push("skill.before must be an array of strings");
  }
  const retrievalScore = requireNumber(
    source.retrievalScore,
    "skill.retrievalScore",
    issues,
  );
  if (retrievalScore < 0 || retrievalScore > 1) {
    issues.push("skill.retrievalScore must be between 0 and 1");
  }

  return {
    id: requireString(source.id, "skill.id", issues),
    version: requireString(source.version, "skill.version", issues),
    retrievalScore,
    loaded: requireBoolean(source.loaded, "skill.loaded", issues),
    before: Array.isArray(before)
      ? before.filter((line): line is string => typeof line === "string")
      : [],
  };
}

function parseEvents(
  value: unknown,
  issues: string[],
  requireMetrics: boolean,
): TraceStep[] {
  if (!Array.isArray(value) || value.length === 0) {
    issues.push("events must be a non-empty array");
    return [];
  }

  const events = value.map((event, index): TraceStep => {
    const path = `events[${index}]`;
    const source = requireRecord(event, path, issues);
    const kind = source.kind;
    const status = source.status;
    const durationMs =
      source.durationMs === undefined && !requireMetrics
        ? 0
        : requireNonnegativeInteger(
            source.durationMs,
            `${path}.durationMs`,
            issues,
          );

    if (!traceKinds.has(kind as string)) {
      issues.push(`${path}.kind is not supported`);
    }
    if (!traceStatuses.has(status as string)) {
      issues.push(`${path}.status is not supported`);
    }
    if (
      source.evidence !== undefined &&
      typeof source.evidence !== "string"
    ) {
      issues.push(`${path}.evidence must be a string when provided`);
    }

    return {
      id: requireString(source.id, `${path}.id`, issues),
      at: requireString(source.at, `${path}.at`, issues),
      durationMs,
      kind: traceKinds.has(kind as string)
        ? (kind as TraceStep["kind"])
        : "decision",
      title: requireString(source.title, `${path}.title`, issues),
      detail: requireString(source.detail, `${path}.detail`, issues),
      status: traceStatuses.has(status as string)
        ? (status as TraceStep["status"])
        : "fault",
      model:
        source.model === undefined && !requireMetrics
          ? "unknown"
          : requireString(source.model, `${path}.model`, issues),
      usage: parseTokenUsage(
        source.usage,
        `${path}.usage`,
        issues,
        requireMetrics,
      ),
      evidence:
        typeof source.evidence === "string" ? source.evidence : undefined,
    };
  });

  const ids = events.map((event) => event.id);
  if (new Set(ids).size !== ids.length) {
    issues.push("events must have unique ids");
  }
  if (events.filter((event) => event.status === "fault").length !== 1) {
    issues.push("events must contain exactly one actionable fault");
  }

  return events;
}

export function parseTraceSession(payload: unknown): DemoCase {
  const issues: string[] = [];
  const root = requireRecord(payload, "trace", issues);
  if (
    root.schemaVersion !== TRACE_SCHEMA_VERSION &&
    root.schemaVersion !== LEGACY_TRACE_SCHEMA_VERSION
  ) {
    issues.push(
      `schemaVersion must equal ${TRACE_SCHEMA_VERSION} or ${LEGACY_TRACE_SCHEMA_VERSION}`,
    );
  }

  const session = requireRecord(root.session, "session", issues);
  const observations =
    root.schemaVersion === TRACE_SCHEMA_VERSION
      ? parseObservations(root.observations, issues)
      : null;
  const result: DemoCase = {
    id: requireString(session.id, "session.id", issues),
    name: requireString(session.name, "session.name", issues),
    summary: requireString(session.summary, "session.summary", issues),
    task: requireString(session.task, "session.task", issues),
    expected: requireString(session.expected, "session.expected", issues),
    actual: requireString(session.actual, "session.actual", issues),
    skill: parseSkill(root.skill, issues),
    signals: observations
      ? deriveSignals(observations)
      : parseSignals(root.signals, issues),
    trace: parseEvents(
      root.events,
      issues,
      root.schemaVersion === TRACE_SCHEMA_VERSION,
    ),
  };

  if (result.skill.loaded !== result.signals.skillLoaded) {
    issues.push("skill.loaded and signals.skillLoaded must agree");
  }

  if (issues.length > 0) {
    throw new TraceValidationError(issues);
  }
  return result;
}

export function serializeTraceSession(input: DemoCase): RawTraceSession {
  return {
    schemaVersion: TRACE_SCHEMA_VERSION,
    session: {
      id: input.id,
      name: input.name,
      summary: input.summary,
      task: input.task,
      expected: input.expected,
      actual: input.actual,
    },
    skill: input.skill,
    observations: observationsFromSignals(input),
    events: input.trace,
  };
}
