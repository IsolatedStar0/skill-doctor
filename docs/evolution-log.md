# Evolution Log

## Iteration 1 / 8 — Versioned Trace Adapter

- Objective: 让诊断闭环接收真实文件输入，而不是只能运行内置对象。
- Observed gap: `demo-engine.ts` 仅暴露固定 `DemoCase`，缺少输入协议和边界校验。
- Taxonomy: Non-Skill Cause（系统能力缺口，不修改任何案例 Skill）。
- Primary fault: Trace ingestion boundary。
- Evidence: 没有 Trace schema、文件 adapter 或 CLI 入口。
- Skill responsibility: 0.00。
- Confidence: 0.99。
- Hypothesis: 增加版本化 JSON 协议、运行时校验和 CLI 后，外部 Trace 可复用同一归因与验证链。
- Files changed: `lib/trace-adapter.ts`、`scripts/analyze-trace.mjs`、JSON fixture、adapter tests、package scripts、README。
- Tests: adapter 定向测试 4/4；全量测试 11/11。
- Build: production build passed。
- Replay: 外部 fixture 稳定得到 `Content Gap → skill_patch → ADOPT`。
- Regression: 原 7 项测试全部通过。
- Decision: ADOPT。
- Remaining risks: 归因规则仍是无版本的条件分支，命中过程不可解释。
- Next candidate: 提取 Attribution Engine，并输出规则版本、优先级和排除证据。

## Iteration 2 / 8 — Explainable Attribution Engine

- Objective: 让每次归因能够回答“哪条规则、哪个版本、为什么优先命中”。
- Observed gap: `diagnose()` 使用条件分支直接返回分类，缺少规则身份、优先级和排除过程。
- Taxonomy: Non-Skill Cause（诊断基础设施缺口）。
- Primary fault: Attribution decision boundary。
- Evidence: 诊断对象只包含分类和置信度，没有 rule proof。
- Skill responsibility: 0.00。
- Confidence: 0.99。
- Hypothesis: 把 7 类规则提取为版本化表并记录所有规则评估，可稳定复现分类优先级。
- Files changed: `lib/attribution-engine.ts`、diagnosis model、归因界面、规则测试、README。
- Tests: 7 类规则、冲突优先级和规则目录测试通过；全量 20/20。
- Build: production build passed。
- Replay: 三个内置案例和外部 JSON fixture 结果不变。
- Regression: Iteration 1 的 11 项测试全部通过。
- Decision: ADOPT。
- Remaining risks: 自动 patch 模板仍默认使用 CSV 修复，可能错误处理其他 `patch_skill` 分类。
- Next candidate: 增加 patch strategy 适配门禁；没有策略时转为 `NEEDS_REVIEW`。

## Iteration 3 / 8 — Patch Strategy Isolation

- Objective: 阻止归因正确但修复模板不匹配时产生错误 Skill patch。
- Observed gap: 所有 `patch_skill` 动作都会套用 `spreadsheet-summary` 的第 3 行修复。
- Taxonomy: Content Gap（patch planner 自身的策略覆盖缺口）。
- Primary fault: Repair strategy selection。
- Evidence: `proposePatch()` 没有检查 taxonomy 与 target skill。
- Skill responsibility: 0.00（平台修复器责任）。
- Confidence: 1.00。
- Hypothesis: 使用 taxonomy + skill 双重策略门禁，未注册策略进入人工审查，可消除错误写回。
- Files changed: repair plan union、strategy gate、review validation、7 类 regression manifest、repair tests、README。
- Tests: 定向测试 3/3；全量 23/23。
- Build: production build passed。
- Replay: Content Gap 仍为 `ADOPT`；路由类仍为 `ROUTE`。
- Regression: Tool Misuse 与 Instruction Violation 稳定进入 `NEEDS_REVIEW`，0 条 Skill mutation。
- Decision: ADOPT。
- Remaining risks: 外部 Trace 仍直接提供因果 signals，存在调用方自报分类的可信性问题。
- Next candidate: 从结构化 routing/loading/execution observations 自动提取 signals。

## Iteration 4 / 8 — Evidence-Derived Signals

