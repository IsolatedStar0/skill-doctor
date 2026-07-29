"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createCandidateSkill,
  createRepairPreview,
  listScenarios,
  listRejectionHistory,
  saveRunAsDiagnosticCase,
  streamLangGraphRun,
  validateCandidateSkill,
  verifyRepair,
  type CandidateSkillResponse,
  type CandidateValidationReport,
  type LangGraphState,
  type RepairPreview,
  type RepairVerificationReport,
  type RejectionHistoryResponse,
  type ScenarioCatalogItem,
} from "../lib/langgraph-stream";
import { useRunStore } from "./RunStore";
import BusinessResultCard from "./BusinessResultCard";

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

const statusCopy: Record<string, string> = {
  idle: "待启动",
  running: "运行中",
  passed: "已通过",
  failed: "未通过",
  error: "连接异常",
  completed: "已完成",
  started: "已开始",
  skipped: "已跳过",
};

const modeCopy: Record<RunMode, { eyebrow: string; title: string }> = {
  fixture: { eyebrow: "离线模式", title: "确定性自修复" },
  replay: { eyebrow: "证据重放", title: "真实配对重放" },
  codex: { eyebrow: "Codex Live", title: "真实 Agent 执行" },
};

const sourceCopy = {
  graph: "状态图",
  sdk: "SDK 事件",
};

const faultTypeCopy: Record<string, string> = {
  skill_wrong: "Skill 内容错误",
  skill_missing: "Skill 缺失",
  reasoning_wrong: "推理/平台问题",
  unknown: "未知类型",
};

const fallbackScenarioOptions: ScenarioCatalogItem[] = [
  {
    id: "content-gap",
    name: "内容缺口",
    summary: "Skill 已加载但遗漏关键约束，期望进入 Skill 修订链路。",
    category: "skill",
    skill_id: "spreadsheet-summary",
    task: "读取 100 行订单 CSV，汇总总营收并输出 Markdown 报告。",
    expected: "¥428,650（基于全部 100 行订单）",
    actual: "¥82,410（错误地只统计预览的 20 行）",
    executor: "fixture",
    repair_action: "patch_skill",
  },
  {
    id: "loading-miss",
    name: "加载遗漏",
    summary: "Skill 被选中但引用资源缺失，期望归因为 loader 问题。",
    category: "loader",
    skill_id: "release-checklist",
    task: "按仓库规范生成发布检查清单，并包含安全回滚步骤。",
    expected: "依据 release-policy references 生成 8 项检查清单",
    actual: "只生成 4 项通用检查，缺少回滚和审批门禁",
    executor: "fixture",
    repair_action: "patch_loader",
  },
  {
    id: "platform-error",
    name: "平台异常",
    summary: "执行失败来自外部服务边界，期望拒绝修改 Skill。",
    category: "platform",
    skill_id: "skill-release",
    task: "把验证通过的候选 Skill 发布到远端注册表。",
    expected: "注册表返回 release id，候选版本进入 staged 状态",
    actual: "registry.publish 返回 403 insufficient_scope",
    executor: "fixture",
    repair_action: "split_non_skill",
  },
];

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

function tokenBreakdown(usage: LangGraphState["events"][number]["usage"]) {
  if (!usage) return null;
  return [
    `输入 ${usage.input_tokens.toLocaleString("zh-CN")}`,
    `输出 ${usage.output_tokens.toLocaleString("zh-CN")}`,
    usage.cached_input_tokens
      ? `缓存 ${usage.cached_input_tokens.toLocaleString("zh-CN")}`
      : null,
    usage.reasoning_tokens
      ? `推理 ${usage.reasoning_tokens.toLocaleString("zh-CN")}`
      : null,
  ].filter(Boolean).join(" / ");
}

