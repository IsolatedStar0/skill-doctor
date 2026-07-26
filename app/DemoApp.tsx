"use client";

import { useEffect, useMemo, useState } from "react";
import {
  analyzeCase,
  demoCases,
  stepTokenTotal,
  summarizeTokenUsage,
  type DemoCase,
  type TokenUsageSummary,
  type TraceStep,
} from "../lib/demo-engine";
import {
  parseTraceSession,
  TraceValidationError,
} from "../lib/trace-adapter";
import type { PairedBenchmarkReport } from "../lib/benchmark-engine";
import benchmarkReportJson from "../public/benchmarks/latest.json";
import LangGraphDashboard from "./LangGraphDashboard";

type View =
  | "overview"
  | "trace"
  | "usage"
  | "diagnosis"
  | "patch"
  | "benchmark"
  | "orchestrator";

const views: { id: View; label: string; eyebrow: string }[] = [
  { id: "overview", label: "运行概览", eyebrow: "00" },
  { id: "trace", label: "Trace 过程", eyebrow: "01" },
  { id: "usage", label: "Token 面板", eyebrow: "02" },
  { id: "diagnosis", label: "故障归因", eyebrow: "03" },
  { id: "patch", label: "修复验证", eyebrow: "04" },
  { id: "benchmark", label: "配对评测", eyebrow: "05" },
  { id: "orchestrator", label: "LangGraph Loop", eyebrow: "06" },
];

const stageLabels = ["冻结证据", "定位故障", "规划修复", "回放验证"];
const benchmarkReport =
  benchmarkReportJson as unknown as PairedBenchmarkReport;

function Percent({ value }: { value: number }) {
  return <>{Math.round(value * 100)}%</>;
}

