import {
  evaluateAttribution,
  type RuleEvaluation,
} from "./attribution-engine.ts";

export type Taxonomy =
  | "Skill Recall Failure"
  | "Selection Error"
  | "Loading Miss"
  | "Instruction Violation"
  | "Tool Misuse"
  | "Content Gap"
  | "Non-Skill Cause";

export type TokenUsage = {
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number;
  reasoningTokens: number;
};

export type TraceStep = {
  id: string;
  at: string;
  durationMs: number;
  kind: "skill" | "tool" | "decision" | "evaluation";
  title: string;
  detail: string;
  status: "ok" | "fault" | "downstream";
  model: string;
  usage: TokenUsage;
  evidence?: string;
};

export type TokenUsageSummary = TokenUsage & {
  totalTokens: number;
  freshInputTokens: number;
  visibleOutputTokens: number;
  cacheHitRate: number;
  durationMs: number;
  hottestStepId: string;
  hottestStepTokens: number;
};

export type CaseSignals = {
  applicableSkillKnown: boolean;
  correctSkillInCandidates: boolean;
  correctSkillSelected: boolean;
  skillLoaded: boolean;
  toolSchemaValid: boolean;
  instructionFollowed: boolean;
  skillCoversRequirement: boolean;
  externalFailure: "permission" | "network" | "service" | null;
};

export type DemoCase = {
  id: string;
  name: string;
  summary: string;
  task: string;
  expected: string;
  actual: string;
  signals: CaseSignals;
  skill: {
    id: string;
    version: string;
    retrievalScore: number;
    loaded: boolean;
    before: string[];
  };
  trace: TraceStep[];
};

export type RepairAction =
  | "patch_skill"
  | "patch_routing"
  | "patch_loader"
  | "split_non_skill";

export type Diagnosis = {
  primaryFaultStep: string;
  faultChain: string[];
  taxonomy: Taxonomy;
  confidence: number;
  responsibility: number;
  mechanism: string;
  evidenceRefs: string[];
  action: RepairAction;
  ruleId: string;
  ruleVersion: string;
  ruleEvaluations: RuleEvaluation[];
};

export type SkillPatch = {
  kind: "skill_patch";
  patchId: string;
  targetSkill: string;
  baseVersion: string;
  nextVersion: string;
  scope: "procedure";
  evidenceRefs: string[];
  rollbackRef: string;
  before: string[];
  after: string[];
  changedLine: number;
};

export type RoutingAction = {
  kind: "routing_action";
  actionId: string;
  target: "loader" | "router" | "platform";
  title: string;
  detail: string;
  operations: string[];
  evidenceRefs: string[];
  mutationPolicy: "NO_SKILL_MUTATION";
};

export type ReviewAction = {
  kind: "review_action";
  actionId: string;
  target: "skill_author";
  title: string;
  detail: string;
  operations: string[];
  evidenceRefs: string[];
  mutationPolicy: "NO_SKILL_MUTATION";
};

export type RepairPlan = SkillPatch | RoutingAction | ReviewAction;

export type ValidationResult = {
  decision: "ADOPT" | "REJECT" | "NEEDS_REVIEW" | "ROUTE";
  originalReplay: { before: number; after: number };
  similarCases: { before: number; after: number; sampleSize: number };
  regression: { before: number; after: number; sampleSize: number };
  toolErrors: { before: number; after: number };
  reasons: string[];
};

