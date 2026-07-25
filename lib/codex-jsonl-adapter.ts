import type { DemoCase, TokenUsage, TraceStep } from "./demo-engine.ts";
import {
  parseTraceSession,
  TRACE_SCHEMA_VERSION,
  type RawTraceSession,
  type TraceObservations,
} from "./trace-adapter.ts";

type JsonObject = Record<string, unknown>;

export type CodexFaultSelector = {
  itemId?: string;
  eventType?: "error" | "turn.failed";
  title: string;
  detail: string;
  evidence: string;
};

export type CodexTraceContext = {
  session: RawTraceSession["session"];
  skill: DemoCase["skill"];
  observations: TraceObservations;
  fault: CodexFaultSelector;
  model?: string;
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringify(value: unknown, maximum = 2_000): string {
  const result =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!result) return "(no detail)";
  return result.length <= maximum
    ? result
    : `${result.slice(0, maximum)}…`;
}

function formatElapsed(milliseconds: number): string {
  const safe = Math.max(0, milliseconds);
  const minutes = Math.floor(safe / 60_000);
  const seconds = Math.floor((safe % 60_000) / 1_000);
  const tenths = Math.floor((safe % 1_000) / 100);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(
    2,
    "0",
  )}.${tenths}`;
}

function eventTimestamp(event: JsonObject): number | null {
  const value = event.timestamp ?? event.created_at;
  if (typeof value !== "string" && typeof value !== "number") return null;
  const parsed = typeof value === "number" ? value : Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function itemKind(type: unknown): TraceStep["kind"] {
  switch (type) {
    case "command_execution":
    case "file_change":
    case "mcp_tool_call":
    case "web_search":
      return "tool";
    case "agent_message":
    case "reasoning":
    case "plan_update":
      return "decision";
    default:
      return "decision";
  }
}

function itemTitle(item: JsonObject): string {
  switch (item.type) {
    case "command_execution":
      return `执行命令：${stringify(item.command, 160)}`;
    case "file_change":
      return "修改工作区文件";
    case "mcp_tool_call":
      return `调用 MCP：${String(item.server ?? "unknown")}/${String(
        item.tool ?? item.name ?? "unknown",
      )}`;
    case "web_search":
      return `搜索：${stringify(item.query, 160)}`;
    case "agent_message":
      return "Codex 回复";
    case "reasoning":
      return "Codex 推理";
    case "plan_update":
      return "更新执行计划";
    default:
      return `Codex item：${String(item.type ?? "unknown")}`;
  }
}

function itemDetail(item: JsonObject): string {
  switch (item.type) {
    case "command_execution":
      return stringify({
        command: item.command,
        status: item.status,
        exitCode: item.exit_code,
        output: item.aggregated_output ?? item.output,
      });
    case "file_change":
      return stringify(item.changes ?? item);
    case "mcp_tool_call":
      return stringify({
        arguments: item.arguments,
        result: item.result,
        error: item.error,
        status: item.status,
      });
    case "web_search":
      return stringify({
        query: item.query,
        result: item.result,
      });
    case "agent_message":
    case "reasoning":
      return stringify(item.text ?? item.summary ?? item);
    case "plan_update":
      return stringify(item.plan ?? item);
    default:
      return stringify(item);
  }
}

function readUsage(event: JsonObject): TokenUsage {
  const usage = isObject(event.usage) ? event.usage : {};
  const integer = (value: unknown) =>
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0
      ? value
      : 0;

  return {
    inputTokens: integer(usage.input_tokens),
    outputTokens: integer(usage.output_tokens),
    cachedInputTokens: integer(usage.cached_input_tokens),
    reasoningTokens: integer(usage.reasoning_output_tokens),
  };
}

function parseLines(jsonl: string): JsonObject[] {
  const events: JsonObject[] = [];
  const issues: string[] = [];

  for (const [index, line] of jsonl.split(/\r?\n/).entries()) {
    if (line.trim() === "") continue;
    try {
      const parsed: unknown = JSON.parse(line);
      if (!isObject(parsed)) {
        issues.push(`line ${index + 1} must contain a JSON object`);
      } else {
        events.push(parsed);
      }
    } catch (error) {
      issues.push(
        `line ${index + 1} is invalid JSON: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  }

  if (events.length === 0) issues.push("JSONL must contain at least one event");
  if (issues.length > 0) {
    throw new Error(`Invalid Codex JSONL:\n- ${issues.join("\n- ")}`);
  }
  return events;
}