function formatTokens(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function tokenParts(step: TraceStep) {
  return [
    {
      key: "fresh",
      label: "新输入",
      value: step.usage.inputTokens - step.usage.cachedInputTokens,
    },
    {
      key: "cached",
      label: "缓存输入",
      value: step.usage.cachedInputTokens,
    },
    {
      key: "visible",
      label: "可见输出",
      value: step.usage.outputTokens - step.usage.reasoningTokens,
    },
    {
      key: "reasoning",
      label: "推理",
      value: step.usage.reasoningTokens,
    },
  ];
}

function TokenStack({
  step,
  maxTokens,
}: {
  step: TraceStep;
  maxTokens: number;
}) {
  return (
    <div
      className="token-stack"
      aria-label={`${step.id} 使用 ${formatTokens(stepTokenTotal(step))} tokens`}
    >
      {tokenParts(step).map((part) => (
        <i
          key={part.key}
          className={`token-${part.key}`}
          style={{ width: `${(part.value / maxTokens) * 100}%` }}
          aria-label={`${part.label} ${formatTokens(part.value)}`}
        />
      ))}
    </div>
  );
}

function TraceCard({
  step,
  index,
  maxTokens,
}: {
  step: TraceStep;
  index: number;
  maxTokens: number;
}) {
  return (
    <article className={`trace-card trace-${step.status}`}>
      <div className="trace-rail">
        <span>{String(index + 1).padStart(2, "0")}</span>
        <i />
      </div>
      <div className="trace-body">
        <div className="trace-meta">
          <span>{step.at}</span>
          <span>{step.kind}</span>
          <span>{step.durationMs} ms</span>
          <span>{step.model}</span>
          <span className={`status-dot ${step.status}`}>{step.status}</span>
        </div>
        <h3>{step.title}</h3>
        <p>{step.detail}</p>
        <div className="trace-token-row">
          <TokenStack step={step} maxTokens={maxTokens} />
          <strong>{formatTokens(stepTokenTotal(step))} tok</strong>
        </div>
        {step.evidence && <code>{step.evidence}</code>}
      </div>
    </article>
  );
}

function TraceProcessMap({ trace }: { trace: TraceStep[] }) {
  return (
    <article className="process-map panel">
      <div className="panel-heading">
        <span>EXECUTION FLOW</span>
        <strong>{trace.length} OBSERVED STEPS</strong>
      </div>
      <div
        className="process-flow"
        style={{ "--steps": trace.length } as React.CSSProperties}
        role="list"
        aria-label="Agent Trace 执行过程"
      >
        {trace.map((step, index) => (
          <div
            key={step.id}
            role="listitem"
            className={`process-node process-${step.status}`}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <b>{step.kind}</b>
            <strong>{step.title}</strong>
            <small>
              {step.durationMs} ms / {formatTokens(stepTokenTotal(step))} tok
            </small>
          </div>
        ))}
      </div>
    </article>
  );
}

function CumulativeTokenChart({
  trace,
  summary,
}: {
  trace: TraceStep[];
  summary: TokenUsageSummary;
}) {
  const width = 760;
  const height = 210;
  const left = 58;
  const right = 24;
  const top = 20;
  const bottom = 46;
  const cumulative = trace.map((_, index) =>
    trace
      .slice(0, index + 1)
      .reduce((total, step) => total + stepTokenTotal(step), 0),
  );
  const points = trace.map((step, index) => {
    const running = cumulative[index];
    const x =
      trace.length === 1
        ? left
        : left + (index * (width - left - right)) / (trace.length - 1);
    const y =
      height -
      bottom -
      (running / Math.max(summary.totalTokens, 1)) *
        (height - top - bottom);
    return { x, y, value: running, step };
  });
  const line = points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <svg
      className="burn-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-labelledby="burn-title burn-description"
    >
      <title id="burn-title">累计 Token 消耗曲线</title>
      <desc id="burn-description">
        本次运行累计消耗 {formatTokens(summary.totalTokens)} tokens，最高消耗步骤为{" "}
        {summary.hottestStepId}。
      </desc>
      {[0, 0.5, 1].map((ratio) => {
        const y = height - bottom - ratio * (height - top - bottom);
        return (
          <g key={ratio}>
            <line x1={left} x2={width - right} y1={y} y2={y} />
            <text x={left - 9} y={y + 4} textAnchor="end">
              {formatTokens(Math.round(summary.totalTokens * ratio))}
            </text>
          </g>
        );
      })}
      <polyline points={line} />
      {points.map((point) => (
        <g key={point.step.id}>
          <circle cx={point.x} cy={point.y} r="5" />
          <text
            className="burn-value"
            x={point.x}
            y={point.y - 12}
            textAnchor="middle"
          >
            {formatTokens(point.value)}
          </text>
          <text
            className="burn-step"
            x={point.x}
            y={height - 18}
            textAnchor="middle"
          >
            {point.step.id}
          </text>
        </g>
      ))}
    </svg>
  );
}