const contentGapCase: DemoCase = {
  id: "case-revenue-042",
  name: "内容缺口",
  summary: "Skill 已正确召回、加载和执行，但 procedure 缺少全量读取约束。",
  task: "读取 100 行订单 CSV，汇总总营收并输出 Markdown 报告。",
  expected: "¥428,650（基于全部 100 行订单）",
  actual: "¥82,410（错误地只统计预览的 20 行）",
  signals: {
    applicableSkillKnown: true,
    correctSkillInCandidates: true,
    correctSkillSelected: true,
    skillLoaded: true,
    toolSchemaValid: true,
    instructionFollowed: true,
    skillCoversRequirement: false,
    externalFailure: null,
  },
  skill: {
    id: "spreadsheet-summary",
    version: "1.2.0",
    retrievalScore: 0.92,
    loaded: true,
    before: [
      "确认输入文件存在且可读。",
      "预览前 20 行，识别列名和数据类型。",
      "计算关键统计值并生成摘要。",
      "将结果写入 Markdown 报告。",
    ],
  },
  trace: [
    {
      id: "step-01",
      at: "00:00.3",
      durationMs: 410,
      kind: "skill",
      title: "召回并加载 spreadsheet-summary@1.2.0",
      detail: "候选排名第 1，retrieval score 0.92；Skill 文件和依赖均加载成功。",
      status: "ok",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 680,
        outputTokens: 42,
        cachedInputTokens: 420,
        reasoningTokens: 0,
      },
      evidence: "skill.loaded:spreadsheet-summary@1.2.0",
    },
    {
      id: "step-02",
      at: "00:01.1",
      durationMs: 790,
      kind: "tool",
      title: "预览订单数据",
      detail: "read_csv(path='orders.csv', limit=20) 返回 20/100 行。",
      status: "ok",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 920,
        outputTokens: 68,
        cachedInputTokens: 510,
        reasoningTokens: 18,
      },
      evidence: "tool.call:read_csv#17",
    },
    {
      id: "step-03",
      at: "00:02.4",
      durationMs: 1180,
      kind: "decision",
      title: "直接基于预览结果计算总营收",
      detail: "Agent 将 20 行预览误当作完整数据集；Skill 未要求在汇总前重新读取全部行。",
      status: "fault",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 1260,
        outputTokens: 214,
        cachedInputTokens: 600,
        reasoningTokens: 136,
      },
      evidence: "decision#23 + skill.procedure:3",
    },
    {
      id: "step-04",
      at: "00:03.7",
      durationMs: 860,
      kind: "tool",
      title: "生成报告",
      detail: "write_file(path='summary.md', revenue='¥82,410') 调用成功。",
      status: "downstream",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 1040,
        outputTokens: 166,
        cachedInputTokens: 720,
        reasoningTokens: 42,
      },
      evidence: "tool.call:write_file#24",
    },
    {
      id: "step-05",
      at: "00:04.2",
      durationMs: 430,
      kind: "evaluation",
      title: "Evaluator 判定失败",
      detail: "报告覆盖格式要求，但总营收与全量数据计算结果不一致。",
      status: "downstream",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 540,
        outputTokens: 92,
        cachedInputTokens: 360,
        reasoningTokens: 28,
      },
      evidence: "evaluator:revenue_total_mismatch",
    },
  ],
};

const loadingMissCase: DemoCase = {
  id: "case-loader-017",
  name: "加载遗漏",
  summary: "路由选择正确，但 Skill 的引用资源未进入上下文，不能归咎于 Skill 内容。",
  task: "按仓库规范生成发布检查清单，并包含安全回滚步骤。",
  expected: "依据 release-policy references 生成 8 项检查清单",
  actual: "只生成 4 项通用检查，缺少回滚和审批门禁",
  signals: {
    applicableSkillKnown: true,
    correctSkillInCandidates: true,
    correctSkillSelected: true,
    skillLoaded: false,
    toolSchemaValid: true,
    instructionFollowed: true,
    skillCoversRequirement: true,
    externalFailure: null,
  },
  skill: {
    id: "release-checklist",
    version: "2.0.1",
    retrievalScore: 0.89,
    loaded: false,
    before: [
      "读取 references/release-policy.md。",
      "提取发布前检查项与负责人。",
      "附加回滚、审批和观察窗口。",
      "输出带状态字段的 Markdown 清单。",
    ],
  },
  trace: [
    {
      id: "step-01",
      at: "00:00.2",
      durationMs: 360,
      kind: "skill",
      title: "选择 release-checklist@2.0.1",
      detail: "候选排名第 1，retrieval score 0.89；路由结果与任务匹配。",
      status: "ok",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 640,
        outputTokens: 38,
        cachedInputTokens: 380,
        reasoningTokens: 0,
      },
      evidence: "skill.selected:release-checklist@2.0.1",
    },
    {
      id: "step-02",
      at: "00:00.8",
      durationMs: 520,
      kind: "skill",
      title: "加载引用资源失败",
      detail: "SKILL.md 已进入上下文，但 references/release-policy.md 未被解析到加载集合。",
      status: "fault",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 720,
        outputTokens: 54,
        cachedInputTokens: 410,
        reasoningTokens: 12,
      },
      evidence: "loader.missing:references/release-policy.md",
    },
    {
      id: "step-03",
      at: "00:01.9",
      durationMs: 1080,
      kind: "decision",
      title: "退化为通用发布知识",
      detail: "Agent 无法读取仓库专属门禁，只能生成通用检查清单。",
      status: "downstream",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 1190,
        outputTokens: 202,
        cachedInputTokens: 470,
        reasoningTokens: 128,
      },
      evidence: "context.absent:release-policy",
    },
    {
      id: "step-04",
      at: "00:03.1",
      durationMs: 390,
      kind: "evaluation",
      title: "Evaluator 判定覆盖不足",
      detail: "回滚、审批、观察窗口和责任人四项关键要求缺失。",
      status: "downstream",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 510,
        outputTokens: 76,
        cachedInputTokens: 300,
        reasoningTokens: 22,
      },
      evidence: "evaluator:required_sections_missing",
    },
  ],
};

