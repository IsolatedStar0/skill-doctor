import type {
  DemoCase,
  RepairAction,
  Taxonomy,
} from "./demo-engine.ts";

export const ATTRIBUTION_RULESET_VERSION = "2026-07-25.1";

export type RuleEvaluation = {
  ruleId: string;
  priority: number;
  taxonomy: Taxonomy;
  matched: boolean;
  selected: boolean;
  reason: string;
};

export type AttributionFinding = {
  taxonomy: Taxonomy;
  confidence: number;
  responsibility: number;
  mechanism: string;
  action: RepairAction;
  ruleId: string;
  ruleVersion: string;
  ruleEvaluations: RuleEvaluation[];
};

type AttributionRule = Omit<
  AttributionFinding,
  "ruleVersion" | "ruleEvaluations"
> & {
  priority: number;
  when: (input: DemoCase) => boolean;
  explain: (input: DemoCase) => string;
};

const rules: AttributionRule[] = [
  {
    ruleId: "ATTR-001",
    priority: 10,
    taxonomy: "Non-Skill Cause",
    confidence: 0.98,
    responsibility: 0.02,
    action: "split_non_skill",
    when: (input) => input.signals.externalFailure !== null,
    explain: (input) =>
      `externalFailure=${input.signals.externalFailure ?? "null"}`,
    mechanism:
      "Skill 已正确加载并被完整执行，工具参数也符合 schema；失败由外部运行条件触发，修改 Skill 不会消除根因。",
  },
  {
    ruleId: "ATTR-010",
    priority: 20,
    taxonomy: "Skill Recall Failure",
    confidence: 0.94,
    responsibility: 0.61,
    action: "patch_routing",
    when: (input) =>
      !input.signals.applicableSkillKnown ||
      !input.signals.correctSkillInCandidates,
    explain: (input) =>
      `applicableSkillKnown=${input.signals.applicableSkillKnown}, correctSkillInCandidates=${input.signals.correctSkillInCandidates}`,
    mechanism:
      "适用 Skill 没有进入候选集合，最早可行动故障发生在召回阶段。",
  },
  {
    ruleId: "ATTR-020",
    priority: 30,
    taxonomy: "Selection Error",
    confidence: 0.92,
    responsibility: 0.56,
    action: "patch_routing",
    when: (input) => !input.signals.correctSkillSelected,
    explain: (input) =>
      `correctSkillSelected=${input.signals.correctSkillSelected}`,
    mechanism:
      "适用 Skill 已被召回，但排序器选择了不匹配的候选项。",
  },
  {
    ruleId: "ATTR-030",
    priority: 40,
    taxonomy: "Loading Miss",
    confidence: 0.97,
    responsibility: 0.08,
    action: "patch_loader",
    when: (input) => !input.signals.skillLoaded,
    explain: (input) => `skillLoaded=${input.signals.skillLoaded}`,
    mechanism:
      "路由选择了正确 Skill，但引用资源未进入运行时上下文；应修复加载器并重放，而不是改写 Skill 指令。",
  },
  {
    ruleId: "ATTR-040",
    priority: 50,
    taxonomy: "Tool Misuse",
    confidence: 0.9,
    responsibility: 0.62,
    action: "patch_skill",
    when: (input) => !input.signals.toolSchemaValid,
    explain: (input) =>
      `toolSchemaValid=${input.signals.toolSchemaValid}`,
    mechanism:
      "工具调用不符合 schema，失败发生在参数构造或工具使用约束。",
  },
  {
    ruleId: "ATTR-050",
    priority: 60,
    taxonomy: "Instruction Violation",
    confidence: 0.87,
    responsibility: 0.72,
    action: "patch_skill",
    when: (input) => !input.signals.instructionFollowed,
    explain: (input) =>
      `instructionFollowed=${input.signals.instructionFollowed}`,
    mechanism:
      "Skill 指令覆盖了正确流程，但 Agent 没有遵循已加载的关键步骤。",
  },
  {
    ruleId: "ATTR-060",
    priority: 70,
    taxonomy: "Content Gap",
    confidence: 0.91,
    responsibility: 0.86,
    action: "patch_skill",
    when: (input) => !input.signals.skillCoversRequirement,
    explain: (input) =>
      `skillCoversRequirement=${input.signals.skillCoversRequirement}`,
    mechanism:
      "Skill 缺少满足任务要求的前置条件或验证检查，导致 Agent 合理但错误地执行了不完整流程。",
  },
];

export function evaluateAttribution(input: DemoCase): AttributionFinding {
  const orderedRules = [...rules].sort(
    (left, right) => left.priority - right.priority,
  );
  const matchedRule = orderedRules.find((rule) => rule.when(input));

  if (!matchedRule) {
    throw new Error("No repairable failure signal was found.");
  }

  const ruleEvaluations = orderedRules.map((rule) => ({
    ruleId: rule.ruleId,
    priority: rule.priority,
    taxonomy: rule.taxonomy,
    matched: rule.when(input),
    selected: rule.ruleId === matchedRule.ruleId,
    reason: rule.explain(input),
  }));

  return {
    taxonomy: matchedRule.taxonomy,
    confidence: matchedRule.confidence,
    responsibility: matchedRule.responsibility,
    mechanism: matchedRule.mechanism,
    action: matchedRule.action,
    ruleId: matchedRule.ruleId,
    ruleVersion: ATTRIBUTION_RULESET_VERSION,
    ruleEvaluations,
  };
}

export function listAttributionRules() {
  return rules
    .map(({ ruleId, priority, taxonomy, action }) => ({
      ruleId,
      priority,
      taxonomy,
      action,
    }))
    .sort((left, right) => left.priority - right.priority);
}