function TokenDashboard({ trace }: { trace: TraceStep[] }) {
  const summary = summarizeTokenUsage(trace);
  const maxTokens = Math.max(...trace.map(stepTokenTotal), 1);

  return (
    <section className="usage-view">
      <div className="section-intro">
        <div>
          <span className="kicker">TOKEN ACCOUNTING / PER TRACE STEP</span>
          <h2>
            每个推理和工具步骤，<em>都留下可核对的消耗。</em>
          </h2>
        </div>
        <div className="usage-total">
          <strong>{formatTokens(summary.totalTokens)}</strong>
          <span>total tokens</span>
        </div>
      </div>

      <div className="usage-kpis">
        <article className="panel">
          <span>INPUT</span>
          <strong>{formatTokens(summary.inputTokens)}</strong>
          <small>{formatTokens(summary.freshInputTokens)} fresh</small>
        </article>
        <article className="panel">
          <span>OUTPUT</span>
          <strong>{formatTokens(summary.outputTokens)}</strong>
          <small>{formatTokens(summary.reasoningTokens)} reasoning</small>
        </article>
        <article className="panel">
          <span>CACHE HIT</span>
          <strong>{Math.round(summary.cacheHitRate * 100)}%</strong>
          <small>{formatTokens(summary.cachedInputTokens)} cached</small>
        </article>
        <article className="panel">
          <span>HOT STEP</span>
          <strong>{summary.hottestStepId}</strong>
          <small>{formatTokens(summary.hottestStepTokens)} tokens</small>
        </article>
      </div>

      <div className="usage-grid">
        <article className="token-breakdown panel">
          <div className="panel-heading">
            <span>STEP BREAKDOWN</span>
            <div className="token-legend" aria-label="Token 类型图例">
              <span className="legend-fresh">新输入</span>
              <span className="legend-cached">缓存输入</span>
              <span className="legend-visible">可见输出</span>
              <span className="legend-reasoning">推理</span>
            </div>
          </div>
          <div className="token-rows">
            {trace.map((step) => (
              <div className="token-row" key={step.id}>
                <div>
                  <strong>{step.id}</strong>
                  <span>{step.kind}</span>
                </div>
                <TokenStack step={step} maxTokens={maxTokens} />
                <b>{formatTokens(stepTokenTotal(step))}</b>
              </div>
            ))}
          </div>
        </article>

        <article className="burn-panel panel">
          <div className="panel-heading">
            <span>CUMULATIVE BURN</span>
            <strong>{summary.durationMs} MS</strong>
          </div>
          <CumulativeTokenChart trace={trace} summary={summary} />
        </article>
      </div>
    </section>
  );
}

