# Skill Doctor — Agent Skill 自愈诊断与修复系统

[![CI](https://github.com/IsolatedStar0/skill-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/IsolatedStar0/skill-doctor/actions/workflows/ci.yml)

Skill Doctor 是一个面向 **Agent Skill 可靠性** 的自愈系统 Demo：它把失败 Trace 转换成结构化证据，判断失败到底来自 Skill 内容缺口、加载遗漏还是平台/工具边界，再生成候选修复并通过回归验证决定是否采纳。

这个项目的目标不是简单“自动改 prompt”，而是展示一个更接近真实 Agent 工程化场景的闭环：**可观测、可归因、可修复、可验证、可量化**。

## 项目亮点

- **多阶段 Agent Workflow**：基于 LangGraph 将失败处理拆成 Trace Ingest、Evidence Builder、Attribution Agent、Repair Planner、Candidate Generator、Regression Verifier、Reject Memory 七个阶段。
- **安全修复边界**：只有高置信度 Skill/loader 归因才会进入修复；平台异常、工具问题和非 Skill 原因默认拒绝修改 Skill。
- **Scenario Catalog**：内置 `Content Gap`、`Loading Miss`、`Platform Error` 等典型 Agent Skill 失败模式，支持前端动态切换与后端确定性复现。
- **Benchmark Summary**：自动汇总配对评测快照，展示修复成功率、平均 pass-rate 提升、token 开销、回归风险等可写进简历的量化指标。
- **CI 持续验证**：GitHub Actions 自动执行前端构建、Node 单测、Python 后端测试和 benchmark summary 生成，保证自愈链路持续可验证。
- **可视化 Demo**：前端提供 Trace、Token、故障归因、修复验证、配对评测、评测指标和 Agent 架构页面，方便面试现场讲解。

## 在线 Demo

在线访问：<https://isolatedstar0.github.io/skill-doctor/>

当前项目通过 GitHub Pages 自动部署前端静态 Demo。静态 Demo 使用内置离线数据，不依赖外部 API；真实 Codex/LangGraph live run 需要本机或服务端启动后端 control plane。

## 核心问题

Agent 使用 Skill 时，失败不一定都应该归咎于 Skill 本身：

1. **Content Gap**：Skill 指令缺少必要约束，应该生成最小化、可回滚的 Skill patch。
2. **Loading Miss**：Skill 选择正确但依赖资源未加载，应该修复 loader，禁止改写 Skill。
3. **Platform Error / Non-Skill Cause**：外部服务、权限或网络失败，应该路由到平台 owner，禁止改写 Skill。

Skill Doctor 的核心价值是先归因再修复，避免“所有失败都自动改 Skill”导致错误修复和回归。

## 架构闭环

```text
失败 Trace
→ Evidence Snapshot
→ 最早可行动故障步骤
→ 7 类规则归因
→ Skill 责任权重
→ scoped repair / safe refusal
→ replay + regression / owner routing
→ ADOPT / ROUTE / REJECT
```

前端 `Agent 架构` 页面会将完整链路展开为七阶段：

```text
Trace Ingest
→ Evidence Builder
→ Attribution Agent
→ Repair Planner
→ Candidate Generator
→ Regression Verifier
→ Reject Memory + Storage
```

每个 Trace step 同时记录开始时间、持续时间、事件类型、模型、input/output token、cached input token、reasoning token、evidence ref 与故障状态。缓存输入是 input 的子集，reasoning token 是 output 的子集，总量只按 `input + output` 计算，避免重复计数。

## 技术栈

- **Frontend**：React 19、TypeScript、Vinext/Vite、响应式 Dashboard
- **Agent Orchestration**：Python、LangGraph、确定性 fixture worker、Codex SDK worker
- **Storage**：File Storage、SQLite Backend，可扩展到 Postgres/Cloud Storage
- **Evaluation**：paired benchmark、Benchmark Summary、regression gate、token overhead 统计
- **Quality**：GitHub Actions、Node test runner、pytest、typed TypeScript

## 环境

- Node.js >= 22.13
- npm
- Python 3.11+

离线 Demo 和测试不需要数据库、API Key 或外部服务；只有真实 Codex
probe 需要本机 Codex 登录态和网络访问。

## 分析外部 Trace

Trace Adapter 接受版本化 JSON 协议，并在归因前校验事件类型、唯一 ID、故障边界、Skill 加载状态和证据信息。`1.1` 协议不接受调用方直接声明归因结果，而是从 routing、loading、execution 和 external error 观测自动提取 signals；`1.0` 仅作为兼容格式保留：

```bash
npm run analyze -- examples/traces/content-gap.json
```

命令输出机器可读的诊断、修复计划和验证结果。格式示例见 `examples/traces/content-gap.json`。

## 接入真实 Agent

`TraceRecorder` 提供与模型供应商无关的采集边界：

- `observeRouting()`：记录候选 Skill 与最终选择
- `observeLoading()`：记录成功加载的 Skill 和缺失资源
- `addExecutionCheck()`：记录工具 schema、指令遵循和需求覆盖检查
- `startStep().finish()`：记录步骤起止、状态、证据与 usage
- `normalizeTokenUsage()`：归一化常见的 snake_case、camelCase、cache 和 reasoning usage 字段

运行完整的插桩示例并生成可导入文件：

```bash
npm run record:example -- trace.json
```

在页面右上角选择“导入 Trace JSON”，导入后会立即进入 Trace 过程页；同一份数据会驱动 Token 面板、故障归因和修复验证。文件会先经过 Trace 1.1 完整校验，超过 1 MB 或包含不可能计量的数据会被拒绝。

## 接入 Codex

Codex 专用 Adapter 接收 `codex exec --json` 的 JSONL。它将 command、file
change、MCP、web search、reasoning 和 agent message 映射成 Trace step，并把
`turn.completed.usage` 映射到单独的整轮汇总节点。Codex CLI 没有提供可靠的
逐 item token 时，系统不会把总量伪分摊到每一步。

用官方事件形状的离线 fixture 验证完整转换：

```bash
npm run codex:import -- \
  examples/codex/codex-exec-sample.jsonl \
  examples/codex/trace-context.json
```

真实运行可以先保存 Codex JSONL，再用同一命令转换；生成的 Trace 1.1 JSON
可直接导入面板。

## 开源 Skill smoke test

测试集选择自 MIT 许可的 `GeniusHTX/SWE-Skills-Bench`，当前固定测试：
`tdd-workflow`、`python-observability`、`distributed-tracing`。

```bash
npm run bench:fetch
npm run bench:skills
```

静态门禁会校验 Skill frontmatter、文档和任务完整性、目标仓库、pytest
assertion 以及 verifier Python 语法。再执行真实 Codex 只需：

```bash
npm run bench:skills -- --live
```

这个命令将每个 Skill 安装到隔离临时 Git 工作区的
`.agents/skills/<id>/SKILL.md`，再调用 `codex exec --json`。它是接入 smoke
test，不冒充完整 benchmark 分数；正式评测还需要固定仓库/Docker 环境，并运行
with-skill / without-skill 配对实验。

## LangGraph Python 编排层

`backend/` 提供真正的 Python Agent control plane。LangGraph 负责显式状态、
条件路由、修复重试和验证门禁，现有 TypeScript Codex SDK runner 继续负责
执行和 JSONL 证据采集。

完整状态流：

```text
prepare -> execute -> collect_evidence
                     -> attribute -> repair -> execute
                     -> verify -> promote / reject
```

安装 Python 依赖：

```bash
node scripts/python.mjs -m pip install -e "backend[api,dev]"
```

运行完全离线、确定性可复现的 Content Gap 自修复：

```bash
npm run agent:demo
```

把已经完成的真实 Codex SDK TDD 配对报告重放到 LangGraph：

```bash
npm run agent:replay
```

启动零额外依赖的本地 control plane：

```bash
npm run agent:api
```

API 提供 `POST /runs`、`POST /runs/stream`、`GET /runs`、
`GET /runs/events`（SSE）和 `GET /runs/{run_id}`。
每个节点都输出 attempt、stage、状态、token、pass rate 和证据哈希；完成的
运行保存在 `reports/langgraph/`。平台异常会直接结束并保持
`NO_SKILL_MUTATION`，只有高置信度的 Skill/loader 归因才能进入修复循环。

所有入口共享文件型 Run Registry。`RunService.run()`、`RunService.stream()`、
CLI、FastAPI 和零依赖 HTTP server 会在每次状态变化时原子发布快照；因此 CLI
与 API 即使运行在不同进程，前端也能通过 `/runs/events` 自动发现。页面顶部的
Run Registry 显示最近运行、SSE 连接状态和事件数量，选择任一 `run_id` 后，
全部分析视图会切换到该 Run。

页面中的 `LangGraph Loop` 会消费 `/runs/stream` 的 NDJSON 增量响应，并将
每个完整 `AgentState` 写入前端唯一的 `RunStore`。运行概览、Trace、Token、
归因、Skill diff、验证门禁以及 Benchmark 的当前运行关联都由该状态适配生成，
不再各自读取独立 mock。尚未启动 Agent 时，内置 case 只作为输入样例；一旦
Run 开始，所有页面立即切换到同一个 `run_id`、Evidence Snapshot 和 LangSmith
Trace。数据链路如下：

```text
Codex SDK / Fixture Worker
→ LangGraph AgentState
→ Run Registry
→ /runs/stream NDJSON + /runs/events SSE
→ frontend RunStore
→ Overview / Trace / Token / Attribution / Repair / Benchmark
                         ↘ Evidence Snapshot / LangSmith trace_url
```

本地默认连接 `http://localhost:8010`；需要覆盖时设置
`NEXT_PUBLIC_SKILL_DOCTOR_API_URL`。安装 `backend[api]` 后也可以运行
`npm run agent:api:fastapi` 使用 FastAPI 入口。

### 可选 LangSmith 双写

本地 NDJSON trace、Evidence Snapshot 和运行报告仍是事实来源；LangSmith
只作为第二可观测界面。未配置凭证、网络不可用或 exporter 异常都不会中断
Agent 的归因、修复和验证流程。

```bash
node scripts/python.mjs -m pip install -e "backend[api,dev,observability]"
npx cross-env LANGSMITH_TRACING="true" LANGSMITH_API_KEY="<your-key>" LANGSMITH_PROJECT="skill-doctor-dev" npm run agent:api
```

每次运行只创建一条名为 `skill-doctor.run` 的 LangGraph 原生根 trace。
LangGraph 自动记录生命周期节点；只有框架无法观察到的 Codex SDK 内部事件会
作为当前 `execute` 节点的子 run 补充上报。最终状态包含 `trace_id` 和可用时的
`trace_url`，页面会显示 `OPEN IN LANGSMITH` 入口。可复制的配置模板见
`.env.example`，不要提交真实 API Key。

### 真实 CodexExecutionWorker

LangGraph 现在支持 `executor=codex`，不再只能重放历史配对报告。Python
编排层会调用 `scripts/codex-execution-worker.mjs`，由已安装的
`@openai/codex-sdk` 在隔离临时 Git 工作区中安装当前候选 Skill，并使用
`read-only` sandbox、`approvalPolicy=never` 和禁用任务网络的配置执行真实
Codex thread。Node bridge 会把 `runStreamed()` 的每个内部事件编码为
NDJSON，Python control plane 在执行尚未结束时就将它们合并进
`/runs/stream`：

```text
Codex SDK runStreamed()
→ Node NDJSON bridge
→ Python CodexExecutionWorker callback
→ LangGraph live state
→ /runs/stream
→ React timeline
```

实时事件包括 thread、turn、reasoning、command execution、file change、
MCP call、web search、agent message、transport error 和最终 token usage。
每次 attempt 同时保存：

- Codex SDK 原始事件 JSONL 与 thread id
- 最终回答与 Token usage
- verifier assertions 与 pass rate
- Git diff 和带 SHA-256 的 Evidence Snapshot

命令行执行一次真实闭环：

```bash
npm run agent:codex
```

也可以在 `LangGraph Loop` 页面选择 `CODEX SDK LIVE`。面板会区分
LangGraph 生命周期节点和 Codex SDK 内部事件，Token 只在
`turn.completed` 计入一次。真实执行使用本机 Codex 登录态和服务连接；
鉴权、网络或超时失败会归类为 `Non-Skill Cause`，不会生成 Skill Patch。

## Codex SDK 配对评测

项目已接入官方 `@openai/codex-sdk`，对同一个任务分别运行：

- control：不安装 Skill，只使用任务描述和模型基础知识
- treatment：在 `.agents/skills/<id>/SKILL.md` 安装目标 Skill

前端“配对评测”页面可以动态选择 Skill、任务、执行器和超时，一键创建
一个 `bm-*` Benchmark 父 Run 与两个 `lg-*` Agent 子 Run。父子 Run 共用
Run Registry，并通过 SSE/NDJSON 实时更新；可在 Benchmark 与两个子 Run
之间双向跳转。

对应接口：

- `POST /benchmarks`：同步执行一组配对实验。
- `POST /benchmarks/stream`：以 NDJSON 推送父 Run 状态。
- `GET /benchmarks`：列出最近 Benchmark。
- `GET /benchmarks/{benchmark_id}`：读取持久化结果。

`fixture` 可离线验证整条链路，`replay` 可重放证据，`codex` 执行真实 SDK
实验。`public/benchmarks/latest.json` 现在仅作为尚未产生动态结果时的离线
兜底。

运行三个真实 Codex SDK 配对 probe：

```bash
npm run bench:paired
```

中断后可以复用已经完成的 condition：

```bash
npm run bench:paired -- --resume reports/paired/<run-id>
```

每个 condition 都会保存：

- Codex 流式 JSONL 与 token usage
- pytest verifier 完整输出
- Agent 运行后 git diff
- 带 SHA-256 的 `evidence-snapshot.json`

该批处理命令仍会将最新汇总写入 `public/benchmarks/latest.json`。当前三组
知识/计划 probe 的真实结果为：

| Skill | Without | With | Δ Pass rate | Token overhead | Regression |
| --- | ---: | ---: | ---: | ---: | ---: |
| TDD Workflow | 25% | 100% | +75 pp | +126.2% | 0% |
| Python Observability | 60% | 80% | +20 pp | +118.7% | 0% |
| Distributed Tracing | 100% | 100% | 0 pp | +117.6% | 0% |

这张表验证的是 Skill-grounded planning knowledge，不是仓库级代码修复成绩。
后一种成绩需要拉取固定 commit、让 Agent 修改代码，再运行数据集提供的完整
pytest suite。

## Benchmark Summary / 量化指标

项目会从 `reports/benchmarks/*.json` 自动生成 `reports/evaluation-summary.json`，并在前端 `评测指标` 页面展示：

- `repairSuccessRate`：配对实验中 treatment 明显优于 control 的比例。
- `averagePassRateDelta`：加载 Skill 后平均 pass-rate 提升。
- `averageTokenOverheadRate`：Skill 带来的 token 成本增量。
- `regressionDetectionRate`：验证门禁对回归 pair 的覆盖情况。
- `scenarioBreakdown`：按场景拆解的修复收益。

生成命令：

```bash
npm run bench:summary
```

这部分用于回答面试中的量化问题：系统不仅能展示一次修复，还能用固定指标证明 Agent Skill 的收益、成本和安全边界。

## CI / 持续验证

GitHub Actions 会在 push 和 PR 时执行完整验证：

```text
npm ci
python -m pip install -e "backend[dev,api,observability]"
npm run build
node --experimental-strip-types --test "tests/*.test.mjs"
python -m pytest backend/tests -q --basetemp=.pytest-tmp
npm run bench:summary
```

CI 覆盖前端构建、TypeScript/Node 逻辑、Python LangGraph control plane、API/存储测试和评测报告生成，确保 demo 页面、后端自愈链路和量化指标可以持续复现。

## 启动

```bash
npm install
npm run dev
```

打开终端显示的本地地址。

## 验证

```bash
npm test
```

测试会先执行生产构建，再验证：

- 三种故障的规则优先级和 step-level 归因
- 每步 Token 指标的协议校验、聚合和热点步骤定位
- `Content Gap` patch 只修改 `procedure` 中的一行并带 rollback reference
- 原失败 replay 修复、同类案例改善且历史案例无回归
- `Loading Miss` 与 `Non-Skill Cause` 生成路由动作而非 Skill diff
- `Tool Misuse` 与 `Instruction Violation` 在没有专用策略时进入 `NEEDS_REVIEW`
- 非 Skill 归因调用 patch API 时会被安全拒绝
- 7 类归因、修复类型和决策由版本化 regression manifest 固定
- 三个案例重复执行时结果完全一致

## 代码结构

```text
app/
  DemoApp.tsx        多场景、多视图的交互控制台
  globals.css        响应式界面样式
backend/
  skilldoctor/       LangGraph 状态图、worker、CLI、API 与运行存储
  tests/             修复闭环、平台安全边界和真实 Codex replay 测试
lib/
  demo-engine.ts     Trace、规则归因、修复隔离和验证引擎
  attribution-engine.ts  有版本和优先级证明的 7 类归因规则
  benchmark-engine.ts  配对指标、token overhead 和回归率计算
  benchmark-summary.ts  多次 benchmark 快照的汇总指标生成
  codex-jsonl-adapter.ts  Codex CLI JSONL 到 Trace 1.1 的无损边界
  trace-adapter.ts   版本化 JSON Trace 协议与运行时校验
  trace-recorder.ts  provider-neutral Agent 插桩与 usage 归一化
scripts/
  analyze-trace.mjs  外部 Trace CLI 分析入口
  generate-benchmark-summary.mjs  Benchmark Summary 报告生成入口
  import-codex-jsonl.mjs  Codex JSONL 转换入口
  run-paired-codex-benchmark.mjs  Codex SDK 配对执行与 Evidence Snapshot
  run-codex-skill-smoke.mjs  三个开源 Skill 的静态/live probe
tests/
  benchmark-engine.test.mjs
  codex-jsonl-adapter.test.mjs
  demo-engine.test.mjs
  trace-adapter.test.mjs
  repair-strategy.test.mjs
  token-usage.test.mjs
  trace-recorder.test.mjs
  fixtures/attribution-regression.json
```

## 演示重点

面试时先运行“内容缺口”，展示完整的 Skill patch 与验证门禁；再切换到“加载遗漏”和“平台异常”，强调系统不会把所有失败都归咎于 Skill。后两条路径中的 `NO_SKILL_MUTATION` 是这个 Demo 的安全边界，也是相对普通自动改 prompt 项目的主要差异。

推荐 3 分钟讲解顺序：

1. **问题背景**：Agent Skill 失败不一定是 Skill 内容错，错误归因会导致错误修复。
2. **系统方案**：用 Trace → Evidence → Attribution → Repair → Verify → Memory 的闭环先归因再修复。
3. **安全边界**：Content Gap 才 patch Skill，Loading Miss patch loader，Platform Error 只路由不改 Skill。
4. **量化结果**：用 Benchmark Summary 展示 pass-rate 提升、修复成功率、token 成本和回归风险。
5. **工程化**：CI 自动跑前端构建、Node 单测、Python 后端测试和评测报告生成。

## 简历表述参考

> Skill Doctor：面向 Agent Skill 的自愈诊断与修复系统。基于 LangGraph 设计 Trace Ingest、Evidence Builder、Attribution Agent、Repair Planner、Candidate Generator、Regression Verifier、Reject Memory 七阶段工作流，实现 Agent 执行失败的证据冻结、故障归因、候选修复、回归验证与持久化管理。构建 Scenario Catalog 覆盖 Content Gap、Loading Miss、Platform Error 等典型失败模式，并实现 Benchmark Summary 自动汇总修复成功率、pass-rate 提升、token 开销和回归检测指标；接入 GitHub Actions CI 自动执行前端构建、Node 单测、Python 后端测试与评测报告生成。