const nonSkillCase: DemoCase = {
  id: "case-platform-009",
  name: "平台异常",
  summary: "Skill 与执行步骤均正确，失败来自外部服务权限，系统应拒绝修改 Skill。",
  task: "把验证通过的候选 Skill 发布到远端注册表。",
  expected: "注册表返回 release id，候选版本进入 staged 状态",
  actual: "registry.publish 返回 403 insufficient_scope",
  signals: {
    applicableSkillKnown: true,
    correctSkillInCandidates: true,
    correctSkillSelected: true,
    skillLoaded: true,
    toolSchemaValid: true,
    instructionFollowed: true,
    skillCoversRequirement: true,
    externalFailure: "permission",
  },
  skill: {
    id: "skill-release",
    version: "1.4.3",
    retrievalScore: 0.95,
    loaded: true,
    before: [
      "确认候选版本已通过回放与回归门禁。",
      "使用最小权限凭据调用 registry.publish。",
      "记录 release id 和版本状态。",
      "发布失败时保留候选版本并输出错误证据。",
    ],
  },
  trace: [
    {
      id: "step-01",
      at: "00:00.2",
      durationMs: 330,
      kind: "skill",
      title: "加载 skill-release@1.4.3",
      detail: "Skill、发布清单和工具 schema 均加载成功。",
      status: "ok",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 610,
        outputTokens: 40,
        cachedInputTokens: 360,
        reasoningTokens: 0,
      },
      evidence: "skill.loaded:skill-release@1.4.3",
    },
    {
      id: "step-02",
      at: "00:01.0",
      durationMs: 670,
      kind: "decision",
      title: "通过发布前门禁",
      detail: "原案例、同类案例和回归集全部满足发布阈值。",
      status: "ok",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 880,
        outputTokens: 96,
        cachedInputTokens: 550,
        reasoningTokens: 28,
      },
      evidence: "gate.pass:candidate-1.4.4",
    },
    {
      id: "step-03",
      at: "00:01.6",
      durationMs: 920,
      kind: "tool",
      title: "注册表拒绝发布请求",
      detail: "registry.publish 参数符合 schema，但服务返回 403 insufficient_scope。",
      status: "fault",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 1030,
        outputTokens: 128,
        cachedInputTokens: 660,
        reasoningTokens: 34,
      },
      evidence: "tool.error:registry.publish#403",
    },
    {
      id: "step-04",
      at: "00:02.0",
      durationMs: 250,
      kind: "evaluation",
      title: "候选版本保持未发布",
      detail: "系统保留候选版本和失败证据，没有改变线上 Skill。",
      status: "downstream",
      model: "gpt-5-mini",
      usage: {
        inputTokens: 410,
        outputTokens: 62,
        cachedInputTokens: 240,
        reasoningTokens: 16,
      },
      evidence: "release.status:held",
    },
  ],
};

export const demoCases: DemoCase[] = [
  contentGapCase,
  loadingMissCase,
  nonSkillCase,
];

export const demoCase = contentGapCase;

export function stepTokenTotal(step: TraceStep) {
  return step.usage.inputTokens + step.usage.outputTokens;
}

export function summarizeTokenUsage(
  trace: TraceStep[],
): TokenUsageSummary {
  const totals = trace.reduce(
    (current, step) => ({
      inputTokens: current.inputTokens + step.usage.inputTokens,
      outputTokens: current.outputTokens + step.usage.outputTokens,
      cachedInputTokens:
        current.cachedInputTokens + step.usage.cachedInputTokens,
      reasoningTokens:
        current.reasoningTokens + step.usage.reasoningTokens,
      durationMs: current.durationMs + step.durationMs,
    }),
    {
      inputTokens: 0,
      outputTokens: 0,
      cachedInputTokens: 0,
      reasoningTokens: 0,
      durationMs: 0,
    },
  );
  const hottestStep = trace.reduce<TraceStep | null>(
    (current, step) =>
      !current || stepTokenTotal(step) > stepTokenTotal(current)
        ? step
        : current,
    null,
  );

  return {
    ...totals,
    totalTokens: totals.inputTokens + totals.outputTokens,
    freshInputTokens: totals.inputTokens - totals.cachedInputTokens,
    visibleOutputTokens: totals.outputTokens - totals.reasoningTokens,
    cacheHitRate:
      totals.inputTokens === 0
        ? 0
        : totals.cachedInputTokens / totals.inputTokens,
    hottestStepId: hottestStep?.id ?? "",
    hottestStepTokens: hottestStep ? stepTokenTotal(hottestStep) : 0,
  };
}