function signedPercent(value: number | null, digits = 1) {
  if (value === null) return "N/A";
  const percent = value * 100;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(digits)}%`;
}

function plainPercent(value: number, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

function signedDuration(value: number | null) {
  if (value === null) return "N/A";
  const seconds = value / 1000;
  return `${seconds > 0 ? "+" : ""}${seconds.toFixed(1)}s`;
}

function BenchmarkDashboard({
  report,
}: {
  report: PairedBenchmarkReport;
}) {
  return (
    <section className="benchmark-view">
      <div className="section-intro benchmark-intro">
        <div>
          <span className="kicker">LIVE CODEX SDK / PAIRED KNOWLEDGE PROBE</span>
          <h2>
            同一任务跑两次，量化 Skill 的
            <em>真实收益与成本。</em>
          </h2>
        </div>
        <div className="benchmark-run-meta">
          <span>RUN ID</span>
          <code>{report.runId}</code>
          <small>{new Date(report.generatedAt).toLocaleString("zh-CN")}</small>
        </div>
      </div>

      <div className="benchmark-notice">
        <strong>范围说明</strong>
        <span>
          当前成绩来自真实 Codex SDK 的只读知识/计划 probe，并由 pytest
          验证关键约束；它不等同于固定仓库中的代码修复通过率。
        </span>
      </div>

      <div className="benchmark-kpis">
        <article className="benchmark-kpi panel">
          <span>AVG PASS-RATE DELTA</span>
          <strong>
            {signedPercent(report.summary.averagePassRateDelta)}
          </strong>
          <small>{report.summary.improved}/3 skills improved</small>
        </article>
        <article className="benchmark-kpi panel">
          <span>AVG TOKEN OVERHEAD</span>
          <strong>
            {signedPercent(report.summary.averageTokenOverheadRate)}
          </strong>
          <small>quality gain is not free</small>
        </article>
        <article className="benchmark-kpi panel">
          <span>AVG LATENCY DELTA</span>
          <strong>
            {signedDuration(report.summary.averageDurationDeltaMs)}
          </strong>
          <small>treatment minus control</small>
        </article>
        <article className="benchmark-kpi panel">
          <span>REGRESSION RATE</span>
          <strong>{signedPercent(report.summary.regressionRate)}</strong>
          <small>control checks lost after Skill</small>
        </article>
      </div>

      <article className="benchmark-table-panel panel">
        <div className="panel-heading">
          <span>WITH-SKILL / WITHOUT-SKILL</span>
          <strong>{report.summary.completedPairs} COMPLETE PAIRS</strong>
        </div>
        <div className="benchmark-table-wrap">
          <table className="benchmark-table">
            <thead>
              <tr>
                <th>Skill</th>
                <th>Control</th>
                <th>With Skill</th>
                <th>Δ Pass rate</th>
                <th>Token overhead</th>
                <th>Δ Duration</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {report.pairs.map((pair) => (
                <tr key={pair.skillId}>
                  <td>
                    <strong>{pair.name}</strong>
                    <code>{pair.skillId}</code>
                  </td>
                  <td>
                    <b>{plainPercent(pair.control.verifier.passRate)}</b>
                    <small>
                      {formatTokens(pair.control.usage?.totalTokens ?? 0)} tok
                    </small>
                  </td>
                  <td>
                    <b>{plainPercent(pair.treatment.verifier.passRate)}</b>
                    <small>
                      {formatTokens(pair.treatment.usage?.totalTokens ?? 0)} tok
                    </small>
                  </td>
                  <td className="delta-positive">
                    {signedPercent(pair.comparison.passRateDelta, 0)}
                  </td>
                  <td>
                    {signedPercent(pair.comparison.tokenOverheadRate)}
                  </td>
                  <td>{signedDuration(pair.comparison.durationDeltaMs)}</td>
                  <td>
                    <span
                      className={`benchmark-outcome ${pair.comparison.outcome}`}
                    >
                      {pair.comparison.outcome}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <div className="benchmark-pairs">
        {report.pairs.map((pair) => {
          const gained = pair.treatment.verifier.assertions.filter(
            (assertion) =>
              assertion.passed &&
              pair.control.verifier.assertions.some(
                (control) =>
                  control.id === assertion.id && !control.passed,
              ),
          );
          return (
            <article className="benchmark-pair panel" key={pair.skillId}>
              <div className="panel-heading">
                <span>{pair.dimension}</span>
                <strong>{pair.comparison.outcome.toUpperCase()}</strong>
              </div>
              <h3>{pair.name}</h3>
              <div className="paired-bars">
                <div>
                  <span>WITHOUT</span>
                  <p>
                    <i
                      style={{
                        width: `${pair.control.verifier.passRate * 100}%`,
                      }}
                    />
                  </p>
                  <b>
                    {plainPercent(pair.control.verifier.passRate)}
                  </b>
                </div>
                <div className="with-skill">
                  <span>WITH SKILL</span>
                  <p>
                    <i
                      style={{
                        width: `${pair.treatment.verifier.passRate * 100}%`,
                      }}
                    />
                  </p>
                  <b>
                    {plainPercent(pair.treatment.verifier.passRate)}
                  </b>
                </div>
              </div>
              <div className="gained-checks">
                <span>GAINED CHECKS</span>
                {gained.length === 0 ? (
                  <p>没有新增通过项；Skill 提供了更多上下文，但未提高通过率。</p>
                ) : (
                  <ul>
                    {gained.map((assertion) => (
                      <li key={assertion.id}>
                        <b>+</b>
                        {assertion.label}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <footer>
                <span>
                  Δ tokens{" "}
                  {formatTokens(pair.comparison.tokenDelta ?? 0)}
                </span>
                <span>
                  regression{" "}
                  {signedPercent(pair.comparison.regressionRate, 0)}
                </span>
              </footer>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export default function DemoApp() {
  const [view, setView] = useState<View>("overview");
  const [selectedCaseId, setSelectedCaseId] = useState(demoCases[0].id);
  const [importedCases, setImportedCases] = useState<DemoCase[]>([]);
  const [importState, setImportState] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);
  const [runKey, setRunKey] = useState(0);
  const [stage, setStage] = useState(4);

  const availableCases = useMemo(
    () => [...demoCases, ...importedCases],
    [importedCases],
  );
  const activeCase =
    availableCases.find((item) => item.id === selectedCaseId) ?? demoCases[0];
  const result = useMemo(() => analyzeCase(activeCase), [activeCase]);
  const isSkillPatch = result.repair.kind === "skill_patch";
  const maxStepTokens = Math.max(
    ...activeCase.trace.map(stepTokenTotal),
    1,
  );

  useEffect(() => {
    if (runKey === 0) return;
    const timer = window.setInterval(() => {
      setStage((current) => {
        if (current >= 4) {
          window.clearInterval(timer);
          return 4;
        }
        return current + 1;
      });
    }, 620);
    return () => window.clearInterval(timer);
  }, [runKey]);

  const rerun = () => {
    setView("overview");
    setStage(0);
    setRunKey((value) => value + 1);
  };

  const selectCase = (caseId: string) => {
    setSelectedCaseId(caseId);
    setView("overview");
    setStage(0);
    setRunKey((value) => value + 1);
  };

  const importTrace = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    try {
      if (file.size > 1024 * 1024) {
        throw new Error("Trace 文件不能超过 1 MB。");
      }
      const payload = JSON.parse(await file.text());
      const imported = parseTraceSession(payload);
      setImportedCases((current) => [
        ...current.filter((item) => item.id !== imported.id),
        imported,
      ]);
      setSelectedCaseId(imported.id);
      setView("trace");
      setStage(0);
      setRunKey((value) => value + 1);
      setImportState({
        kind: "success",
        message: `已导入 ${file.name}：${imported.trace.length} steps，${formatTokens(
          summarizeTokenUsage(imported.trace).totalTokens,
        )} tokens。`,
      });
    } catch (error) {
      const message =
        error instanceof TraceValidationError
          ? error.issues[0]
          : error instanceof Error
            ? error.message
            : "无法读取 Trace 文件。";
      setImportState({
        kind: "error",
        message: `导入失败：${message}`,
      });
    }
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand-mark">SD</div>
          <p className="brand-name">Skill Doctor</p>
          <p className="brand-caption">Trace-driven skill evolution</p>
        </div>

        <nav aria-label="Demo sections">
          {views.map((item) => (
            <button
              type="button"
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
              data-testid={`nav-${item.id}`}
            >
              <span>{item.eyebrow}</span>
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className="live-dot" />
          <span>DETERMINISTIC DEMO</span>
          <small>{activeCase.id}</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="kicker">SKILL EVOLUTION LAB / LOCAL RUN</span>
            <h1>
              让每一次失败
              <br />
              <em>留下可验证的修复。</em>
            </h1>
          </div>
          <div className="topbar-actions">
            <label className="import-button">
              <span>导入 Trace JSON</span>
              <input
                type="file"
                accept="application/json,.json"
                onChange={importTrace}
                data-testid="trace-import"
              />
            </label>
            <button className="run-button" type="button" onClick={rerun}>
              <span>重新运行闭环</span>
              <b>↗</b>
            </button>
          </div>
        </header>

        {importState && (
          <div
            className={`import-status ${importState.kind}`}
            role={importState.kind === "error" ? "alert" : "status"}
          >
            <span>{importState.kind === "success" ? "✓" : "!"}</span>
            {importState.message}
          </div>
        )}

        {view !== "benchmark" && view !== "orchestrator" && (
          <>
            <section
              className="scenario-switcher"
              aria-label="Failure scenarios"
            >
              {availableCases.map((item, index) => (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === activeCase.id ? "active" : ""}
                  onClick={() => selectCase(item.id)}
                  data-testid={`case-${item.id}`}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{item.name}</strong>
                  <small>{item.summary}</small>
                </button>
              ))}
            </section>

            <div className="stage-strip" aria-label="Pipeline progress">
              {stageLabels.map((label, index) => (
                <div
                  key={label}
                  className={
                    stage > index
                      ? "complete"
                      : stage === index
                        ? "running"
                        : ""
                  }
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{label}</strong>
                  <i />
                </div>
              ))}
            </div>
          </>
        )}

        {view === "overview" && (
          <section className="view-grid">
            <article className="case-panel panel">
              <div className="panel-heading">
                <span>FAILED SESSION / {activeCase.name}</span>
                <strong>可复现</strong>
              </div>
              <h2>{activeCase.task}</h2>
              <div className="outcome-grid">
                <div>
                  <span>EXPECTED</span>
                  <strong>{activeCase.expected}</strong>
                </div>
                <div className="bad-value">
                  <span>ACTUAL</span>
                  <strong>{activeCase.actual}</strong>
                </div>
              </div>
              <dl className="case-facts">
                <div>
                  <dt>Trace</dt>
                  <dd>
                    {activeCase.trace.length} steps /{" "}
                    {formatTokens(result.usage.totalTokens)} tok
                  </dd>
                </div>
                <div>
                  <dt>Skill</dt>
                  <dd>
                    {activeCase.skill.id}@{activeCase.skill.version}
                  </dd>
                </div>
                <div>
                  <dt>Runtime</dt>
                  <dd>local deterministic harness</dd>
                </div>
              </dl>
            </article>

            <article className="decision-panel panel">
              <div className="panel-heading">
                <span>REPAIR DECISION</span>
                <strong className="adopt-chip">
                  {result.validation.decision}
                </strong>
              </div>
              <div className="score-ring">
                <div>
                  {isSkillPatch ? (
                    <>
                      <strong>0</strong>
                      <span>→</span>
                      <strong>1</strong>
                    </>
                  ) : (
                    <>
                      <strong>NO</strong>
                      <span>→</span>
                      <strong>DIFF</strong>
                    </>
                  )}
                </div>
                <p>{isSkillPatch ? "original replay" : "safe mutation policy"}</p>
              </div>
              <ul className="reason-list">
                {result.validation.reasons.map((reason) => (
                  <li key={reason}>
                    <span>✓</span>
                    {reason}
                  </li>
                ))}
              </ul>
            </article>

            <article className="metric-card panel">
              <span>ATTRIBUTION</span>
              <strong>
                <Percent value={result.diagnosis.confidence} />
              </strong>
              <p>归因置信度</p>
              <div
                style={
                  {
                    "--meter": `${result.diagnosis.confidence * 100}%`,
                  } as React.CSSProperties
                }
              >
                <i />
              </div>
            </article>
            <article className="metric-card panel">
              <span>SKILL RESPONSIBILITY</span>
              <strong>
                <Percent value={result.diagnosis.responsibility} />
              </strong>
              <p>目标 Skill 责任权重</p>
              <div
                style={
                  {
                    "--meter": `${result.diagnosis.responsibility * 100}%`,
                  } as React.CSSProperties
                }
              >
                <i />
              </div>
            </article>
            <article className="metric-card panel">
              <span>MUTATION SAFETY</span>
              <strong>{isSkillPatch ? "1" : "0"}</strong>
              <p>{isSkillPatch ? "条最小化指令变更" : "条 Skill 内容变更"}</p>
              <div style={{ "--meter": "100%" } as React.CSSProperties}>
                <i />
              </div>
            </article>
          </section>
        )}

        {view === "trace" && (
          <section className="trace-view">
            <div className="section-intro">
              <div>
                <span className="kicker">EVIDENCE SNAPSHOT</span>
                <h2>从 Skill 路由到结果失败，证据链保持完整。</h2>
              </div>
              <dl>
                <div>
                  <dt>Snapshot</dt>
                  <dd>sha256:8a7f…42c1</dd>
                </div>
                <div>
                  <dt>Load state</dt>
                  <dd>
                    {activeCase.skill.loaded
                      ? `${activeCase.skill.id}@${activeCase.skill.version}`
                      : "INCOMPLETE / RESOURCE MISSING"}
                  </dd>
                </div>
                <div>
                  <dt>Token usage</dt>
                  <dd>{formatTokens(result.usage.totalTokens)} total</dd>
                </div>
              </dl>
            </div>
            <TraceProcessMap trace={activeCase.trace} />
            <div className="timeline">
              {activeCase.trace.map((step, index) => (
                <TraceCard
                  key={step.id}
                  step={step}
                  index={index}
                  maxTokens={maxStepTokens}
                />
              ))}
            </div>
          </section>
        )}

        {view === "usage" && <TokenDashboard trace={activeCase.trace} />}

        {view === "benchmark" && (
          <BenchmarkDashboard report={benchmarkReport} />
        )}

        {view === "orchestrator" && <LangGraphDashboard />}

        {view === "diagnosis" && (
          <section className="diagnosis-view">
            <div className="section-intro">
              <div>
                <span className="kicker">STEP-LEVEL ATTRIBUTION</span>
                <h2>
                  最早可行动故障发生在{" "}
                  <em>{result.diagnosis.primaryFaultStep}</em>。
                </h2>
              </div>
              <div className="confidence-block">
                <strong>
                  <Percent value={result.diagnosis.confidence} />
                </strong>
                <span>confidence</span>
              </div>
            </div>

            <div className="diagnosis-grid">
              <article className="taxonomy-card panel">
                <span>7-CLASS TAXONOMY</span>
                <div className="taxonomy-list">
                  {[
                    "Skill Recall Failure",
                    "Selection Error",
                    "Loading Miss",
                    "Instruction Violation",
                    "Tool Misuse",
                    "Content Gap",
                    "Non-Skill Cause",
                  ].map((item) => (
                    <div
                      key={item}
                      className={
                        item === result.diagnosis.taxonomy ? "selected" : ""
                      }
                    >
                      <i />
                      <span>{item}</span>
                      {item === result.diagnosis.taxonomy && (
                        <b>{result.diagnosis.confidence.toFixed(2)}</b>
                      )}
                    </div>
                  ))}
                </div>
              </article>

              <article className="mechanism-card panel">
                <div className="panel-heading">
                  <span>CAUSAL MECHANISM</span>
                  <strong>
                    {result.diagnosis.ruleId} / {result.diagnosis.action}
                  </strong>
                </div>
                <blockquote>{result.diagnosis.mechanism}</blockquote>
                <div className="fault-chain">
                  {result.diagnosis.faultChain.map((item, index) => (
                    <span key={item}>
                      {item}
                      {index < result.diagnosis.faultChain.length - 1 && (
                        <b>→</b>
                      )}
                    </span>
                  ))}
                </div>
                <h3>Evidence refs</h3>
                <div className="evidence-list">
                  {result.diagnosis.evidenceRefs.map((ref) => (
                    <code key={ref}>{ref}</code>
                  ))}
                </div>
                <h3 className="rule-heading">
                  Rule proof / {result.diagnosis.ruleVersion}
                </h3>
                <div className="rule-proof">
                  {result.diagnosis.ruleEvaluations.map((rule) => (
                    <div
                      key={rule.ruleId}
                      className={rule.selected ? "selected" : ""}
                    >
                      <code>{rule.ruleId}</code>
                      <span>{rule.taxonomy}</span>
                      <b>{rule.selected ? "SELECTED" : "EXCLUDED"}</b>
                      <small>{rule.reason}</small>
                    </div>
                  ))}
                </div>
              </article>
            </div>
          </section>
        )}

        {view === "patch" && (
          <section className="patch-view">
            {result.repair.kind === "skill_patch" ? (
              <>
                <div className="section-intro">
                  <div>
                    <span className="kicker">
                      SCOPED REPAIR + QUALIFICATION
                    </span>
                    <h2>
                      只改动一条指令，然后用失败案例和回归集证明它。
                    </h2>
                  </div>
                  <div className="version-badge">
                    <span>{result.repair.baseVersion}</span>
                    <b>→</b>
                    <strong>{result.repair.nextVersion}</strong>
                  </div>
                </div>

                <div className="patch-grid">
                  <article className="diff-panel panel">
                    <div className="panel-heading">
                      <span>SKILL.MD / PROCEDURE</span>
                      <strong>1 LINE CHANGED</strong>
                    </div>
                    <div className="diff-lines">
                      {result.repair.before.map((line, index) => {
                        const changed =
                          index + 1 === result.repair.changedLine;
                        return changed ? (
                          <div className="diff-change" key={line}>
                            <p className="removed">
                              <span>-</span>
                              {line}
                            </p>
                            <p className="added">
                              <span>+</span>
                              {result.repair.after[index]}
                            </p>
                          </div>
                        ) : (
                          <p key={line}>
                            <span>{index + 1}</span>
                            {line}
                          </p>
                        );
                      })}
                    </div>
                    <footer>
                      <span>scope: {result.repair.scope}</span>
                      <span>rollback: {result.repair.rollbackRef}</span>
                    </footer>
                  </article>

                  <article className="validation-panel panel">
                    <div className="panel-heading">
                      <span>VALIDATION GATE</span>
                      <strong className="adopt-chip">
                        {result.validation.decision}
                      </strong>
                    </div>
                    <div className="validation-bars">
                      <div>
                        <span>Original replay</span>
                        <p>
                          <i
                            style={{
                              width: `${result.validation.originalReplay.after * 100}%`,
                            }}
                          />
                        </p>
                        <strong>0 → 1</strong>
                      </div>
                      <div>
                        <span>Similar cases</span>
                        <p>
                          <i
                            style={{
                              width: `${result.validation.similarCases.after * 100}%`,
                            }}
                          />
                        </p>
                        <strong>50 → 100%</strong>
                      </div>
                      <div>
                        <span>Regression</span>
                        <p>
                          <i
                            style={{
                              width: `${result.validation.regression.after * 100}%`,
                            }}
                          />
                        </p>
                        <strong>100 → 100%</strong>
                      </div>
                      <div>
                        <span>Tool errors</span>
                        <p>
                          <i style={{ width: "0%" }} />
                        </p>
                        <strong>1 → 0</strong>
                      </div>
                    </div>
                    <div className="gate-rule">
                      original_fixed <b>AND</b> cluster_improved <b>AND</b>{" "}
                      no_regression
                    </div>
                  </article>
                </div>
              </>
            ) : (
              <>
                <div className="section-intro">
                  <div>
                    <span className="kicker">
                      SAFE REFUSAL + FAULT ROUTING
                    </span>
                    <h2>
                      根因不在 Skill 内容，系统明确拒绝生成错误补丁。
                    </h2>
                  </div>
                  <div className="version-badge route-badge">
                    <span>SKILL</span>
                    <b>≠</b>
                    <strong>{result.repair.target.toUpperCase()}</strong>
                  </div>
                </div>

                <div className="route-grid">
                  <article className="route-panel panel">
                    <div className="panel-heading">
                      <span>{result.repair.actionId}</span>
                      <strong>{result.repair.mutationPolicy}</strong>
                    </div>
                    <p className="safe-refusal">NO SKILL PATCH</p>
                    <h3>{result.repair.title}</h3>
                    <p className="route-detail">{result.repair.detail}</p>
                    <ol className="route-operations">
                      {result.repair.operations.map((operation) => (
                        <li key={operation}>{operation}</li>
                      ))}
                    </ol>
                  </article>

                  <article className="validation-panel panel">
                    <div className="panel-heading">
                      <span>ISOLATION GATE</span>
                      <strong className="adopt-chip">
                        {result.validation.decision}
                      </strong>
                    </div>
                    <div className="isolation-score">
                      <span>SKILL MUTATIONS</span>
                      <strong>0</strong>
                      <p>
                        归因证据不足以支持内容修改时，写回通道保持关闭。
                      </p>
                    </div>
                    <ul className="route-reasons">
                      {result.validation.reasons.map((reason) => (
                        <li key={reason}>
                          <span>✓</span>
                          {reason}
                        </li>
                      ))}
                    </ul>
                    <div className="gate-rule">
                      cause_outside_skill <b>THEN</b> freeze_skill{" "}
                      <b>AND</b> route_owner
                    </div>
                  </article>
                </div>
              </>
            )}
          </section>
        )}
      </section>
    </main>
  );
}
