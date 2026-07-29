"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
import type { BenchmarkEvaluationSummary } from "../lib/benchmark-summary";
import {
  runDefaultDiagnostics,
  streamBenchmarkRun,
  type BenchmarkState,
  type DiagnosticSuiteReport,
  type LangGraphState,
  type RunSummary,
} from "../lib/langgraph-stream";
import {
  adaptLangGraphState,
  runEvidenceId,
} from "../lib/run-view-adapter";
import benchmarkReportJson from "../public/benchmarks/latest.json";
import evaluationSummaryJson from "../reports/evaluation-summary.json";
import LangGraphDashboard from "./LangGraphDashboard";
import { useRunStore } from "./RunStore";

type View =
  | "overview"
  | "cases"
  | "trace"
  | "usage"
  | "diagnosis"
  | "patch"
  | "benchmark"
  | "evaluation"
  | "architecture"
  | "diagnostics"
  | "orchestrator";

const views: { id: View; label: string; eyebrow: string }[] = [
  { id: "overview", label: "项目总览", eyebrow: "00" },
  { id: "cases", label: "案例库", eyebrow: "01" },
  { id: "trace", label: "单案例分析", eyebrow: "02" },
  { id: "evaluation", label: "评测结果", eyebrow: "03" },
  { id: "architecture", label: "系统架构", eyebrow: "04" },
  { id: "orchestrator", label: "实时链路", eyebrow: "05" },
];

const overviewActions: { id: View; label: string }[] = [
  { id: "cases", label: "浏览案例库" },
  { id: "trace", label: "进入单案例分析" },
  { id: "evaluation", label: "看量化结果" },
];

const detailViews: { id: View; label: string; detail: string }[] = [
  { id: "trace", label: "1. Trace 证据", detail: "先看失败发生在哪里" },
  { id: "diagnosis", label: "2. 故障归因", detail: "再看为什么是这个分类" },
  { id: "patch", label: "3. 修复验证", detail: "最后看是否应该采纳" },
];

const benchmarkReport =
  benchmarkReportJson as unknown as PairedBenchmarkReport;
const evaluationSummary =
  evaluationSummaryJson as unknown as BenchmarkEvaluationSummary;

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
        <span>执行流程</span>
        <strong>{trace.length} 个观测步骤</strong>
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
          <span>总 Tokens</span>
        </div>
      </div>

      <div className="usage-kpis">
        <article className="panel">
          <span>输入</span>
          <strong>{formatTokens(summary.inputTokens)}</strong>
          <small>{formatTokens(summary.freshInputTokens)} 新输入</small>
        </article>
        <article className="panel">
          <span>输出</span>
          <strong>{formatTokens(summary.outputTokens)}</strong>
          <small>{formatTokens(summary.reasoningTokens)} 推理</small>
        </article>
        <article className="panel">
          <span>缓存命中</span>
          <strong>{Math.round(summary.cacheHitRate * 100)}%</strong>
          <small>{formatTokens(summary.cachedInputTokens)} 缓存输入</small>
        </article>
        <article className="panel">
          <span>最高消耗步骤</span>
          <strong>{summary.hottestStepId}</strong>
          <small>{formatTokens(summary.hottestStepTokens)} Tokens</small>
        </article>
      </div>

      <div className="usage-grid">
        <article className="token-breakdown panel">
          <div className="panel-heading">
            <span>步骤消耗拆分</span>
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
            <span>累计消耗曲线</span>
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

function signedNumber(value: number | null) {
  if (value === null) return "N/A";
  return `${value > 0 ? "+" : ""}${Math.round(value).toLocaleString("zh-CN")}`;
}

function EvaluationDashboard({
  summary,
}: {
  summary: BenchmarkEvaluationSummary;
}) {
  return (
    <section className="evaluation-view">
      <div className="section-intro evaluation-intro">
        <div>
          <span className="kicker">Benchmark Summary / 可复现评测</span>
          <h2>
            用固定指标证明 Agent Skill 的
            <em>收益、成本与安全边界。</em>
          </h2>
        </div>
        <div className="benchmark-run-meta">
          <span>报告来源</span>
          <code>{summary.sourceCount} benchmark snapshots</code>
          <small>{new Date(summary.generatedAt).toLocaleString("zh-CN")}</small>
        </div>
      </div>

      <div className="evaluation-kpis">
        <article className="panel">
          <span>修复成功率</span>
          <strong>{plainPercent(summary.repairSuccessRate)}</strong>
          <small>{summary.improvedPairs}/{summary.completedPairs} completed pairs improved</small>
        </article>
        <article className="panel">
          <span>平均通过率提升</span>
          <strong>{signedPercent(summary.averagePassRateDelta)}</strong>
          <small>Control → Treatment 的平均 pass-rate delta</small>
        </article>
        <article className="panel">
          <span>回归检出门禁</span>
          <strong>{plainPercent(summary.regressionDetectionRate)}</strong>
          <small>{summary.regressedPairs} 个 regressed pair 进入门禁统计</small>
        </article>
        <article className="panel">
          <span>Token 成本</span>
          <strong>{signedNumber(summary.averageTokenDelta)}</strong>
          <small>平均 Token delta / overhead {signedPercent(summary.averageTokenOverheadRate)}</small>
        </article>
      </div>

      <div className="evaluation-grid">
        <article className="panel evaluation-breakdown">
          <div className="panel-heading">
            <span>场景分布</span>
            <strong>{summary.totalPairs} Pairs</strong>
          </div>
          {summary.scenarioBreakdown.map((item) => (
            <div key={item.scenario} className="evaluation-row">
              <div>
                <strong>{item.scenario}</strong>
                <small>{item.runs} runs · {item.completedPairs} completed</small>
              </div>
              <span>{signedPercent(item.averagePassRateDelta)}</span>
              <i style={{ width: `${Math.max(4, item.repairSuccessRate * 100)}%` }} />
            </div>
          ))}
        </article>

        <article className="panel evaluation-breakdown">
          <div className="panel-heading">
            <span>最近评测快照</span>
            <strong>Regression-ready</strong>
          </div>
          {summary.recentRuns.map((run) => (
            <div key={run.runId} className="evaluation-row compact">
              <div>
                <strong>{run.runId}</strong>
                <small>{run.scenario} · {run.status} · {run.outcome}</small>
              </div>
              <span>{signedPercent(run.passRateDelta)}</span>
              <code>{signedNumber(run.tokenDelta)} tok</code>
            </div>
          ))}
        </article>
      </div>
    </section>
  );
}

