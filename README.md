# Skill Doctor Demo

Skill Doctor 是一个完全离线、确定性可复现的 Agent Skill 可观测、可归因、可修复、可验证 Demo。

当前版本内置三条可切换故障链：

1. `Content Gap`：Skill 指令缺少必要约束，生成最小化、可回滚的 Skill patch。
2. `Loading Miss`：Skill 选择正确但依赖资源未加载，只修复 loader，禁止改写 Skill。
3. `Non-Skill Cause`：外部服务权限失败，路由到 platform owner，禁止改写 Skill。

每个 Trace step 同时记录：

- 开始时间、持续时间、事件类型和模型
- input / output token
- cached input token
- reasoning token
- evidence ref 与故障状态

界面中的 `Trace 过程` 会显示完整执行流和每步消耗，`Token 面板` 会显示总量、缓存命中率、热点步骤、逐步堆叠分解和累计消耗曲线。缓存输入是 input 的子集，reasoning token 是 output 的子集，总量只按 `input + output` 计算，避免重复计数。

完整闭环：

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

## 环境

- Node.js >= 22.13
- npm

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
python -m pip install -e "backend[api,dev]"
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

API 提供 `POST /runs`、`POST /runs/stream` 和 `GET /runs/{run_id}`。
每个节点都输出 attempt、stage、状态、token、pass rate 和证据哈希；完成的
运行保存在 `reports/langgraph/`。平台异常会直接结束并保持
`NO_SKILL_MUTATION`，只有高置信度的 Skill/loader 归因才能进入修复循环。

页面中的 `LangGraph Loop` 会消费 `/runs/stream` 的 NDJSON 增量响应，
逐节点更新时间线、Token、归因、Skill diff 和验证门禁。本地默认连接
`http://localhost:8010`；需要覆盖时设置
`NEXT_PUBLIC_SKILL_DOCTOR_API_URL`。安装 `backend[api]` 后也可以运行
`npm run agent:api:fastapi` 使用 FastAPI 入口。

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

最新汇总写入 `public/benchmarks/latest.json`，页面中的“配对评测”直接读取这份
报告。当前三组知识/计划 probe 的真实结果为：

| Skill | Without | With | Δ Pass rate | Token overhead | Regression |
| --- | ---: | ---: | ---: | ---: | ---: |
| TDD Workflow | 25% | 100% | +75 pp | +126.2% | 0% |
| Python Observability | 60% | 80% | +20 pp | +118.7% | 0% |
| Distributed Tracing | 100% | 100% | 0 pp | +117.6% | 0% |

这张表验证的是 Skill-grounded planning knowledge，不是仓库级代码修复成绩。
后一种成绩需要拉取固定 commit、让 Agent 修改代码，再运行数据集提供的完整
pytest suite。

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
  DemoApp.tsx        三场景、四视图的交互控制台
  globals.css        响应式界面样式
backend/
  skilldoctor/       LangGraph 状态图、worker、CLI、API 与运行存储
  tests/             修复闭环、平台安全边界和真实 Codex replay 测试
lib/
  demo-engine.ts     Trace、规则归因、修复隔离和验证引擎
  attribution-engine.ts  有版本和优先级证明的 7 类归因规则
  benchmark-engine.ts  配对指标、token overhead 和回归率计算
  codex-jsonl-adapter.ts  Codex CLI JSONL 到 Trace 1.1 的无损边界
  trace-adapter.ts   版本化 JSON Trace 协议与运行时校验
  trace-recorder.ts  provider-neutral Agent 插桩与 usage 归一化
scripts/
  analyze-trace.mjs  外部 Trace CLI 分析入口
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