- Objective: 让归因输入来自运行观测，而不是由调用方直接声明分类信号。
- Observed gap: Trace 1.0 的 `signals` 字段可以直接控制归因结果。
- Taxonomy: Non-Skill Cause（Trace ingestion trust boundary）。
- Primary fault: Signal extraction boundary。
- Evidence: 修改 `signals.externalFailure` 即可在无外部错误证据时触发 `Non-Skill Cause`。
- Skill responsibility: 0.00。
- Confidence: 1.00。
- Hypothesis: 从 routing、loading、execution checks 和 external errors 派生 signals，可把分类约束到可观测事实。
- Files changed: Trace 1.1 observations schema、signal extractor、fixture、adapter tests、README。
- Tests: adapter 定向测试 6/6；全量 25/25。
- Build: production build passed。
- Replay: 外部 fixture 由观测稳定推导 `ATTR-060 / Content Gap / ADOPT`。
- Regression: Trace 1.0 保持兼容；Trace 1.1 中伪造的额外 `signals` 被忽略。
- Decision: ADOPT。
- Remaining risks: 尚未连接真实 Agent SDK/OpenTelemetry collector；当前通过 JSON 文件边界接入。
- Next candidate: 接入具体 Agent runtime 的 Trace exporter。

## Loop Completion

- Completed iterations: 4 / 8。
- Stop reason: 当前版本已满足本轮全部验收条件；继续扩张需要选择具体 Agent runtime，属于新的集成范围。
- Build: passed。
- Tests: 25 passed, 0 failed。
- Determinism: bundled cases、7-class manifest 和外部 fixture 均通过重复执行验证。
- Safety boundary: `ROUTE`、`NEEDS_REVIEW` 与 `NO_SKILL_MUTATION` 均有自动化门禁。
- Recommended next phase: 选择 LangGraph、OpenAI Agents SDK 或自研 runtime，实现第一个真实 Trace exporter。

## Iteration 5 / 8 — Trace Flow and Token Accounting

- Objective: 将 Agent 的执行过程和 Token 消耗变成可交互、可核对的可视化面板。
- Observed gap: 原 Trace 页面只有事件列表，Trace schema 没有模型、耗时和 Token usage。
- Taxonomy: Non-Skill Cause（观测基础设施缺口）。
- Primary fault: Trace telemetry model。
- Evidence: `TraceStep` 只能记录事件文本和状态，无法回答每步成本与消耗热点。
- Skill responsibility: 0.00。
- Confidence: 1.00。
- Hypothesis: 在 Trace 1.1 事件上记录 duration、model、input/output/cache/reasoning token，并从同一数据源生成流程图和消耗图，可避免展示与诊断数据分叉。
- Files changed: Token usage model、Trace fixture、协议校验、Trace 流程图、Token dashboard、usage tests、README。
- Tests: Token 定向测试 5/5；全量 30/30。
- Build: production build passed。
- Replay: 三个内置场景都能生成逐步 Token 分解与累计曲线。
- Regression: 原 25 项测试全部通过。
- Decision: ADOPT。
- Remaining risks: 当前 Token 数据来自确定性 fixture；接入真实 runtime 后需要把供应商 usage 字段归一化到 Trace 1.1。
- Next candidate: 实现 OpenAI Agents SDK 或 LangGraph usage exporter。

## Iteration 6 / 8 — Runtime Recorder and Trace Import

- Objective: 打通真实 Agent 运行、Trace 1.1 文件和可视化控制台。
- Observed gap: 面板只能展示内置案例，Agent runtime 没有统一的采集 API。
- Taxonomy: Non-Skill Cause（integration boundary）。
- Primary fault: Runtime-to-observability handoff。
- Evidence: 用户无法在不改 UI 代码的情况下查看一次新 Agent 运行。
- Skill responsibility: 0.00。
- Confidence: 1.00。
- Hypothesis: provider-neutral recorder + 浏览器 JSON import 可以先稳定接口，再分别适配具体 Agent SDK。
- Files changed: `trace-recorder.ts`、插桩示例、导入交互、Recorder tests、README。
- Tests: Recorder 定向测试 3/3；全量 33/33。
- Build: production build passed。
- Replay: Recorder 输出可通过 Trace 1.1 校验并得到 `Content Gap` 与 Token 汇总。
- Regression: 原 30 项测试全部通过。
- Decision: ADOPT。
- Remaining risks: 暂无实时流式更新；当前在 Agent 运行结束后导入完整 Trace。
- Next candidate: SSE/WebSocket 实时事件流，或先实现 OpenAI Agents SDK 专用 exporter。

## Iteration 7 / 8 — Codex JSONL Adapter and Open Skill Smoke Suite

