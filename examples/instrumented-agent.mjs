import { TraceRecorder, normalizeTokenUsage } from "../lib/trace-recorder.ts";
import { writeFile } from "node:fs/promises";

let clock = 1000;
const now = () => clock;
const tick = (milliseconds) => {
  clock += milliseconds;
};

const recorder = new TraceRecorder({
  session: {
    id: "live-agent-001",
    name: "Recorder 接入示例",
    summary: "通过 provider-neutral Trace Recorder 采集的真实运行结构。",
    task: "汇总订单并生成报告。",
    expected: "读取全部订单后计算总营收。",
    actual: "只读取预览数据，营收偏低。",
  },
  skill: {
    id: "spreadsheet-summary",
    version: "1.2.0",
    retrievalScore: 0.93,
    loaded: true,
    before: [
      "确认输入文件存在且可读。",
      "预览前 20 行，识别列名和数据类型。",
      "计算关键统计值并生成摘要。",
      "将结果写入 Markdown 报告。",
    ],
  },
  now,
});

recorder
  .observeRouting({
    applicableSkillId: "spreadsheet-summary",
    candidateSkillIds: ["spreadsheet-summary", "generic-report"],
    selectedSkillId: "spreadsheet-summary",
  })
  .observeLoading({
    loadedSkillIds: ["spreadsheet-summary"],
    missingResources: [],
  })
  .addExecutionCheck("toolSchemaChecks", {
    id: "read_csv#1",
    passed: true,
  })
  .addExecutionCheck("instructionChecks", {
    id: "procedure-followed",
    passed: true,
  })
  .addExecutionCheck("requirementChecks", {
    id: "all-rows-covered",
    passed: false,
  });

const load = recorder.startStep({
  id: "step-01",
  kind: "skill",
  title: "加载 spreadsheet-summary",
  model: "gpt-5-mini",
});
tick(320);
load.finish({
  detail: "Skill 排名第一并成功加载。",
  status: "ok",
  usage: normalizeTokenUsage({
    input_tokens: 620,
    output_tokens: 34,
    input_tokens_details: { cached_tokens: 400 },
  }),
  evidence: "skill.loaded:spreadsheet-summary@1.2.0",
});

const decision = recorder.startStep({
  id: "step-02",
  kind: "decision",
  title: "使用预览数据计算总营收",
  model: "gpt-5-mini",
});
tick(890);
decision.finish({
  detail: "Agent 没有重新读取全量数据。",
  status: "fault",
  usage: normalizeTokenUsage({
    input_tokens: 1180,
    output_tokens: 196,
    input_tokens_details: { cached_tokens: 540 },
    output_tokens_details: { reasoning_tokens: 118 },
  }),
  evidence: "decision#2 + skill.procedure:3",
});

const evaluation = recorder.startStep({
  id: "step-03",
  kind: "evaluation",
  title: "Evaluator 检测到总额不一致",
  model: "gpt-5-mini",
});
tick(260);
evaluation.finish({
  detail: "输出格式正确，但只统计了预览行。",
  status: "downstream",
  usage: normalizeTokenUsage({
    input_tokens: 420,
    output_tokens: 64,
    cache_read_input_tokens: 260,
  }),
  evidence: "evaluator:revenue_total_mismatch",
});

recorder.validate();
const serialized = `${JSON.stringify(recorder.toTraceSession(), null, 2)}\n`;
const outputPath = process.argv[2];
if (outputPath) {
  await writeFile(outputPath, serialized, "utf8");
  process.stdout.write(`Trace written to ${outputPath}\n`);
} else {
  process.stdout.write(serialized);
}
