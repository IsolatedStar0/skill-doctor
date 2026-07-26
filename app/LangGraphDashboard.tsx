"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  streamLangGraphRun,
  type LangGraphState,
} from "../lib/langgraph-stream";

type RunMode = "fixture" | "replay" | "codex";
type UiStatus = "idle" | "running" | "passed" | "failed" | "error";

const stageCopy: Record<string, string> = {
  prepare: "准备状态",
  execute: "执行 Agent",
  collect_evidence: "冻结证据",
  attribute: "失败归因",
  repair: "生成修复",
  verify: "回归验证",
  promote: "升级 Skill",
  finalize: "安全结束",
  "codex.thread": "Codex Thread",
  "codex.turn": "Codex Turn",
  "codex.reasoning": "推理过程",
  "codex.command_execution": "命令执行",
  "codex.file_change": "文件变更",
  "codex.mcp_tool_call": "MCP 调用",
  "codex.web_search": "联网检索",
  "codex.agent_message": "Agent 输出",
  "codex.todo_list": "任务列表",
  "codex.transport": "连接状态",
  "codex.error": "执行错误",
};

function percent(value: number | undefined) {
  return value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

function tokens(state: LangGraphState | null) {
  if (!state) return 0;
  return state.events.reduce(
    (total, event) =>
      total +
      (event.usage
        ? event.usage.input_tokens + event.usage.output_tokens
        : 0),
    0,
  );
}

export default function LangGraphDashboard() {
  const [mode, setMode] = useState<RunMode>("fixture");
  const [uiStatus, setUiStatus] = useState<UiStatus>("idle");
  const [snapshot, setSnapshot] = useState<LangGraphState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const apiUrl =
    process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL ??
    "http://localhost:8010";

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const totalTokens = useMemo(() => tokens(snapshot), [snapshot]);
  const graphEventCount =
    snapshot?.events.filter((event) => !event.stage.startsWith("codex."))
      .length ?? 0;
  const codexEventCount =
    snapshot?.events.filter((event) => event.stage.startsWith("codex."))
      .length ?? 0;
  const terminal =
    snapshot?.status === "passed" || snapshot?.status === "failed";
  const progress = terminal
    ? 100
    : Math.min(96, Math.round((graphEventCount / 9) * 100));

  const run = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setSnapshot(null);
    setError(null);
    setUiStatus("running");
    try {
      await streamLangGraphRun(
        {
          executor: mode,
          scenario: "content-gap",
          skill_id:
            mode === "fixture" ? "spreadsheet-summary" : "tdd-workflow",
          stream_delay_ms: mode === "codex" ? 0 : 220,
          codex_timeout_ms: 180_000,
        },
        (state) => {
          setSnapshot(state);
          if (state.status === "passed") setUiStatus("passed");
          if (state.status === "failed") setUiStatus("failed");
        },
        { signal: controller.signal, apiBaseUrl: apiUrl },
      );
      setUiStatus((current) => (current === "running" ? "failed" : current));
    } catch (caught) {
      if (controller.signal.aborted) {
        setUiStatus("idle");
        return;
      }
      setUiStatus("error");
      setError(
        caught instanceof Error
          ? caught.message
          : "无法读取 LangGraph 事件流。",
      );
    }
  };

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  return (
    <section className="langgraph-view">
      <div className="section-intro langgraph-intro">
        <div>
          <span className="kicker">LIVE STATE GRAPH / NDJSON STREAM</span>
          <h2>
            从第一次失败到修复升级，
            <em>逐节点观察 Agent 自愈。</em>
          </h2>
        </div>
        <div className={`graph-status ${uiStatus}`}>
          <i />
          <span>{uiStatus.toUpperCase()}</span>
          <small>{snapshot?.run_id ?? "等待启动"}</small>
        </div>
      </div>

      <div className="graph-control panel">
        <div className="graph-mode" aria-label="LangGraph executor">
          <button
            type="button"
            className={mode === "fixture" ? "active" : ""}
            onClick={() => setMode("fixture")}
            disabled={uiStatus === "running"}
          >
            <span>OFFLINE</span>
            确定性自修复
          </button>
          <button
            type="button"
            className={mode === "replay" ? "active" : ""}
            onClick={() => setMode("replay")}
            disabled={uiStatus === "running"}
          >
            <span>CODEX EVIDENCE</span>
            真实配对重放
          </button>
          <button
            type="button"
            className={mode === "codex" ? "active" : ""}
            onClick={() => setMode("codex")}
            disabled={uiStatus === "running"}
          >
            <span>CODEX SDK LIVE</span>
            真实 Agent 执行
          </button>
        </div>
        <div className="graph-api">
          <span>STREAM ENDPOINT</span>
          <code>{apiUrl}/runs/stream</code>
          {snapshot?.observability?.trace_url ? (
            <a
              href={snapshot.observability.trace_url}
              target="_blank"
              rel="noreferrer"
            >
              OPEN IN LANGSMITH ↗
            </a>
          ) : (
            <small>
              LANGSMITH {snapshot?.observability?.status ?? "OPTIONAL"}
            </small>
          )}
        </div>
        {uiStatus === "running" ? (
          <button className="graph-run secondary" type="button" onClick={cancel}>
            停止运行
          </button>
        ) : (
          <button className="graph-run" type="button" onClick={run}>
            启动 LangGraph <b>→</b>
          </button>
        )}
      </div>

      {error && (
        <div className="graph-error" role="alert">
          <strong>无法连接 Python control plane</strong>
          <span>{error}</span>
          <code>npm run agent:api</code>
        </div>
      )}

      <div className="graph-progress" aria-label={`运行进度 ${progress}%`}>
        <i style={{ width: `${progress}%` }} />
      </div>

      <div className="graph-kpis">
        <article className="panel">
          <span>ATTEMPT</span>
          <strong>{snapshot?.attempt ?? 0}</strong>
          <small>最多 {snapshot?.max_attempts ?? 2} 次修复</small>
        </article>
        <article className="panel">
          <span>LIVE EVENTS</span>
          <strong>{codexEventCount}</strong>
          <small>{graphEventCount} LangGraph nodes</small>
        </article>
        <article className="panel">
          <span>TOTAL TOKENS</span>
          <strong>{totalTokens.toLocaleString("zh-CN")}</strong>
          <small>按执行节点累计</small>
        </article>
        <article className="panel">
          <span>PASS RATE Δ</span>
          <strong>{percent(snapshot?.verification?.pass_rate_delta)}</strong>
          <small>
            regression {percent(snapshot?.verification?.regression_rate)}
          </small>
        </article>
      </div>

      <article className="graph-timeline panel">
        <div className="panel-heading">
          <span>LANGGRAPH EVENT TIMELINE</span>
          <strong>{snapshot?.status ?? "NOT STARTED"}</strong>
        </div>
        {snapshot?.events.length ? (
          <div className="graph-event-list">
            {snapshot.events.map((event) => (
              <div
                className={`graph-event event-${event.status}${
                  event.stage.startsWith("codex.") ? " codex-event" : ""
                }`}
                key={`${event.sequence}-${event.stage}`}
              >
                <span className="event-index">
                  {String(event.sequence).padStart(2, "0")}
                </span>
                <i />
                <div>
                  <small>
                    ATTEMPT {event.attempt} / {event.status}
                  </small>
                  <strong>{stageCopy[event.stage] ?? event.stage}</strong>
                  <p>{event.message}</p>
                </div>
                <dl>
                  <div>
                    <dt>stage</dt>
                    <dd>{event.stage}</dd>
                  </div>
                  <div>
                    <dt>source</dt>
                    <dd>
                      {event.stage.startsWith("codex.") ? "SDK" : "GRAPH"}
                    </dd>
                  </div>
                  <div>
                    <dt>tokens</dt>
                    <dd>
                      {event.usage
                        ? (
                            event.usage.input_tokens +
                            event.usage.output_tokens
                          ).toLocaleString("zh-CN")
                        : "—"}
                    </dd>
                  </div>
                </dl>
              </div>
            ))}
          </div>
        ) : (
          <div className="graph-empty">
            <span>01</span>
            <p>启动运行后，LangGraph 的每个状态转换会在这里实时出现。</p>
          </div>
        )}
      </article>

      <div className="graph-detail-grid">
        <article className="panel">
          <div className="panel-heading">
            <span>ATTRIBUTION</span>
            <strong>{snapshot?.attribution?.cause ?? "PENDING"}</strong>
          </div>
          <h3>{snapshot?.attribution?.taxonomy ?? "等待失败证据"}</h3>
          <p>
            {snapshot?.attribution?.explanation ??
              "归因节点会区分 Skill、loader、tool 与 platform 责任。"}
          </p>
          <footer>
            confidence {percent(snapshot?.attribution?.confidence)}
          </footer>
        </article>

        <article className="panel graph-patch-card">
          <div className="panel-heading">
            <span>REPAIR PATCH</span>
            <strong>{snapshot?.repair_patch?.kind ?? "PENDING"}</strong>
          </div>
          {snapshot?.repair_patch ? (
            <>
              <p className="graph-diff removed">
                − {snapshot.repair_patch.before}
              </p>
              <p className="graph-diff added">
                + {snapshot.repair_patch.after}
              </p>
              <footer>
                rollback {snapshot.repair_patch.rollback_ref}
              </footer>
            </>
          ) : (
            <p>只有高置信度 Skill/loader 责任才会开放写入通道。</p>
          )}
        </article>

        <article className="panel graph-gate-card">
          <div className="panel-heading">
            <span>VERIFICATION GATE</span>
            <strong>{snapshot?.verification?.decision ?? "PENDING"}</strong>
          </div>
          <div className="graph-pass-pair">
            <div>
              <span>BEFORE</span>
              <strong>
                {percent(snapshot?.verification?.baseline_pass_rate)}
              </strong>
            </div>
            <b>→</b>
            <div>
              <span>AFTER</span>
              <strong>
                {percent(snapshot?.verification?.candidate_pass_rate)}
              </strong>
            </div>
          </div>
          <footer>{snapshot?.stop_reason || "等待回归门禁"}</footer>
        </article>
      </div>
    </section>
  );
}
