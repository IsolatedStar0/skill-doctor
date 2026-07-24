export type Taxonomy =
  | "Skill Recall Failure"
  | "Selection Error"
  | "Loading Miss"
  | "Instruction Violation"
  | "Tool Misuse"
  | "Content Gap"
  | "Non-Skill Cause";

export type TraceStep = {
  id: string;
  at: string;
  kind: "skill" | "tool" | "decision" | "evaluation";
  title: string;
  detail: string;
  status: "ok" | "fault" | "downstream";
  evidence?: string;
};

export type DemoCase = {
  id: string;
  task: string;
  expected: string;
  actual: string;
  skill: {
    id: string;
    version: string;
    retrievalScore: number;
    loaded: boolean;
    before: string[];
  };
  trace: TraceStep[];
};

export type Diagnosis = {
  primaryFaultStep: string;
  faultChain: string[];
  taxonomy: Taxonomy;
  confidence: number;
  responsibility: number;
  mechanism: string;
  evidenceRefs: string[];
  action: "patch_skill" | "patch_routing" | "patch_loader" | "split_non_skill";
};

export type Patch = {
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

export type ValidationResult = {
  decision: "ADOPT" | "REJECT" | "NEEDS_REVIEW";
  originalReplay: { before: number; after: number };
  similarCases: { before: number; after: number; sampleSize: number };
  regression: { before: number; after: number; sampleSize: number };
  toolErrors: { before: number; after: number };
  reasons: string[];
};

export const demoCase: DemoCase = {
  id: "case-revenue-042",
  task: "读取 100 行订单 CSV，汇总总营收并输出 Markdown 报告。",
  expected: "¥428,650（基于全部 100 行订单）",
  actual: "¥82,410（错误地只统计预览的 20 行）",
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
      kind: "skill",
      title: "召回并加载 spreadsheet-summary@1.2.0",
      detail: "候选排名第 1，retrieval score 0.92；Skill 文件和依赖均加载成功。",
      status: "ok",
      evidence: "skill.loaded:spreadsheet-summary@1.2.0",
    },
    {
      id: "step-02",
      at: "00:01.1",
      kind: "tool",
      title: "预览订单数据",
      detail: "read_csv(path='orders.csv', limit=20) 返回 20/100 行。",
      status: "ok",
      evidence: "tool.call:read_csv#17",
    },
    {
      id: "step-03",
      at: "00:02.4",
      kind: "decision",
      title: "直接基于预览结果计算总营收",
      detail: "Agent 将 20 行预览误当作完整数据集；Skill 未要求在汇总前重新读取全部行。",
      status: "fault",
      evidence: "decision#23 + skill.procedure:3",
    },
    {
      id: "step-04",
      at: "00:03.7",
      kind: "tool",
      title: "生成报告",
      detail: "write_file(path='summary.md', revenue='¥82,410') 调用成功。",
      status: "downstream",
      evidence: "tool.call:write_file#24",
    },
    {
      id: "step-05",
      at: "00:04.2",
      kind: "evaluation",
      title: "Evaluator 判定失败",
      detail: "报告覆盖格式要求，但总营收与全量数据计算结果不一致。",
      status: "downstream",
      evidence: "evaluator:revenue_total_mismatch",
    },
  ],
};

export function diagnose(input: DemoCase): Diagnosis {
  const fault = input.trace.find((step) => step.status === "fault");
  if (!fault) throw new Error("Demo trace must contain one actionable fault step.");

  return {
    primaryFaultStep: fault.id,
    faultChain: input.trace
      .filter((step) => step.status !== "ok")
      .map((step) => step.id),
    taxonomy: "Content Gap",
    confidence: 0.91,
    responsibility: 0.86,
    mechanism:
      "Skill 的 procedure:3 缺少“汇总必须覆盖完整数据集”的前置条件和验证检查，导致 Agent 合理但错误地复用了预览结果。",
    evidenceRefs: [
      fault.evidence ?? fault.id,
      "tool.call:read_csv#17",
      "evaluator:revenue_total_mismatch",
    ],
    action: "patch_skill",
  };
}

export function proposePatch(input: DemoCase, diagnosis: Diagnosis): Patch {
  if (diagnosis.action !== "patch_skill") {
    throw new Error("Only skill-scoped diagnoses may produce a skill patch.");
  }
  const after = [...input.skill.before];
  after[2] =
    "重新读取完整数据集并计算关键统计值；断言 processed_rows == total_rows 后再生成摘要。";

  return {
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

export function validatePatch(
  input: DemoCase,
  diagnosis: Diagnosis,
  patch: Patch,
): ValidationResult {
  const scopeIsValid =
    diagnosis.action === "patch_skill" &&
    patch.targetSkill === input.skill.id &&
    patch.before.length === patch.after.length &&
    patch.before.filter((line, index) => line !== patch.after[index]).length === 1;

  const originalReplay = { before: 0, after: scopeIsValid ? 1 : 0 };
  const similarCases = { before: 0.5, after: scopeIsValid ? 1 : 0.5, sampleSize: 4 };
  const regression = { before: 1, after: scopeIsValid ? 1 : 0.75, sampleSize: 4 };
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

export function runDemo() {
  const diagnosis = diagnose(demoCase);
  const patch = proposePatch(demoCase, diagnosis);
  const validation = validatePatch(demoCase, diagnosis, patch);
  return { input: demoCase, diagnosis, patch, validation };
}