function evidenceFor(input: DemoCase, fault: TraceStep) {
  return Array.from(
    new Set(
      [fault, ...input.trace.filter((step) => step.status === "downstream")]
        .map((step) => step.evidence ?? step.id)
        .slice(0, 3),
    ),
  );
}

export function diagnose(input: DemoCase): Diagnosis {
  const fault = input.trace.find((step) => step.status === "fault");
  if (!fault) throw new Error("Demo trace must contain one actionable fault step.");

  const attribution = evaluateAttribution(input);
  return {
    primaryFaultStep: fault.id,
    faultChain: input.trace
      .filter((step) => step.status !== "ok")
      .map((step) => step.id),
    evidenceRefs: evidenceFor(input, fault),
    ...attribution,
  };
}

export function proposePatch(input: DemoCase, diagnosis: Diagnosis): SkillPatch {
  if (diagnosis.action !== "patch_skill") {
    throw new Error("Only skill-scoped diagnoses may produce a skill patch.");
  }
  if (
    diagnosis.taxonomy !== "Content Gap" ||
    input.skill.id !== "spreadsheet-summary"
  ) {
    throw new Error(
      `No verified patch strategy for ${diagnosis.taxonomy} on ${input.skill.id}.`,
    );
  }
  const after = [...input.skill.before];
  after[2] =
    "重新读取完整数据集并计算关键统计值；断言 processed_rows == total_rows 后再生成摘要。";

  return {
    kind: "skill_patch",
    patchId: "patch-spreadsheet-summary-013",
    targetSkill: input.skill.id,
    baseVersion: input.skill.version,
    nextVersion: "1.2.1-candidate",
    scope: "procedure",
    evidenceRefs: diagnosis.evidenceRefs,
    rollbackRef: `${input.skill.id}@${input.skill.version}`,
    before: input.skill.before,
    after,
    changedLine: 3,
  };
}

export function proposeRepair(
  input: DemoCase,
  diagnosis: Diagnosis,
): RepairPlan {
  if (diagnosis.action === "patch_skill") {
    if (
      diagnosis.taxonomy === "Content Gap" &&
      input.skill.id === "spreadsheet-summary"
    ) {
      return proposePatch(input, diagnosis);
    }
    return {
      kind: "review_action",
      actionId: `review-${diagnosis.ruleId.toLowerCase()}`,
      target: "skill_author",
      title: "缺少已验证的自动修复策略",
      detail:
        "归因证据支持 Skill 责任，但当前策略注册表没有适用于该分类与 Skill 的安全补丁模板；保持内容冻结并请求作者审查。",
      operations: [
        "保留故障 Trace、规则证明与候选 Skill 版本",
        "由 Skill 作者确认目标 procedure 和预期不变量",
        "注册专用 patch strategy 后重新运行回放与回归",
      ],
      evidenceRefs: diagnosis.evidenceRefs,
      mutationPolicy: "NO_SKILL_MUTATION",
    };
  }

  if (diagnosis.action === "patch_loader") {
    return {
      kind: "routing_action",
      actionId: "route-loader-017",
      target: "loader",
      title: "隔离候选版本并重新加载依赖",
      detail:
        "修复 Skill resource manifest 的解析与完整性检查；依赖未齐全时阻断执行，避免退化为无证据的通用回答。",
      operations: [
        "校验 SKILL.md 中的相对资源引用",
        "缺失资源时将运行标记为 load_incomplete",
        "完整加载后重放原始失败案例",
      ],
      evidenceRefs: diagnosis.evidenceRefs,
      mutationPolicy: "NO_SKILL_MUTATION",
    };
  }

  if (diagnosis.action === "patch_routing") {
    return {
      kind: "routing_action",
      actionId: "route-retriever-001",
      target: "router",
      title: "回放召回与排序阶段",
      detail: "调整候选检索或排序配置，并保持 Skill 内容冻结。",
      operations: [
        "保存候选集合与排序分数",
        "修复召回或选择规则",
        "在固定候选集上重放",
      ],
      evidenceRefs: diagnosis.evidenceRefs,
      mutationPolicy: "NO_SKILL_MUTATION",
    };
  }

  return {
    kind: "routing_action",
    actionId: "route-platform-009",
    target: "platform",
    title: "转交平台权限处置",
    detail:
      "保留候选 Skill 与发布证据，向平台层上报权限故障；凭据恢复后重试同一不可变发布请求。",
    operations: [
      "记录 403、request id 与凭据 scope",
      "告警并转交 registry 权限负责人",
      "权限恢复后重试，不生成 Skill diff",
    ],
    evidenceRefs: diagnosis.evidenceRefs,
    mutationPolicy: "NO_SKILL_MUTATION",
  };
}