export default function LangGraphDashboard() {
  const [mode, setMode] = useState<RunMode>("fixture");
  const [scenario, setScenario] = useState<ScenarioCatalogItem["id"]>("content-gap");
  const [scenarioCatalog, setScenarioCatalog] = useState<ScenarioCatalogItem[]>(fallbackScenarioOptions);
  const [uiStatus, setUiStatus] = useState<UiStatus>("idle");
  const { snapshot, setSnapshot } = useRunStore();
  const [error, setError] = useState<string | null>(null);
  const [caseSaveStatus, setCaseSaveStatus] = useState<string | null>(null);
  const [repairPreview, setRepairPreview] = useState<RepairPreview | null>(null);
  const [previewStatus, setPreviewStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [baselineRunId, setBaselineRunId] = useState("");
  const [candidateRunId, setCandidateRunId] = useState("");
  const [verificationReport, setVerificationReport] = useState<RepairVerificationReport | null>(null);
  const [verificationStatus, setVerificationStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [candidateSkill, setCandidateSkill] = useState<CandidateSkillResponse["candidate"] | null>(null);
  const [candidateValidation, setCandidateValidation] = useState<CandidateValidationReport | null>(null);
  const [candidateStatus, setCandidateStatus] = useState<"idle" | "creating" | "validating" | "done" | "error">("idle");
  const [rejectionHistory, setRejectionHistory] = useState<RejectionHistoryResponse | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const apiUrl =
    process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL ??
    "http://localhost:8010";
  const apiBaseUrl = apiUrl.replace(/\/$/, "");
  const streamUrl = `${apiBaseUrl}/runs/stream`;
  const healthUrl = `${apiBaseUrl}/health`;
  const scenariosUrl = `${apiBaseUrl}/scenarios`;
  const selectedScenario =
    scenarioCatalog.find((item) => item.id === scenario) ?? scenarioCatalog[0];

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    listScenarios(apiUrl)
      .then((catalog) => {
        if (!cancelled && catalog.scenarios.length) {
          setScenarioCatalog(catalog.scenarios);
          setScenario((current) =>
            catalog.scenarios.some((item) => item.id === current)
              ? current
              : catalog.scenarios[0].id,
          );
        }
      })
      .catch(() => {
        // Keep the local fallback so the demo remains usable offline.
      });
    return () => {
      cancelled = true;
    };
  }, [apiUrl]);

  const totalTokens = useMemo(() => tokens(snapshot), [snapshot]);
  const graphEventCount =
    snapshot?.events.filter((event) => !event.stage.startsWith("codex."))
      .length ?? 0;
  const codexEventCount =
    snapshot?.events.filter((event) => event.stage.startsWith("codex."))
      .length ?? 0;
  const terminal =
    snapshot?.status === "passed" || snapshot?.status === "failed";
  const displayedStatus =
    uiStatus === "idle" && terminal
      ? (snapshot.status as "passed" | "failed")
      : uiStatus;
  const progress = terminal
    ? 100
    : Math.min(96, Math.round((graphEventCount / 9) * 100));
  const skillContentLength = snapshot?.skill_content?.trim().length ?? 0;
  const hasSkillContent = skillContentLength > 0;
  const latestEvent = snapshot?.events.at(-1);
  const defaultBaselineRunId =
    snapshot?.executor === "trace-ingest"
      ? (snapshot.parent_run_id ?? (snapshot.status === "failed" ? snapshot.run_id : ""))
      : "";
  const defaultCandidateRunId =
    snapshot?.executor === "trace-ingest" ? snapshot.run_id : "";
  const effectiveBaselineRunId = baselineRunId || defaultBaselineRunId;
  const effectiveCandidateRunId = candidateRunId || defaultCandidateRunId;

  const run = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setSnapshot(null);
    setError(null);
    setCaseSaveStatus(null);
    setRepairPreview(null);
    setPreviewStatus("idle");
    setBaselineRunId("");
    setCandidateRunId("");
    setVerificationReport(null);
    setVerificationStatus("idle");
    setCandidateSkill(null);
    setCandidateValidation(null);
    setCandidateStatus("idle");
    setRejectionHistory(null);
    setUiStatus("running");
    try {
      await streamLangGraphRun(
        {
          executor: mode,
          scenario,
          skill_id: mode === "fixture" ? selectedScenario.skill_id : "tdd-workflow",
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

  const saveCase = async () => {
    if (!snapshot?.run_id) return;
    setCaseSaveStatus("保存中...");
    try {
      const result = await saveRunAsDiagnosticCase(snapshot.run_id, apiUrl);
      setCaseSaveStatus(`已保存为回归用例：${result.case.case_id}`);
    } catch (caught) {
      setCaseSaveStatus(caught instanceof Error ? caught.message : "保存回归用例失败。");
    }
  };

  const previewRepair = async () => {
    if (!snapshot?.run_id) return;
    setPreviewStatus("loading");
    setRepairPreview(null);
    try {
      const result = await createRepairPreview(snapshot.run_id, apiUrl);
      setRepairPreview(result);
      setPreviewStatus("done");
    } catch (caught) {
      setPreviewStatus("error");
      setCaseSaveStatus(caught instanceof Error ? caught.message : "生成修复预览失败。");
    }
  };

  const runVerification = async () => {
    if (!effectiveBaselineRunId.trim() || !effectiveCandidateRunId.trim()) {
      setCaseSaveStatus("请填写 baseline run 与 candidate run。通常 candidate 为当前修复后 Trace，baseline 为原失败 Trace。");
      return;
    }
    setVerificationStatus("loading");
    setVerificationReport(null);
    try {
      const result = await verifyRepair(
        effectiveBaselineRunId.trim(),
        effectiveCandidateRunId.trim(),
        apiUrl,
      );
      setVerificationReport(result);
      setVerificationStatus("done");
    } catch (caught) {
      setVerificationStatus("error");
      setCaseSaveStatus(caught instanceof Error ? caught.message : "修复验证失败。");
    }
  };

  const createAndValidateCandidate = async () => {
    if (!snapshot?.run_id) return;
    setCandidateStatus("creating");
    setCandidateSkill(null);
    setCandidateValidation(null);
    try {
      const history = await listRejectionHistory(snapshot.skill_id, apiUrl);
      setRejectionHistory(history);
      const created = await createCandidateSkill(snapshot.run_id, apiUrl);
      setCandidateSkill(created.candidate);
      setCandidateStatus("validating");
      const validation = await validateCandidateSkill(
        created.candidate.candidate_id,
        apiUrl,
      );
      setCandidateValidation(validation);
      const refreshed = await listRejectionHistory(snapshot.skill_id, apiUrl);
      setRejectionHistory(refreshed);
      setCandidateStatus("done");
    } catch (caught) {
      setCandidateStatus("error");
      setCaseSaveStatus(caught instanceof Error ? caught.message : "候选 Skill 验证失败。");
    }
  };

  return (
    <section className="langgraph-view">
      <div className="section-intro langgraph-intro">
        <div>
          <span className="kicker">实时状态图 / NDJSON 流</span>
          <h2>
            从第一次失败到修复升级，
            <em>逐节点观察 Agent 自愈。</em>
          </h2>
        </div>
        <div className={`graph-status ${displayedStatus}`}>
          <i />
          <span>{statusCopy[displayedStatus] ?? displayedStatus}</span>
          <small>{snapshot?.run_id ?? "等待启动"}</small>
        </div>
      </div>

      <div className="graph-control panel">
        <div className="graph-control-main">
          <div className="graph-control-header">
            <span>运行入口</span>
            <strong>先选执行方式，再选择要演示的故障类型。</strong>
            <p>
              页面会把控制面、实时指标、事件时间线和最终决策拆成四块，便于面试时按“启动 → 观察 → 归因 → 验证”讲解。
            </p>
          </div>
          <div className="graph-mode graph-executor-mode" aria-label="LangGraph executor">
            <button
              type="button"
              className={mode === "fixture" ? "active" : ""}
              onClick={() => setMode("fixture")}
              disabled={uiStatus === "running"}
            >
              <span>{modeCopy.fixture.eyebrow}</span>
              {modeCopy.fixture.title}
            </button>
            <button
              type="button"
              className={mode === "replay" ? "active" : ""}
              onClick={() => setMode("replay")}
              disabled={uiStatus === "running"}
            >
              <span>{modeCopy.replay.eyebrow}</span>
              {modeCopy.replay.title}
            </button>
            <button
              type="button"
              className={mode === "codex" ? "active" : ""}
              onClick={() => setMode("codex")}
              disabled={uiStatus === "running"}
            >
              <span>{modeCopy.codex.eyebrow}</span>
              {modeCopy.codex.title}
            </button>
          </div>
          {mode === "fixture" ? (
            <div className="graph-mode graph-scenario-mode" aria-label="Failure scenario">
              {scenarioCatalog.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={scenario === item.id ? "active" : ""}
                  onClick={() => setScenario(item.id)}
                  disabled={uiStatus === "running"}
                >
                  <span>{item.summary}</span>
                  {item.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <aside className="graph-run-card">
          <span>当前配置</span>
          <strong>{mode === "fixture" ? selectedScenario.name : modeCopy[mode].title}</strong>
          <p>
            {mode === "fixture"
              ? selectedScenario.summary
              : "连接真实执行链路后，会持续流出 Codex SDK / LangGraph 节点事件。"}
          </p>
          <dl>
            <div>
              <dt>执行模式</dt>
              <dd>{modeCopy[mode].eyebrow}</dd>
            </div>
            <div>
              <dt>Skill</dt>
              <dd>{mode === "fixture" ? selectedScenario.skill_id : "tdd-workflow"}</dd>
            </div>
            <div>
              <dt>最近事件</dt>
              <dd>{latestEvent ? (stageCopy[latestEvent.stage] ?? latestEvent.stage) : "等待启动"}</dd>
            </div>
          </dl>
          <div className="graph-api">
            <span>API 基地址</span>
            <a
              className="graph-api-url"
              href={apiBaseUrl}
              target="_blank"
              rel="noreferrer"
              title={apiBaseUrl}
            >
              {apiBaseUrl}
            </a>
            <div className="graph-api-links" aria-label="API 快速检查链接">
              <a href={healthUrl} target="_blank" rel="noreferrer">
                健康检查 ↗
              </a>
              <a href={scenariosUrl} target="_blank" rel="noreferrer">
                场景列表 ↗
              </a>
            </div>
            <small title={streamUrl}>POST 流式端点：{streamUrl}</small>
            {snapshot?.observability?.trace_url ? (
              <a
                href={snapshot.observability.trace_url}
                target="_blank"
                rel="noreferrer"
              >
                在 LangSmith 查看 ↗
              </a>
            ) : (
              <small>
                LangSmith {snapshot?.observability?.status ?? "可选"}
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
        </aside>
      </div>

      {error && (
        <div className="graph-error" role="alert">
          <strong>无法连接 Python 控制面</strong>
          <span>{error}</span>
          <code>npm run agent:api</code>
        </div>
      )}

      <div className="graph-progress" aria-label={`运行进度 ${progress}%`}>
        <i style={{ width: `${progress}%` }} />
      </div>

      <div className="graph-kpis">
        <article className="panel">
          <span>修复轮次</span>
          <strong>{snapshot?.attempt ?? 0}</strong>
          <small>最多 {snapshot?.max_attempts ?? 2} 次修复</small>
        </article>
        <article className="panel">
          <span>实时事件</span>
          <strong>{codexEventCount}</strong>
          <small>{graphEventCount} 个 LangGraph 节点</small>
        </article>
        <article className="panel">
          <span>Token 总量</span>
          <strong>{totalTokens.toLocaleString("zh-CN")}</strong>
          <small>按执行节点累计</small>
        </article>
        <article className="panel">
          <span>通过率变化</span>
          <strong>{percent(snapshot?.verification?.pass_rate_delta)}</strong>
          <small>
            回归率 {percent(snapshot?.verification?.regression_rate)}
          </small>
        </article>
      </div>

      {snapshot?.business_result ? (
        <BusinessResultCard result={snapshot.business_result} />
      ) : null}

      {snapshot?.executor === "trace-ingest" && terminal ? (
        <article className="panel graph-real-trace-actions">
          <div className="panel-heading">
            <span>真实 Trace 资产化</span>
            <strong>{snapshot.skill_id}</strong>
          </div>
          <p>
            当前 run 来自真实 Aime Trace，可沉淀为回归用例，并基于归因结果生成只读修复预览。
          </p>
          <div className={`skill-content-status ${hasSkillContent ? "available" : "missing"}`}>
            <span>Skill 原文</span>
            <strong>
              {hasSkillContent
                ? `已注入，${skillContentLength.toLocaleString("zh-CN")} 字符`
                : "未注入，仅基于 Trace 证据判断"}
            </strong>
          </div>
          {rejectionHistory ? (
            <div className="skill-content-status warning">
              <span>Reject Memory</span>
              <strong>
                历史拒绝 {rejectionHistory.count.toLocaleString("zh-CN")} 条
                {candidateSkill?.rejection_memory?.matched_count
                  ? ` · 本次匹配 ${candidateSkill.rejection_memory.matched_count} 条`
                  : ""}
              </strong>
            </div>
          ) : null}
          <div className="graph-action-row">
            <button type="button" onClick={() => void saveCase()}>
              保存为回归用例
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!snapshot.attribution || previewStatus === "loading"}
              onClick={() => void previewRepair()}
            >
              {previewStatus === "loading" ? "生成中" : "生成修复预览"}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!snapshot.attribution || candidateStatus === "creating" || candidateStatus === "validating"}
              onClick={() => void createAndValidateCandidate()}
            >
              {candidateStatus === "creating"
                ? "生成候选中"
                : candidateStatus === "validating"
                  ? "验证候选中"
                  : "生成候选并验证"}
            </button>
          </div>
          <div className="repair-verification-form">
            <label>
              <span>Baseline Run</span>
              <input
                value={effectiveBaselineRunId}
                placeholder="原失败 run_id"
                onChange={(event) => setBaselineRunId(event.target.value)}
              />
            </label>
            <label>
              <span>Candidate Run</span>
              <input
                value={effectiveCandidateRunId}
                placeholder="修复后 run_id"
                onChange={(event) => setCandidateRunId(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={verificationStatus === "loading"}
              onClick={() => void runVerification()}
            >
              {verificationStatus === "loading" ? "验证中" : "验证修复效果"}
            </button>
          </div>
          {caseSaveStatus ? <small>{caseSaveStatus}</small> : null}
          {repairPreview ? (
            <div className="repair-preview-card">
              <div>
                <span>{repairPreview.repair_type}</span>
                <strong>风险：{repairPreview.risk}</strong>
              </div>
              <p>{repairPreview.principle}</p>
              <pre>{repairPreview.suggested_patch.diff}</pre>
              <ol>
                {repairPreview.verification_plan.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </div>
          ) : null}
          {verificationReport ? (
            <div className={`repair-verification-card ${verificationReport.decision.toLowerCase()}`}>
              <div>
                <span>修复验证报告</span>
                <strong>{verificationReport.decision}</strong>
              </div>
              <div className="graph-pass-pair compact">
                <div>
                  <span>Baseline</span>
                  <strong>{percent(verificationReport.baseline.pass_rate)}</strong>
                </div>
                <b>→</b>
                <div>
                  <span>Candidate</span>
                  <strong>{percent(verificationReport.candidate.pass_rate)}</strong>
                </div>
              </div>
              <ul>
                {verificationReport.checks.map((check) => (
                  <li key={check.name} className={check.passed ? "passed" : "failed"}>
                    <span>{check.passed ? "✅" : "❌"}</span>
                    {check.label}
                  </li>
                ))}
              </ul>
              <p>{verificationReport.reasons[0]}</p>
            </div>
          ) : null}
          {candidateValidation ? (
            <div className={`repair-verification-card ${candidateValidation.decision.toLowerCase()}`}>
              <div>
                <span>候选 Skill 验证</span>
                <strong>{candidateValidation.decision}</strong>
              </div>
              <p>
                {candidateSkill?.candidate_id} · {candidateValidation.skill_id}@{candidateValidation.candidate_version}
              </p>
              <div className="graph-pass-pair compact">
                <div>
                  <span>Baseline Suite</span>
                  <strong>{percent(candidateValidation.baseline.pass_rate)}</strong>
                </div>
                <b>→</b>
                <div>
                  <span>Candidate Suite</span>
                  <strong>{percent(candidateValidation.candidate.pass_rate)}</strong>
                </div>
              </div>
              <ul>
                {candidateValidation.checks.map((check) => (
                  <li key={check.name} className={check.passed ? "passed" : "failed"}>
                    <span>{check.passed ? "✅" : "❌"}</span>
                    {check.label}
                  </li>
                ))}
              </ul>
              {candidateValidation.rejection_memory ? (
                <div className="reject-memory-card">
                  <strong>Reject Memory</strong>
                  <span>
                    匹配 {candidateValidation.rejection_memory.matched_count} 条
                    {candidateValidation.rejection_memory.recorded
                      ? ` · 已记录 ${candidateValidation.rejection_memory.recorded.rejection_id}`
                      : ""}
                  </span>
                  {candidateValidation.rejection_memory.constraints.length ? (
                    <ul>
                      {candidateValidation.rejection_memory.constraints.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
              <p>{candidateValidation.reasons[0]}</p>
            </div>
          ) : null}
        </article>
      ) : null}

      <div className="graph-observe-grid">
        <article className="graph-timeline panel">
          <div className="panel-heading">
            <span>LangGraph 事件时间线</span>
            <strong>{snapshot?.status ? (statusCopy[snapshot.status] ?? snapshot.status) : "待启动"}</strong>
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
                      第 {event.attempt} 轮 / {statusCopy[event.status] ?? event.status}
                    </small>
                    <strong>{stageCopy[event.stage] ?? event.stage}</strong>
                    <p>{event.message}</p>
                  </div>
                  <dl>
                    <div>
                      <dt>阶段</dt>
                      <dd>{event.stage}</dd>
                    </div>
                    <div>
                      <dt>来源</dt>
                      <dd>
                        {event.stage.startsWith("codex.") ? sourceCopy.sdk : sourceCopy.graph}
                      </dd>
                    </div>
                    <div>
                      <dt>Tokens</dt>
                      <dd>
                        {event.usage
                          ? (
                              event.usage.input_tokens +
                              event.usage.output_tokens
                            ).toLocaleString("zh-CN")
                          : "—"}
                      </dd>
                    </div>
                    {event.usage ? (
                      <div className="event-token-detail">
                        <dt>Token 明细</dt>
                        <dd>{tokenBreakdown(event.usage)}</dd>
                      </div>
                    ) : null}
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
              <span>故障归因</span>
              <strong>{snapshot?.attribution?.cause ?? "等待中"}</strong>
            </div>
            <h3>{snapshot?.attribution?.taxonomy ?? "等待失败证据"}</h3>
            {snapshot?.attribution?.agent_source === "llm" &&
            snapshot.attribution.agent_conclusion ? (
              <div className="agent-conclusion-card">
                <div>🤖 AI 归因结论 · DeepSeek</div>
                <p>{snapshot.attribution.agent_conclusion}</p>
                {snapshot.attribution.agent_reason ? (
                  <p className="agent-reason">
                    归因理由：{snapshot.attribution.agent_reason}
                  </p>
                ) : null}
                {snapshot.attribution.fault_type &&
                snapshot.attribution.fault_type !== "unknown" ? (
                  <p className="agent-fault-meta">
                    故障类型：
                    {faultTypeCopy[snapshot.attribution.fault_type] ??
                      snapshot.attribution.fault_type}
                    {typeof snapshot.attribution.t_star === "number"
                      ? ` · t* = ${snapshot.attribution.t_star}`
                      : ""}
                  </p>
                ) : null}
              </div>
            ) : (
              <p>
                {snapshot?.attribution?.explanation ??
                  "归因节点会区分 Skill、loader、tool 与 platform 责任。"}
              </p>
            )}
            <footer>
              置信度 {percent(snapshot?.attribution?.confidence)}
              {snapshot?.attribution?.agent_source
                ? ` · 来源=${snapshot.attribution.agent_source === "llm" ? "LLM" : "规则"}`
                : ""}
            </footer>
          </article>

          <article className="panel graph-patch-card">
            <div className="panel-heading">
              <span>修复补丁</span>
              <strong>{snapshot?.repair_patch?.kind ?? "等待中"}</strong>
            </div>
            {snapshot?.repair_patch ? (
              <>
                <p className="graph-diff removed">
                  − {snapshot.repair_patch.before}
                </p>
                <p className="graph-diff added">
                  + {snapshot.repair_patch.after}
                </p>
                <footer>回滚引用 {snapshot.repair_patch.rollback_ref}</footer>
              </>
            ) : (
              <p>只有高置信度 Skill/loader 责任才会开放写入通道。</p>
            )}
          </article>

          <article className="panel graph-gate-card">
            <div className="panel-heading">
              <span>验证门禁</span>
              <strong>{snapshot?.verification?.decision ?? "等待中"}</strong>
            </div>
            <div className="graph-pass-pair">
              <div>
                <span>修复前</span>
                <strong>
                  {percent(snapshot?.verification?.baseline_pass_rate)}
                </strong>
              </div>
              <b>→</b>
              <div>
                <span>修复后</span>
                <strong>
                  {percent(snapshot?.verification?.candidate_pass_rate)}
                </strong>
              </div>
            </div>
            <footer>{snapshot?.stop_reason || "等待回归门禁"}</footer>
          </article>
        </div>
      </div>
    </section>
  );
}