type CaseStudyResult = ReturnType<typeof analyzeCase>;

const caseStudyNarratives: Record<
  string,
  { title: string; value: string; action: string }
> = {
  "Content Gap": {
    title: "内容缺口：Skill 写得不够完整",
    value: "展示系统如何把失败归因到 Skill procedure，并生成最小 scoped patch。",
    action: "自动修复",
  },
  "Loading Miss": {
    title: "加载遗漏：Skill 对但上下文没加载全",
    value: "展示系统如何避免误改 Skill，把问题路由到 loader / manifest 层。",
    action: "安全路由",
  },
  "Non-Skill Cause": {
    title: "平台异常：不是 Skill 的锅",
    value: "展示系统如何识别权限/服务故障，拒绝生成无意义 Skill diff。",
    action: "拒绝改 Skill",
  },
};

function repairLabel(result: CaseStudyResult) {
  if (result.repair.kind === "skill_patch") {
    return `${result.repair.targetSkill}@${result.repair.nextVersion}`;
  }
  if (result.repair.kind === "routing_action") {
    return `${result.repair.target} route / ${result.repair.mutationPolicy}`;
  }
  return `${result.repair.target} review / ${result.repair.mutationPolicy}`;
}

function CaseStudyGallery({
  studies,
  selectedCaseId,
  onOpenCase,
}: {
  studies: CaseStudyResult[];
  selectedCaseId: string;
  onOpenCase: (caseId: string) => void;
}) {
  const adopted = studies.filter(
    (item) => item.validation.decision === "ADOPT",
  ).length;
  const safelyRouted = studies.filter(
    (item) => item.validation.decision === "ROUTE",
  ).length;
  const taxonomies = new Set(studies.map((item) => item.diagnosis.taxonomy));

  return (
    <section className="case-gallery-view">
      <div className="section-intro case-gallery-intro">
        <div>
          <span className="kicker">Case Study Gallery / 失败模式样本库</span>
          <h2>
            把分散的失败 Trace 沉淀成
            <em>可讲解、可对比、可复现</em> 的案例资产。
          </h2>
        </div>
        <div className="case-gallery-summary">
          <span>Gallery Scope</span>
          <strong>{studies.length} cases</strong>
          <small>
            {taxonomies.size} 类故障 · {adopted} 个自动采纳 · {safelyRouted} 个安全路由
          </small>
        </div>
      </div>

      <div className="case-gallery-grid">
        {studies.map((study, index) => {
          const isSelected = study.input.id === selectedCaseId;
          const narrative = caseStudyNarratives[study.diagnosis.taxonomy] ?? {
            title: study.input.name,
            value: study.input.summary,
            action: study.validation.decision,
          };
          const faultStep = study.input.trace.find(
            (step) => step.status === "fault",
          );
          const passDelta =
            study.validation.originalReplay.after -
            study.validation.originalReplay.before;

          return (
            <article
              className={`panel case-study-card ${isSelected ? "selected" : ""}`}
              key={study.input.id}
            >
              <div className="case-study-topline">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{study.diagnosis.taxonomy}</strong>
                <b>{narrative.action}</b>
              </div>

              <h3>{narrative.title}</h3>
              <p>{narrative.value}</p>

              <dl>
                <div>
                  <dt>Root Cause</dt>
                  <dd>{study.diagnosis.mechanism}</dd>
                </div>
                <div>
                  <dt>Fault Evidence</dt>
                  <dd>
                    <code>{faultStep?.evidence ?? study.diagnosis.primaryFaultStep}</code>
                  </dd>
                </div>
                <div>
                  <dt>Repair Plan</dt>
                  <dd>{repairLabel(study)}</dd>
                </div>
              </dl>

              <div className="case-study-metrics">
                <div>
                  <span>Replay Δ</span>
                  <strong>{signedPercent(passDelta)}</strong>
                </div>
                <div>
                  <span>Regression</span>
                  <strong>{plainPercent(study.validation.regression.after)}</strong>
                </div>
                <div>
                  <span>Tokens</span>
                  <strong>{formatTokens(study.usage.totalTokens)}</strong>
                </div>
              </div>

              <ul>
                {study.validation.reasons.slice(0, 3).map((reason) => (
                  <li key={reason}>
                    <span>✓</span>
                    {reason}
                  </li>
                ))}
              </ul>

              <button
                type="button"
                aria-pressed={isSelected}
                onClick={() => onOpenCase(study.input.id)}
              >
                选择并查看 Trace →
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}

const architectureStages = [
  {
    id: "trace-ingest",
    name: "Trace Ingest",
    role: "接入真实/模拟 Agent 执行轨迹，统一 runtime events、assertions、token usage。",
    inputs: ["Aime Trace", "Codex JSONL", "Fixture Run"],
    outputs: ["Normalized ExecutionResult"],
  },
  {
    id: "evidence",
    name: "Evidence Builder",
    role: "冻结失败证据与 artifact refs，保证后续修复和报告可审计。",
    inputs: ["ExecutionResult", "Assertions"],
    outputs: ["Evidence Snapshot", "sha256 refs"],
  },
  {
    id: "attribution",
    name: "Attribution Agent",
    role: "定位 t*、fault chain 与责任边界，区分 content-gap / loading-miss / platform-error。",
    inputs: ["Trace", "Skill content", "Business signals"],
    outputs: ["Fault taxonomy", "Repair action"],
  },
  {
    id: "planner",
    name: "Repair Planner",
    role: "根据归因选择 patch_skill、patch_loader 或 split_non_skill，生成最小修复原则。",
    inputs: ["Attribution", "Reject Memory"],
    outputs: ["Candidate plan", "Constraints"],
  },
  {
    id: "candidate",
    name: "Candidate Generator",
    role: "生成只读候选 Skill，不覆盖生产版本，并记录可解释 patch。",
    inputs: ["Base Skill", "Repair plan"],
    outputs: ["Candidate Skill", "Patch diff"],
  },
  {
    id: "verifier",
    name: "Regression Verifier",
    role: "运行默认+保存用例，使用 ADOPT/REJECT 门禁检查修复收益与回归风险。",
    inputs: ["Candidate", "Diagnostic suite"],
    outputs: ["Validation report", "Decision"],
  },
  {
    id: "memory",
    name: "Reject Memory + Storage",
    role: "持久化失败候选和运行资产，支持 File/SQLite 后端与后续 Postgres 扩展。",
    inputs: ["Rejected candidates", "Runs", "Benchmarks"],
    outputs: ["Constraints", "Reports"],
  },
];

function ArchitectureDashboard() {
  return (
    <section className="architecture-view">
      <div className="section-intro architecture-intro">
        <div>
          <span className="kicker">Agent Architecture / 多阶段自愈工作流</span>
          <h2>
            从失败 Trace 到候选修复，形成
            <em>可解释、可回归、可记忆</em> 的闭环。
          </h2>
        </div>
        <div className="architecture-badge">
          <span>LangGraph Pipeline</span>
          <strong>{architectureStages.length} stages</strong>
          <small>Trace → Evidence → Attribution → Repair → Verify → Memory</small>
        </div>
      </div>

      <div className="architecture-flow">
        {architectureStages.map((stage, index) => (
          <article className="panel architecture-stage" key={stage.id}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{stage.name}</strong>
            <p>{stage.role}</p>
            <div>
              <small>Inputs</small>
              {stage.inputs.map((item) => <code key={item}>{item}</code>)}
            </div>
            <div>
              <small>Outputs</small>
              {stage.outputs.map((item) => <code key={item}>{item}</code>)}
            </div>
          </article>
        ))}
      </div>

      <div className="architecture-principles">
        <article className="panel">
          <span>Safety Gate</span>
          <strong>默认拒绝风险变更</strong>
          <p>所有候选必须通过 suite-level validation；tool/platform 失败不会修改 Skill。</p>
        </article>
        <article className="panel">
          <span>Memory</span>
          <strong>避免重复失败方案</strong>
          <p>Reject Memory 会在后续生成中注入约束，并拦截完全重复 patch。</p>
        </article>
        <article className="panel">
          <span>Observability</span>
          <strong>每个决策都有证据</strong>
          <p>保留 evidence snapshot、runtime events、token usage 和 markdown report。</p>
        </article>
      </div>
    </section>
  );
}

function BenchmarkDashboard({
  fallbackReport,
  activeRun,
  activeBenchmark,
  onBenchmarkState,
  onOpenChild,
}: {
  fallbackReport: PairedBenchmarkReport;
  activeRun: LangGraphState | null;
  activeBenchmark: BenchmarkState | null;
  onBenchmarkState: (state: BenchmarkState) => void;
  onOpenChild: (runId: string) => void;
}) {
  const report = activeBenchmark?.report ?? fallbackReport;
  const activeBenchmarkScenario = activeRun?.scenario;
  const [executor, setExecutor] = useState<"fixture" | "replay" | "codex">(
    "fixture",
  );
  const [skillId, setSkillId] = useState("tdd-workflow");
  const [task, setTask] = useState(
    "Use the target Skill to produce a verified implementation plan.",
  );
  const [timeoutSeconds, setTimeoutSeconds] = useState(180);
  const [runStatus, setRunStatus] = useState<
    "idle" | "running" | "completed" | "failed"
  >("idle");
  const [runError, setRunError] = useState<string | null>(null);
  const benchmarkAbort = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      benchmarkAbort.current?.abort();
    },
    [],
  );

  const linkedPair = activeRun
    ? report.pairs.find((pair) => pair.skillId === activeRun.skill_id)
    : undefined;

  const runBenchmark = async () => {
    benchmarkAbort.current?.abort();
    const controller = new AbortController();
    benchmarkAbort.current = controller;
    setRunStatus("running");
    setRunError(null);
    try {
      await streamBenchmarkRun(
        {
          executor,
          scenario:
            activeBenchmarkScenario === "loading-miss" ||
            activeBenchmarkScenario === "platform-error" ||
            activeBenchmarkScenario === "network-error"
              ? activeBenchmarkScenario
              : "content-gap",
          skill_id: skillId,
          task,
          codex_timeout_ms: timeoutSeconds * 1_000,
        },
        (state) => {
          onBenchmarkState(state);
          if (state.status === "completed") setRunStatus("completed");
          if (state.status === "failed") setRunStatus("failed");
        },
        { signal: controller.signal },
      );
    } catch (error) {
      if (controller.signal.aborted) {
        setRunStatus("idle");
        return;
      }
      setRunStatus("failed");
      setRunError(
        error instanceof Error ? error.message : "动态配对评测启动失败。",
      );
    }
  };

  return (
    <section className="benchmark-view">
      <div className="section-intro benchmark-intro">
        <div>
          <span className="kicker">动态配对 Skill 评测</span>
          <h2>
            同一任务跑两次，量化 Skill 的
            <em>真实收益与成本。</em>
          </h2>
        </div>
        <div className="benchmark-run-meta">
          <span>运行 ID</span>
          <code>{activeBenchmark?.run_id ?? report.runId}</code>
          <small>{new Date(report.generatedAt).toLocaleString("zh-CN")}</small>
        </div>
      </div>

      <article className="benchmark-launcher panel">
        <div>
          <span>动态配对运行</span>
          <strong>从同一基线启动 Control / Treatment</strong>
        </div>
        <label>
          <span>执行器</span>
          <select
            value={executor}
            onChange={(event) =>
              setExecutor(
                event.target.value as "fixture" | "replay" | "codex",
              )
            }
            disabled={runStatus === "running"}
          >
            <option value="fixture">Fixture</option>
            <option value="replay">Codex Replay</option>
            <option value="codex">Codex SDK Live</option>
          </select>
        </label>
        <label>
          <span>Skill</span>
          <select
            value={skillId}
            onChange={(event) => setSkillId(event.target.value)}
            disabled={runStatus === "running"}
          >
            <option value="tdd-workflow">tdd-workflow</option>
            <option value="python-observability">
              python-observability
            </option>
            <option value="distributed-tracing">
              distributed-tracing
            </option>
            <option value="spreadsheet-summary">
              spreadsheet-summary
            </option>
          </select>
        </label>
        <label className="benchmark-task-input">
          <span>任务</span>
          <input
            value={task}
            onChange={(event) => setTask(event.target.value)}
            disabled={runStatus === "running"}
          />
        </label>
        <label>
          <span>超时</span>
          <input
            type="number"
            min={10}
            max={600}
            value={timeoutSeconds}
            onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
            disabled={runStatus === "running"}
          />
        </label>
        {runStatus === "running" ? (
          <button
            type="button"
            className="secondary"
            onClick={() => benchmarkAbort.current?.abort()}
          >
            停止评测
          </button>
        ) : (
          <button type="button" onClick={() => void runBenchmark()}>
            启动配对评测 ↗
          </button>
        )}
      </article>

      {runError && <div className="graph-error">{runError}</div>}

      {activeBenchmark && (
        <article className="benchmark-live-pair panel">
          <div className="panel-heading">
            <span>实时评测 / {activeBenchmark.run_id}</span>
            <strong>{activeBenchmark.status.toUpperCase()}</strong>
          </div>
          <div className="benchmark-child-runs">
            <button
              type="button"
              disabled={!activeBenchmark.control_run_id}
              onClick={() =>
                activeBenchmark.control_run_id &&
                onOpenChild(activeBenchmark.control_run_id)
              }
            >
              <span>未加载 Skill</span>
              <strong>
                {activeBenchmark.control
                  ? plainPercent(
                      activeBenchmark.control.verifier.passRate,
                    )
                  : "运行中"}
              </strong>
              <small>
                {activeBenchmark.control_run_id ?? "等待子 Run"}
              </small>
            </button>
            <i>VS</i>
            <button
              type="button"
              disabled={!activeBenchmark.treatment_run_id}
              onClick={() =>
                activeBenchmark.treatment_run_id &&
                onOpenChild(activeBenchmark.treatment_run_id)
              }
            >
              <span>加载 Skill</span>
              <strong>
                {activeBenchmark.treatment
                  ? plainPercent(
                      activeBenchmark.treatment.verifier.passRate,
                    )
                  : "等待中"}
              </strong>
              <small>
                {activeBenchmark.treatment_run_id ?? "等待子 Run"}
              </small>
            </button>
          </div>
          <footer>
            {activeBenchmark.events.at(-1)?.message ??
              "评测状态已初始化。"}
          </footer>
        </article>
      )}

      {activeRun && (
        <div className="benchmark-run-link panel">
          <div>
            <span>当前 Agent Run</span>
            <strong>{activeRun.run_id}</strong>
            <small>
              {activeRun.skill_id} · {activeRun.executor} ·{" "}
              {linkedPair ? "已匹配配对评测" : "暂无历史配对"}
            </small>
          </div>
          <div>
            <span>证据快照</span>
            <code>
              {runEvidenceId(activeRun)?.slice(0, 16) ?? "pending"}
            </code>
            {activeRun.observability?.trace_url && (
              <a
                href={activeRun.observability.trace_url}
                target="_blank"
                rel="noreferrer"
              >
                在 LangSmith 查看 →
              </a>
            )}
          </div>
          <div>
            <span>当前运行评估</span>
            <strong>
              {plainPercent(
                activeRun.verification?.baseline_pass_rate ??
                  activeRun.baseline_execution?.pass_rate ??
                  0,
              )}{" "}
              →{" "}
              {plainPercent(
                activeRun.verification?.candidate_pass_rate ??
                  activeRun.execution?.pass_rate ??
                  0,
              )}
            </strong>
            <small>
              {activeRun.verification?.decision ?? activeRun.status} · 回归率{" "}
              {plainPercent(
                activeRun.verification?.regression_rate ??
                  activeRun.execution?.regression_rate ??
                  0,
              )}
            </small>
          </div>
        </div>
      )}

      <div className="benchmark-notice">
        <strong>范围说明</strong>
        <span>
          {activeBenchmark?.report
            ? `当前成绩来自 ${activeBenchmark.executor} 动态配对运行。`
            : activeBenchmark
              ? "动态评测运行中；完成后会自动替换下方历史成绩。"
              : "当前展示最近一次持久化成绩；可从上方启动新的配对运行。"}
        </span>
      </div>

      <div className="benchmark-kpis">
        <article className="benchmark-kpi panel">
          <span>平均通过率提升</span>
          <strong>
            {signedPercent(report.summary.averagePassRateDelta)}
          </strong>
          <small>{report.summary.improved}/3 个 Skill 有提升</small>
        </article>
        <article className="benchmark-kpi panel">
          <span>平均 Token 开销</span>
          <strong>
            {signedPercent(report.summary.averageTokenOverheadRate)}
          </strong>
          <small>质量收益伴随额外成本</small>
        </article>
        <article className="benchmark-kpi panel">
          <span>平均耗时变化</span>
          <strong>
            {signedDuration(report.summary.averageDurationDeltaMs)}
          </strong>
          <small>Treatment 减去 Control</small>
        </article>
        <article className="benchmark-kpi panel">
          <span>回归率</span>
          <strong>{signedPercent(report.summary.regressionRate)}</strong>
          <small>加载 Skill 后丢失的检查项</small>
        </article>
      </div>

      <article className="benchmark-table-panel panel">
        <div className="panel-heading">
          <span>加载 Skill / 未加载 Skill</span>
          <strong>{report.summary.completedPairs} 组完成</strong>
        </div>
        <div className="benchmark-table-wrap">
          <table className="benchmark-table">
            <thead>
              <tr>
                <th>Skill</th>
                <th>对照组</th>
                <th>加载 Skill</th>
                <th>通过率变化</th>
                <th>Token 开销</th>
                <th>耗时变化</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {report.pairs.map((pair) => (
                <tr
                  key={pair.skillId}
                  className={
                    activeRun?.skill_id === pair.skillId
                      ? "current-run-pair"
                      : undefined
                  }
                >
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
                  <span>未加载</span>
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
                  <span>加载 Skill</span>
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
                <span>新增通过项</span>
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
                  Token 变化{" "}
                  {formatTokens(pair.comparison.tokenDelta ?? 0)}
                </span>
                <span>
                  回归率{" "}
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

function DiagnosticsDashboard() {
  const [report, setReport] = useState<DiagnosticSuiteReport | null>(null);
  const [status, setStatus] = useState<"idle" | "running" | "completed" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);

  const runDiagnostics = async () => {
    setStatus("running");
    setError(null);
    try {
      const next = await runDefaultDiagnostics();
      setReport(next);
      setStatus(next.status === "passed" ? "completed" : "failed");
    } catch (caught) {
      setStatus("failed");
      setError(caught instanceof Error ? caught.message : "诊断套件运行失败。");
    }
  };

  const activeReport = report;
  return (
    <section className="diagnostics-view">
      <div className="section-intro diagnostics-intro">
        <div>
          <span className="kicker">Trace 回归套件 / 可解释报告</span>
          <h2>
            批量验证多类 Agent Skill 故障，生成<em>可复现诊断报告。</em>
          </h2>
        </div>
        <button
          type="button"
          className="run-button diagnostics-run-button"
          disabled={status === "running"}
          onClick={() => void runDiagnostics()}
        >
          <span>{status === "running" ? "诊断运行中" : "运行默认套件"}</span>
          <b>↗</b>
        </button>
      </div>

      {error ? <div className="graph-error">{error}</div> : null}

      <div className="diagnostics-kpis">
        <article className="panel">
          <span>套件状态</span>
          <strong>{activeReport?.status === "passed" ? "通过" : status === "running" ? "运行中" : "待运行"}</strong>
          <small>{activeReport?.name ?? "Core Trace Regression Suite"}</small>
        </article>
        <article className="panel">
          <span>通过率</span>
          <strong>{activeReport ? plainPercent(activeReport.summary.pass_rate) : "—"}</strong>
          <small>{activeReport ? `${activeReport.summary.passed}/${activeReport.summary.total} 个用例通过` : "等待运行"}</small>
        </article>
        <article className="panel">
          <span>可修复用例</span>
          <strong>{activeReport?.summary.repairable ?? "—"}</strong>
          <small>会开放 Skill/loader 修复通道</small>
        </article>
        <article className="panel">
          <span>真实回归用例</span>
          <strong>{activeReport?.summary.saved_cases ?? "—"}</strong>
          <small>由真实 Aime Trace 沉淀</small>
        </article>
      </div>

      {activeReport ? (
        <>
          <div className="diagnostics-case-grid">
            {activeReport.cases.map((item) => (
              <article className={`panel diagnostics-case ${item.passed ? "passed" : "failed"}`} key={item.case_id}>
                <div className="panel-heading">
                  <span>{item.source === "saved_run" ? "真实 Trace" : item.category}</span>
                  <strong>{item.passed ? "通过" : "失败"}</strong>
                </div>
                <h3>{item.name}</h3>
                <p>{item.description}</p>
                <dl>
                  <div>
                    <dt>Run</dt>
                    <dd>{item.run_id}</dd>
                  </div>
                  <div>
                    <dt>归因</dt>
                    <dd>{item.attribution.cause} / {item.attribution.fault_type}</dd>
                  </div>
                  <div>
                    <dt>动作</dt>
                    <dd>{item.attribution.action}</dd>
                  </div>
                </dl>
                <blockquote>{item.attribution.explanation}</blockquote>
                <div className="diagnostics-checks">
                  {item.checks.map((check) => (
                    <span className={check.passed ? "passed" : "failed"} key={check.name}>
                      {check.passed ? "✓" : "!"} {check.name}
                    </span>
                  ))}
                </div>
              </article>
            ))}
          </div>
          <article className="panel diagnostics-markdown">
            <div className="panel-heading">
              <span>Markdown 报告</span>
              <strong>可复制</strong>
            </div>
            <pre>{activeReport.markdown}</pre>
          </article>
        </>
      ) : (
        <article className="panel diagnostics-empty">
          <h3>默认套件覆盖 4 条核心链路</h3>
          <p>健康 Trace 快路径、Skill 内容缺口、Skill 未加载、平台网络失败。运行后会展示每个用例的归因、修复策略、期望检查和 Markdown 报告。</p>
        </article>
      )}
    </section>
  );
}

export default function DemoApp() {
  const {
    snapshot,
    clearRun,
    runs,
    registryStatus,
    selectRun,
    benchmarkSnapshot,
    setBenchmarkSnapshot,
  } = useRunStore();
  const [view, setView] = useState<View>("overview");
  const [selectedCaseId, setSelectedCaseId] = useState(demoCases[0].id);
  const [importedCases, setImportedCases] = useState<DemoCase[]>([]);
  const [importState, setImportState] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);

  const availableCases = useMemo(
    () => [...demoCases, ...importedCases],
    [importedCases],
  );
  const caseStudies = useMemo(
    () => availableCases.map((item) => analyzeCase(item)),
    [availableCases],
  );
  const selectedCase =
    availableCases.find((item) => item.id === selectedCaseId) ?? demoCases[0];
  const sampleResult = useMemo(
    () => analyzeCase(selectedCase),
    [selectedCase],
  );
  const result = useMemo(
    () => (snapshot ? adaptLangGraphState(snapshot) : sampleResult),
    [snapshot, sampleResult],
  );
  const activeCase = result.input;
  const isSkillPatch = result.repair.kind === "skill_patch";
  const changedLineCount =
    result.repair.kind === "skill_patch"
      ? Array.from({
          length: Math.max(
            result.repair.before.length,
            result.repair.after.length,
          ),
        }).filter(
          (_, index) =>
            result.repair.kind === "skill_patch" &&
            result.repair.before[index] !== result.repair.after[index],
        ).length
      : 0;
  const repairLineCount =
    result.repair.kind === "skill_patch"
      ? Math.max(result.repair.before.length, result.repair.after.length)
      : 0;
  const showsRunContext = ["trace", "usage", "diagnosis", "patch"].includes(
    view,
  );
  const showsCaseControls = ["trace", "usage", "diagnosis", "patch"].includes(
    view,
  );
  const maxStepTokens = Math.max(
    ...activeCase.trace.map(stepTokenTotal),
    1,
  );
  const activePrimaryView = ["trace", "usage", "diagnosis", "patch"].includes(
    view,
  )
    ? "trace"
    : view;

  const goToView = (nextView: View) => {
    setView(nextView);
  };

  const rerun = () => {
    goToView("orchestrator");
  };

  const openRun = async (run: RunSummary) => {
    await selectRun(run.run_id, run.run_kind);
    goToView(run.run_kind === "benchmark" ? "evaluation" : "trace");
  };

  const openChildRun = async (runId: string) => {
    await selectRun(runId, "agent");
    goToView("trace");
  };

  const openParentBenchmark = async (runId: string) => {
    await selectRun(runId, "benchmark");
    goToView("evaluation");
  };

  const selectCaseForCurrentDetail = (caseId: string) => {
    clearRun();
    setSelectedCaseId(caseId);
  };

  const openCaseTrace = (caseId: string) => {
    clearRun();
    setSelectedCaseId(caseId);
    goToView("trace");
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
      clearRun();
      setSelectedCaseId(imported.id);
      goToView("trace");
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
          <p className="brand-caption">基于 Trace 的 Skill 自愈</p>
        </div>

        <nav aria-label="Demo sections">
          {views.map((item) => (
            <button
              type="button"
              key={item.id}
              className={activePrimaryView === item.id ? "active" : ""}
              aria-current={activePrimaryView === item.id ? "page" : undefined}
              onClick={() => goToView(item.id)}
              data-testid={`nav-${item.id}`}
            >
              <span>{item.eyebrow}</span>
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className="live-dot" />
          <span>{snapshot ? "实时 Agent Run" : "示例数据"}</span>
          <small>{activeCase.id}</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="kicker">Skill 自愈实验台 / 本地运行</span>
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
              <span>启动统一 Run</span>
              <b>↗</b>
            </button>
          </div>
        </header>

        {(runs.length > 0 || snapshot || benchmarkSnapshot) && (
          <section className="run-registry panel" aria-label="Agent runs">
            <div className="run-registry-heading">
              <div>
                <span>Run 注册中心 / SSE</span>
                <strong>后端运行中心</strong>
              </div>
              <small className={registryStatus}>
                <i />
                {registryStatus}
              </small>
            </div>
            <div className="run-registry-list">
              {runs.slice(0, 8).map((run) => (
                <button
                  type="button"
                  key={run.run_id}
                  className={
                    snapshot?.run_id === run.run_id ||
                    benchmarkSnapshot?.run_id === run.run_id
                      ? "active"
                      : ""
                  }
                  onClick={() => void openRun(run)}
                >
                  <span>
                    {run.run_kind} / {run.status}
                  </span>
                  <strong>{run.skill_id}</strong>
                  <code>{run.run_id}</code>
                  <small>
                    {run.executor} · {run.event_count} 个事件
                  </small>
                </button>
              ))}
            </div>
          </section>
        )}

        {importState && (
          <div
            className={`import-status ${importState.kind}`}
            role={importState.kind === "error" ? "alert" : "status"}
          >
            <span>{importState.kind === "success" ? "✓" : "!"}</span>
            {importState.message}
          </div>
        )}

        {showsRunContext && (
          <div className={`run-provenance ${snapshot ? "live" : "sample"}`}>
            <div>
              <span>{snapshot ? "实时运行来源" : "预运行示例"}</span>
              <strong>{activeCase.id}</strong>
            </div>
            <div>
              <span>执行器</span>
              <code>{snapshot?.executor ?? "deterministic-demo-engine"}</code>
            </div>
            <div>
              <span>证据</span>
              <code>
                {runEvidenceId(snapshot)?.slice(0, 16) ??
                  (snapshot ? "pending" : "sample-fixture")}
              </code>
            </div>
            <div className="run-provenance-actions">
              {snapshot?.parent_run_id ? (
                <button
                  type="button"
                  onClick={() =>
                    void openParentBenchmark(snapshot.parent_run_id!)
                  }
                >
                  返回评测 →
                </button>
              ) : null}
              {snapshot?.observability?.trace_url ? (
                <a
                  href={snapshot.observability.trace_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  在 LangSmith 查看 →
                </a>
              ) : (
                <button type="button" onClick={() => goToView("orchestrator")}>
                  {snapshot ? "查看实时链路 →" : "启动 Agent Run →"}
                </button>
              )}
            </div>
          </div>
        )}

        {showsCaseControls && (
          <>
            <section
              className="case-tabs"
              aria-label="Failure scenarios"
            >
              <span>当前案例</span>
              {availableCases.map((item, index) => (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === activeCase.id ? "active" : ""}
                  onClick={() => selectCaseForCurrentDetail(item.id)}
                  data-testid={`case-${item.id}`}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{item.name}</strong>
                </button>
              ))}
            </section>

            <nav className="detail-step-nav" aria-label="单案例分析步骤">
              {detailViews.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={view === item.id ? "active" : ""}
                  aria-current={view === item.id ? "step" : undefined}
                  onClick={() => goToView(item.id)}
                >
                  <strong>{item.label}</strong>
                  <span>{item.detail}</span>
                </button>
              ))}
            </nav>
          </>
        )}

        {view === "overview" && (
          <section className="view-grid">
            <article className="case-panel panel">
              <div className="panel-heading">
                <span>失败会话 / {activeCase.name}</span>
                <strong>可复现</strong>
              </div>
              <h2>{activeCase.task}</h2>
              <div className="outcome-grid">
                <div>
                  <span>预期结果</span>
                  <strong>{activeCase.expected}</strong>
                </div>
                <div className="bad-value">
                  <span>实际结果</span>
                  <strong>{activeCase.actual}</strong>
                </div>
              </div>
              <dl className="case-facts">
                <div>
                  <dt>Trace</dt>
                  <dd>
                    {activeCase.trace.length} 步 /{" "}
                    {formatTokens(result.usage.totalTokens)} Tokens
                  </dd>
                </div>
                <div>
                  <dt>Skill</dt>
                  <dd>
                    {activeCase.skill.id}@{activeCase.skill.version}
                  </dd>
                </div>
                <div>
                  <dt>运行时</dt>
                  <dd>
                    {snapshot
                      ? `${snapshot.executor} / LangGraph`
                      : "本地确定性 Harness"}
                  </dd>
                </div>
              </dl>
              <div className="overview-actions" aria-label="推荐讲解路径">
                {overviewActions.map((item) => {
                  const isActive = activePrimaryView === item.id;
                  return (
                    <button
                      type="button"
                      key={item.id}
                      className={isActive ? "active" : ""}
                      aria-current={isActive ? "page" : undefined}
                      onClick={() => goToView(item.id)}
                      data-testid={`overview-action-${item.id}`}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </article>

            <article className="decision-panel panel">
              <div className="panel-heading">
                <span>修复决策</span>
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
                <p>{isSkillPatch ? "原始用例回放" : "安全变更策略"}</p>
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
              <span>归因</span>
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
              <span>Skill 责任</span>
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
              <span>变更安全性</span>
              <strong>{isSkillPatch ? "1" : "0"}</strong>
              <p>{isSkillPatch ? "条最小化指令变更" : "条 Skill 内容变更"}</p>
              <div style={{ "--meter": "100%" } as React.CSSProperties}>
                <i />
              </div>
            </article>
          </section>
        )}

        {view === "cases" && (
          <CaseStudyGallery
            studies={caseStudies}
            selectedCaseId={selectedCaseId}
            onOpenCase={openCaseTrace}
          />
        )}

        {view === "trace" && (
          <section className="trace-view">
            <div className="section-intro">
              <div>
                <span className="kicker">证据快照</span>
                <h2>从 Skill 路由到结果失败，证据链保持完整。</h2>
              </div>
              <dl>
                <div>
                  <dt>Snapshot</dt>
                  <dd>
                    {runEvidenceId(snapshot)
                      ? `sha256:${runEvidenceId(snapshot)?.slice(0, 12)}…`
                      : snapshot
                        ? "pending"
                        : "sample-fixture"}
                  </dd>
                </div>
                <div>
                  <dt>加载状态</dt>
                  <dd>
                    {activeCase.skill.loaded
                      ? `${activeCase.skill.id}@${activeCase.skill.version}`
                      : "INCOMPLETE / RESOURCE MISSING"}
                  </dd>
                </div>
                <div>
                  <dt>Token 用量</dt>
                  <dd>{formatTokens(result.usage.totalTokens)} 总量</dd>
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
          <BenchmarkDashboard
            fallbackReport={benchmarkReport}
            activeRun={snapshot}
            activeBenchmark={benchmarkSnapshot}
            onBenchmarkState={setBenchmarkSnapshot}
            onOpenChild={(runId) => void openChildRun(runId)}
          />
        )}

        {view === "evaluation" && (
          <EvaluationDashboard summary={evaluationSummary} />
        )}

        {view === "architecture" && <ArchitectureDashboard />}

        {view === "diagnostics" && <DiagnosticsDashboard />}

        {view === "orchestrator" && <LangGraphDashboard />}

        {view === "diagnosis" && (
          <section className="diagnosis-view">
            <div className="section-intro">
              <div>
                <span className="kicker">步骤级归因</span>
                <h2>
                  最早可行动故障发生在{" "}
                  <em>{result.diagnosis.primaryFaultStep}</em>。
                </h2>
              </div>
              <div className="confidence-block">
                <strong>
                  <Percent value={result.diagnosis.confidence} />
                </strong>
                <span>置信度</span>
              </div>
            </div>

            <div className="diagnosis-grid">
              <article className="taxonomy-card panel">
                <span>七类故障分类</span>
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
                  <span>因果机制</span>
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
                <h3>证据引用</h3>
                <div className="evidence-list">
                  {result.diagnosis.evidenceRefs.map((ref) => (
                    <code key={ref}>{ref}</code>
                  ))}
                </div>
                <h3 className="rule-heading">
                  规则证明 / {result.diagnosis.ruleVersion}
                </h3>
                <div className="rule-proof">
                  {result.diagnosis.ruleEvaluations.map((rule) => (
                    <div
                      key={rule.ruleId}
                      className={rule.selected ? "selected" : ""}
                    >
                      <code>{rule.ruleId}</code>
                      <span>{rule.taxonomy}</span>
                      <b>{rule.selected ? "命中" : "排除"}</b>
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
                      最小范围修复 + 验证
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
                      <span>SKILL.MD / 执行流程</span>
                      <strong>{changedLineCount} 行变更</strong>
                    </div>
                    <div className="diff-lines">
                      {Array.from({ length: repairLineCount }).map(
                        (_, index) => {
                          const beforeLine = result.repair.before[index];
                          const afterLine = result.repair.after[index];
                          const changed = beforeLine !== afterLine;
                          return changed ? (
                            <div
                              className="diff-change"
                              key={`diff-${index}`}
                            >
                              {beforeLine !== undefined && (
                                <p className="removed">
                                  <span>-</span>
                                  {beforeLine}
                                </p>
                              )}
                              {afterLine !== undefined && (
                                <p className="added">
                                  <span>+</span>
                                  {afterLine}
                                </p>
                              )}
                            </div>
                          ) : (
                            <p key={`line-${index}`}>
                              <span>{index + 1}</span>
                              {beforeLine}
                            </p>
                          );
                        },
                      )}
                    </div>
                    <footer>
                      <span>scope: {result.repair.scope}</span>
                      <span>rollback: {result.repair.rollbackRef}</span>
                    </footer>
                  </article>

                  <article className="validation-panel panel">
                    <div className="panel-heading">
                      <span>验证门禁</span>
                      <strong className="adopt-chip">
                        {result.validation.decision}
                      </strong>
                    </div>
                    <div className="validation-bars">
                      <div>
                        <span>当前运行通过率</span>
                        <p>
                          <i
                            style={{
                              width: `${result.validation.originalReplay.after * 100}%`,
                            }}
                          />
                        </p>
                        <strong>
                          {plainPercent(
                            result.validation.originalReplay.before,
                          )}{" "}
                          →{" "}
                          {plainPercent(
                            result.validation.originalReplay.after,
                          )}
                        </strong>
                      </div>
                      <div>
                        <span>验证样本</span>
                        <p>
                          <i
                            style={{
                              width: `${result.validation.similarCases.after * 100}%`,
                            }}
                          />
                        </p>
                        <strong>
                          {plainPercent(
                            result.validation.similarCases.before,
                          )}{" "}
                          →{" "}
                          {plainPercent(
                            result.validation.similarCases.after,
                          )}
                        </strong>
                      </div>
                      <div>
                        <span>回归保持率</span>
                        <p>
                          <i
                            style={{
                              width: `${result.validation.regression.after * 100}%`,
                            }}
                          />
                        </p>
                        <strong>
                          {plainPercent(result.validation.regression.before)}{" "}
                          →{" "}
                          {plainPercent(result.validation.regression.after)}
                        </strong>
                      </div>
                      <div>
                        <span>工具错误</span>
                        <p>
                          <i style={{ width: "0%" }} />
                        </p>
                        <strong>
                          {result.validation.toolErrors.before} →{" "}
                          {result.validation.toolErrors.after}
                        </strong>
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
                      安全拒绝 + 故障路由
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
                    <p className="safe-refusal">不修改 Skill</p>
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
                      <span>隔离门禁</span>
                      <strong className="adopt-chip">
                        {result.validation.decision}
                      </strong>
                    </div>
                    <div className="isolation-score">
                      <span>Skill 变更数</span>
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