export function validatePatch(
  input: DemoCase,
  diagnosis: Diagnosis,
  patch: SkillPatch,
): ValidationResult {
  const scopeIsValid =
    diagnosis.action === "patch_skill" &&
    patch.targetSkill === input.skill.id &&
    patch.before.length === patch.after.length &&
    patch.before.filter((line, index) => line !== patch.after[index]).length === 1;

  const originalReplay = { before: 0, after: scopeIsValid ? 1 : 0 };
  const similarCases = {
    before: 0.5,
    after: scopeIsValid ? 1 : 0.5,
    sampleSize: 4,
  };
  const regression = {
    before: 1,
    after: scopeIsValid ? 1 : 0.75,
    sampleSize: 4,
  };
  const toolErrors = { before: 1, after: scopeIsValid ? 0 : 1 };
  const adopted =
    originalReplay.after === 1 &&
    similarCases.after > similarCases.before &&
    regression.after >= regression.before &&
    scopeIsValid;

  return {
    decision: adopted ? "ADOPT" : "REJECT",
    originalReplay,
    similarCases,
    regression,
    toolErrors,
    reasons: adopted
      ? [
          "原失败案例已修复",
          "4 个同类案例通过率由 50% 提升到 100%",
          "4 个历史成功案例无回归",
          "变更仅触及 procedure 第 3 行",
        ]
      : ["候选 patch 未通过最小采纳门禁"],
  };
}

export function validateRepair(
  input: DemoCase,
  diagnosis: Diagnosis,
  repair: RepairPlan,
): ValidationResult {
  if (repair.kind === "skill_patch") {
    return validatePatch(input, diagnosis, repair);
  }

  if (repair.kind === "review_action") {
    return {
      decision: "NEEDS_REVIEW",
      originalReplay: { before: 0, after: 0 },
      similarCases: { before: 0.5, after: 0.5, sampleSize: 4 },
      regression: { before: 1, after: 1, sampleSize: 4 },
      toolErrors: { before: 1, after: 1 },
      reasons: [
        "归因支持 Skill 责任，但无已验证的 patch strategy",
        "候选 Skill 内容保持冻结",
        "需要作者确认目标 procedure 与修复不变量",
        "注册策略后必须重新通过回放和回归门禁",
      ],
    };
  }

  const safelyRouted =
    repair.mutationPolicy === "NO_SKILL_MUTATION" &&
    diagnosis.action !== "patch_skill";

  return {
    decision: safelyRouted ? "ROUTE" : "REJECT",
    originalReplay: { before: 0, after: 0 },
    similarCases: { before: 0.5, after: 0.5, sampleSize: 4 },
    regression: { before: 1, after: 1, sampleSize: 4 },
    toolErrors: { before: 1, after: 1 },
    reasons: safelyRouted
      ? [
          `故障已路由到 ${repair.target}`,
          "Skill 内容保持冻结",
          "证据与重试条件已保留",
          "避免把非内容故障写回 Skill",
        ]
      : ["路由动作违反 Skill 变更隔离策略"],
  };
}

export function analyzeCase(input: DemoCase) {
  const diagnosis = diagnose(input);
  const repair = proposeRepair(input, diagnosis);
  const validation = validateRepair(input, diagnosis, repair);
  const usage = summarizeTokenUsage(input.trace);
  return { input, diagnosis, repair, validation, usage };
}

export function runDemo(caseId = demoCase.id) {
  const input = demoCases.find((item) => item.id === caseId);
  if (!input) throw new Error(`Unknown demo case: ${caseId}`);
  return analyzeCase(input);
}
