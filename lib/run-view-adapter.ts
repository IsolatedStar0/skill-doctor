import type { RuleEvaluation } from "./attribution-engine.ts";
import {
  summarizeTokenUsage,
  type DemoCase,
  type Diagnosis,
  type RepairPlan,
  type Taxonomy,
  type TraceStep,
  type ValidationResult,
} from "./demo-engine.ts";
import type {
  LangGraphEvent,
  LangGraphState,
} from "./langgraph-stream.ts";

const taxonomies: Taxonomy[] = [
  "Skill Recall Failure",
  "Selection Error",
  "Loading Miss",
  "Instruction Violation",
  "Tool Misuse",
  "Content Gap",
  "Non-Skill Cause",
];

function taxonomyOf(value?: string): Taxonomy {
  return taxonomies.includes(value as Taxonomy)
    ? (value as Taxonomy)
    : "Tool Misuse";
}

function eventKind(stage: string): TraceStep["kind"] {
  if (stage === "prepare" || stage === "repair") return "skill";
  if (
    stage === "execute" ||
    stage.includes("command") ||
    stage.includes("file_change") ||
    stage.includes("tool_call") ||
    stage.includes("web_search")
  ) {
    return "tool";
  }
  if (
    stage === "collect_evidence" ||
    stage === "attribute" ||
    stage === "verify" ||
    stage === "promote" ||
    stage === "finalize"
  ) {
    return "evaluation";
  }
  return "decision";
}

function eventStatus(event: LangGraphEvent): TraceStep["status"] {
  if (event.status === "failed") return "fault";
  if (event.status === "skipped") return "downstream";
  return "ok";
}

function eventDuration(state: LangGraphState, event: LangGraphEvent) {
  if (event.stage !== "execute") return 0;
  const execution =
    event.attempt === 0 ? state.baseline_execution : state.execution;
  return execution?.duration_ms ?? 0;
}

function evidenceFor(event: LangGraphEvent) {
  const entries = Object.entries(event.metadata);
  if (!entries.length) return undefined;
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key}:${String(value)}`)
    .join(" · ");
}

function toTrace(state: LangGraphState): TraceStep[] {
  return state.events.map((event) => ({
    id: `run-${String(event.sequence).padStart(3, "0")}`,
    at: `#${String(event.sequence).padStart(3, "0")}`,
    durationMs: eventDuration(state, event),
    kind: eventKind(event.stage),
    title: event.stage,
    detail: event.message,
    status: eventStatus(event),
    model: state.executor,
    usage: {
      inputTokens: event.usage?.input_tokens ?? 0,
      outputTokens: event.usage?.output_tokens ?? 0,
      cachedInputTokens: event.usage?.cached_input_tokens ?? 0,
      reasoningTokens: event.usage?.reasoning_tokens ?? 0,
    },
    evidence: evidenceFor(event),
  }));
}

function ruleEvaluations(taxonomy: Taxonomy): RuleEvaluation[] {
  return taxonomies.map((candidate, index) => ({
    ruleId: `RUNTIME-${String(index + 1).padStart(2, "0")}`,
    priority: (index + 1) * 10,
    taxonomy: candidate,
    matched: candidate === taxonomy,
    selected: candidate === taxonomy,
    reason:
      candidate === taxonomy
        ? "Selected by the LangGraph attribution node."
        : "Excluded by the LangGraph attribution node.",
  }));
}

function toDiagnosis(state: LangGraphState, trace: TraceStep[]): Diagnosis {
  const attribution = state.attribution;
  const taxonomy = taxonomyOf(attribution?.taxonomy);
  const firstFault =
    trace.find((step) => step.status === "fault") ??
    trace.find((step) => step.title === "execute") ??
    trace[0];
  const evidenceRefs =
    attribution?.evidence_refs ??
    (state.evidence_snapshot
      ? [
          state.evidence_snapshot.execution_sha256,
          state.evidence_snapshot.assertion_sha256,
        ]
      : []);

  return {
    primaryFaultStep: firstFault?.id ?? "pending",
    faultChain: trace
      .filter(
        (step) =>
          step.status === "fault" ||
          ["attribute", "repair", "verify"].includes(step.title),
      )
      .map((step) => step.id),
    taxonomy,
    confidence: attribution?.confidence ?? 0,
    responsibility: attribution?.responsibility ?? 0,
    mechanism: attribution?.explanation ?? "Attribution is still pending.",
    evidenceRefs,
    action: attribution?.action ?? "split_non_skill",
    ruleId: "LANGGRAPH-RUNTIME",
    ruleVersion: "agent-state-v1",
    ruleEvaluations: ruleEvaluations(taxonomy),
  };
}