function validateContext(context: CodexTraceContext) {
  const hasItem = typeof context.fault.itemId === "string";
  const hasEvent = typeof context.fault.eventType === "string";
  if (hasItem === hasEvent) {
    throw new Error(
      "Codex fault selector must provide exactly one of itemId or eventType.",
    );
  }
}

export function codexJsonlToTraceSession(
  jsonl: string,
  context: CodexTraceContext,
): RawTraceSession {
  validateContext(context);
  const events = parseLines(jsonl);
  const model = context.model ?? "codex";
  const firstTimestamp =
    events.map(eventTimestamp).find((value) => value !== null) ?? 0;
  const steps: TraceStep[] = [];
  let faultIndex = -1;
  let matchedFaults = 0;
  let completedTurn: JsonObject | null = null;

  for (const event of events) {
    const eventType = typeof event.type === "string" ? event.type : "unknown";
    if (eventType === "turn.completed") completedTurn = event;

    const item = isObject(event.item) ? event.item : null;
    const itemId =
      item && typeof item.id === "string" ? item.id : undefined;
    const matchesItem =
      context.fault.itemId !== undefined &&
      itemId === context.fault.itemId &&
      (eventType === "item.completed" || eventType === "item.failed");
    const matchesEvent = context.fault.eventType === eventType;
    const isFault = matchesItem || matchesEvent;

    if (isFault) {
      matchedFaults += 1;
      faultIndex = steps.length;
    }

    if (
      item &&
      (eventType === "item.completed" || eventType === "item.failed")
    ) {
      const timestamp = eventTimestamp(event);
      steps.push({
        id: itemId ?? `codex-item-${steps.length + 1}`,
        at: formatElapsed(
          timestamp === null ? 0 : timestamp - firstTimestamp,
        ),
        durationMs:
          typeof item.duration_ms === "number" &&
          Number.isFinite(item.duration_ms) &&
          item.duration_ms >= 0
            ? Math.round(item.duration_ms)
            : 0,
        kind: itemKind(item.type),
        title: isFault ? context.fault.title : itemTitle(item),
        detail: isFault ? context.fault.detail : itemDetail(item),
        status: isFault ? "fault" : faultIndex >= 0 ? "downstream" : "ok",
        model,
        usage: {
          inputTokens: 0,
          outputTokens: 0,
          cachedInputTokens: 0,
          reasoningTokens: 0,
        },
        evidence: isFault
          ? context.fault.evidence
          : `codex:${eventType}:${itemId ?? steps.length + 1}`,
      });
      continue;
    }

    if (isFault) {
      steps.push({
        id: `codex-${eventType.replace(".", "-")}-${steps.length + 1}`,
        at: "00:00.0",
        durationMs: 0,
        kind: "evaluation",
        title: context.fault.title,
        detail: context.fault.detail,
        status: "fault",
        model,
        usage: {
          inputTokens: 0,
          outputTokens: 0,
          cachedInputTokens: 0,
          reasoningTokens: 0,
        },
        evidence: context.fault.evidence,
      });
    }
  }

  if (matchedFaults !== 1) {
    throw new Error(
      `Codex fault selector must match exactly one completed event; matched ${matchedFaults}.`,
    );
  }
  if (!completedTurn) {
    throw new Error(
      "Codex JSONL must contain turn.completed so token usage is auditable.",
    );
  }

  const usage = readUsage(completedTurn);
  steps.push({
    id: "codex-turn-summary",
    at: "00:00.0",
    durationMs: 0,
    kind: "evaluation",
    title: "Codex turn 用量汇总",
    detail:
      "Codex CLI 只在 turn.completed 提供整轮 usage；为避免伪造逐步成本，item step 保持 0，整轮 token 全部记在此汇总节点。",
    status: "downstream",
    model,
    usage,
    evidence: "codex:turn.completed:usage",
  });

  return {
    schemaVersion: TRACE_SCHEMA_VERSION,
    session: structuredClone(context.session),
    skill: structuredClone(context.skill),
    observations: structuredClone(context.observations),
    events: steps,
  };
}

export function parseCodexJsonl(
  jsonl: string,
  context: CodexTraceContext,
): DemoCase {
  return parseTraceSession(codexJsonlToTraceSession(jsonl, context));
}