- Objective: 连接真实 Codex 非交互事件协议，并用公开 Skill 数据验证接入边界。
- Observed gap: provider-neutral Recorder 仍要求业务代码主动插桩，不能直接读取 Codex CLI JSONL；项目也没有外部 Skill 数据基线。
- Taxonomy: Non-Skill Cause（runtime integration 与 evaluation infrastructure 缺口）。
- Primary fault: Codex-to-Trace handoff。
- Evidence: `codex exec --json` 只在 `turn.completed` 给出整轮 token usage；原 Trace Adapter 不认识 `item.completed`、command、MCP、file change 等 Codex 事件。
- Skill responsibility: 0.00。
- Confidence: 1.00。
- Hypothesis: 专用 JSONL Adapter + 开源 Skill smoke suite 可以先固定事件、token 和数据集边界，再安全扩展到完整模型对照实验。
- Files changed: `codex-jsonl-adapter.ts`、Codex fixture/context、import CLI、SWE-Skills-Bench manifest、fetch/smoke runner、adapter tests、README。
- Tests: Codex Adapter 定向测试 4/4；三个开源 Skill 的 frontmatter、文档/任务长度、仓库 URL、pytest assertions 和 Python verifier 语法门禁 3/3。
- Token accounting: item step 不伪造 token；`turn.completed.usage` 原样进入 `codex-turn-summary`，fixture 验证 total=24,885、fresh input=315。
- Live probe: `tdd-workflow` 已尝试通过 `.agents/skills/tdd-workflow/SKILL.md + codex exec --json` 运行；当前 Codex Desktop 沙箱返回 `spawnSync codex EPERM`，报告为 `blocked`，未伪造模型结果。
- Replay: 官方事件形状 fixture 可稳定转换为 4 个 Trace step，并定位 `item_2` 为 `Content Gap / ATTR-060`。
- Decision: ADOPT（Adapter 与离线 benchmark 门禁）；live 模型效果保持未决。
- Remaining risks: 真实模型 run 尚未在当前桌面沙箱完成；完整 SWE-Skills-Bench 还需要目标仓库、Docker 和 with/without-skill 配对运行。
- Next candidate: 在普通终端或 CI 运行 live probe，随后实现 paired runner、pass-rate delta、token delta 和失败 Trace 自动导入。

## Iteration 8 / 8 — Codex SDK Paired Evaluation and Evidence Snapshot

- Objective: 对同一任务真实运行 without-Skill control 与 with-Skill treatment，并统一比较质量、Token、耗时和回归。
- Observed gap: CLI runner 在 Codex Desktop 沙箱内无法启动 WindowsApps `codex.exe`，静态 smoke test 也不能证明 Skill 对模型行为有收益。
- Taxonomy: Non-Skill Cause（executor 与 evaluation harness 缺口）。
- Primary fault: paired execution boundary。
- Evidence: 官方 `@openai/codex-sdk@0.145.0` 可调用包内固定 Codex runtime，成功使用本机登录态返回结构化响应和 usage。
- Skill responsibility: 0.00。
- Confidence: 1.00。
- Implementation:
  - `benchmark-engine.ts` 计算 pass-rate delta、token overhead、duration delta 和 assertion-level regression。
  - paired runner 为每个 condition 创建隔离 Git 工作区，treatment 只额外安装 `.agents/skills/<id>/SKILL.md`。
  - SDK `runStreamed()` 事件保存为 JSONL；结构化回答交给真实 pytest verifier。
  - 每次运行保存 Codex JSONL、pytest 输出、git diff 与带内容哈希的 Evidence Snapshot。
  - `--resume` 会复用已完成 condition，避免网络中断后重复消耗 Token。
- Live results:
  - TDD Workflow: 25% → 100%，+75 pp；Token +126.2%；回归 0%。
  - Python Observability: 60% → 80%，+20 pp；Token +118.7%；回归 0%。
  - Distributed Tracing: 100% → 100%，0 pp；Token +117.6%；回归 0%。
  - 平均 pass-rate delta +31.7 pp；平均 token overhead +120.8%；平均耗时 +12.2s；回归率 0%。
- UI: 新增“配对评测”页面，展示真实报告、逐 Skill 对照、收益检查项和成本。
- Tests: 配对指标定向测试 3/3；全量生产构建与 40/40 tests passed。
- Decision: ADOPT。
- Interpretation: Skill 对 TDD 程序性知识帮助显著，对已被任务描述充分覆盖的 distributed tracing 没有质量收益，却仍带来约 118% Token 开销；因此 Skill router 需要同时优化准确率与成本，而不是无条件加载。
- Remaining risks: 当前是只读知识/计划 probe；尚未测量真实代码修改后的仓库测试通过率和行为回归。
- Next candidate: 使用固定 commit + Docker image 运行 SWE-Skills-Bench 的 repository-level pytest，并把失败 pair 自动送入现有归因/修复闭环。