function changedLine(before: string[], after: string[]) {
  const limit = Math.max(before.length, after.length);
  for (let index = 0; index < limit; index += 1) {
    if (before[index] !== after[index]) return index + 1;
  }
  return Math.max(1, limit);
}

function toRepair(state: LangGraphState): RepairPlan {
  const patch = state.repair_patch;
  if (patch) {
    const before = patch.before.split(/\r?\n/);
    const after = patch.after.split(/\r?\n/);
    return {
      kind: "skill_patch",
      patchId: patch.patch_id,
      targetSkill: state.skill_id,
      baseVersion: patch.base_version,
      nextVersion: patch.next_version,
      scope: "procedure",
      evidenceRefs: patch.evidence_refs,
      rollbackRef: patch.rollback_ref,
      before,
      after,
      changedLine: changedLine(before, after),
    };
  }

  const cause = state.attribution?.cause;
  const target =
    cause === "loader" ? "loader" : cause === "routing" ? "router" : "platform";
  return {
    kind: "routing_action",
    actionId: `route-${state.run_id}`,
    target,
    title: "Keep Skill immutable and route the failure.",
    detail:
      state.attribution?.explanation ??
      "No safe Skill mutation is available for the current Run.",
    operations: [
      `route cause=${cause ?? "pending"} to ${target}`,
      "retain the Evidence Snapshot for replay",
    ],
    evidenceRefs: state.attribution?.evidence_refs ?? [],
    mutationPolicy: "NO_SKILL_MUTATION",
  };
}

function toValidation(state: LangGraphState): ValidationResult {
  const verification = state.verification;
  const baseline =
    verification?.baseline_pass_rate ??
    state.baseline_execution?.pass_rate ??
    0;
  const candidate =
    verification?.candidate_pass_rate ?? state.execution?.pass_rate ?? baseline;
  const regressionRate =
    verification?.regression_rate ?? state.execution?.regression_rate ?? 0;
  const decision =
    verification?.decision ??
    (state.status === "passed" ? "ADOPT" : "NEEDS_REVIEW");

  return {
    decision,
    originalReplay: { before: baseline, after: candidate },
    similarCases: { before: baseline, after: candidate, sampleSize: 1 },
    regression: {
      before: 1,
      after: 1 - regressionRate,
      sampleSize: 1,
    },
    toolErrors: {
      before: state.baseline_execution?.error ? 1 : 0,
      after: state.execution?.error ? 1 : 0,
    },
    reasons:
      verification?.reasons ??
      [state.stop_reason || "Waiting for the verification gate."],
  };
}

export function adaptLangGraphState(state: LangGraphState) {
  const trace = toTrace(state);
  const baseline = state.baseline_execution;
  const execution = state.execution;
  const before = (state.repair_patch?.before ?? state.skill_content).split(
    /\r?\n/,
  );
  const input: DemoCase = {
    id: state.run_id,
    name: `${state.executor} / ${state.status}`,
    summary: `Agent Run · ${state.scenario} · attempt ${state.attempt}/${state.max_attempts}`,
    task: state.task,
    expected: "通过验证门禁，并保留可审计的执行与断言证据。",
    actual:
      execution?.summary ??
      baseline?.summary ??
      "Run 已创建，正在等待执行结果。",
    signals: {
      applicableSkillKnown: true,
      correctSkillInCandidates: true,
      correctSkillSelected: true,
      skillLoaded: true,
      toolSchemaValid: !execution?.error,
      instructionFollowed: execution?.passed ?? false,
      skillCoversRequirement: execution?.passed ?? false,
      externalFailure: state.attribution?.cause === "platform" ? "service" : null,
    },
    skill: {
      id: state.skill_id,
      version: state.skill_version,
      retrievalScore: 1,
      loaded: true,
      before,
    },
    trace,
  };

  return {
    input,
    diagnosis: toDiagnosis(state, trace),
    repair: toRepair(state),
    validation: toValidation(state),
    usage: summarizeTokenUsage(trace),
  };
}

export function runEvidenceId(state: LangGraphState | null) {
  return state?.evidence_snapshot?.execution_sha256 ?? null;
}
