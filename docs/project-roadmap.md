# Skill Doctor 完整项目排期

> 目标：在 2～3 周内完成一个可复现、可演示、可量化、可以写入秋招简历的  
> **Skill Reliability Agent（Skill 可靠性智能体）**，后续再扩展为完整研究与工程平台。

## 1. 项目定位

Skill Doctor 不只是“失败后自动修改提示词”，而是一套面向 Agent Skill 的可靠性闭环：

```text
任务与 Skill
→ Agent 执行
→ Trace 与 Token 采集
→ Evidence Snapshot
→ 失败归因
→ 最小化修复
→ with-skill / without-skill 配对验证
→ 回归门禁
→ ADOPT / REJECT / ROUTE
```

核心能力：

- 可观测：记录 LangGraph、Codex SDK、工具调用、Token、耗时和状态变化。
- 可归因：区分 Skill、召回、选择、加载、工具、平台等不同责任边界。
- 可修复：只在证据充分时生成最小化、可回滚的 Skill Patch。
- 可验证：通过失败重放、配对实验和回归测试决定是否采用修复。
- 可追溯：本地 Run、Evidence Snapshot、LangSmith Trace 和评测结果使用同一 `run_id`。

## 2. 状态标记规范

- [√] 已完成并经过测试或实际运行验证
- [ ] 尚未完成
- 完成一项后，在对应任务前打勾，并在同一个 commit 中更新本文档。
- 未通过验收标准的任务不能提前标记完成。

---

## 3. 已完成工作

### 3.1 可复现基础 Demo

- [√] 建立 React/Vinext 可视化前端。
- [√] 建立确定性离线 Demo，不依赖模型 API 即可复现。
- [√] 内置 `Content Gap` 故障案例。
- [√] 内置 `Loading Miss` 故障案例。
- [√] 内置 `Non-Skill Cause` 故障案例。
- [√] 实现 7 类 Skill 故障分类体系。
- [√] 实现带优先级和版本号的规则归因引擎。
- [√] 实现 Skill 责任权重和归因置信度。
- [√] 实现最小化 Skill Patch。
- [√] 实现 `NO_SKILL_MUTATION` 安全边界。
- [√] 实现 ADOPT、REJECT、ROUTE、NEEDS_REVIEW 决策。

### 3.2 Trace 与 Token 可观测性

- [√] 定义版本化 Trace JSON 协议。
- [√] 实现外部 Trace 导入和输入校验。
- [√] 实现 provider-neutral `TraceRecorder`。
- [√] 记录每步开始时间、耗时、类型、状态和 Evidence Ref。
- [√] 记录 input token 和 output token。
- [√] 记录 cached input token。
- [√] 记录 reasoning token。
- [√] 避免缓存 Token 和推理 Token 重复计数。
- [√] 实现 Token 总量、缓存命中率和热点步骤统计。
- [√] 实现 Trace 时间线和 Token 可视化面板。

### 3.3 Codex 接入

- [√] 实现 Codex JSONL → Trace 1.1 Adapter。
- [√] 映射 command、file change、MCP、web search、reasoning 和 agent message。
- [√] 保留 `turn.completed` 的真实 Token usage。
- [√] 接入官方 `@openai/codex-sdk`。
- [√] 实现 Node NDJSON bridge。
- [√] 使用 `runStreamed()` 实时输出 Codex 内部事件。
- [√] Python `CodexExecutionWorker` 实时消费 Node NDJSON。
- [√] Codex 内部事件进入 LangGraph 当前执行节点。
- [√] 保存 Codex JSONL、最终响应和 git diff。
- [√] 生成带 SHA-256 的 Evidence Snapshot。

### 3.4 LangGraph Agent 编排层

- [√] 使用 Python LangGraph 作为 Agent control plane。
- [√] 定义统一 `AgentState`。
- [√] 实现 `prepare` 节点。
- [√] 实现 `execute` 节点。
- [√] 实现 `collect_evidence` 节点。
- [√] 实现 `attribute` 节点。
- [√] 实现 `repair` 节点。
- [√] 实现 `verify` 节点。
- [√] 实现 `promote/finalize` 节点。
- [√] 实现最大修复次数和条件路由。
- [√] 实现 Fixture、Replay、Codex 三种 Execution Worker。
- [√] 实现平台错误不修改 Skill 的安全结束路径。

### 3.5 LangSmith 可观测性

- [√] 接入 LangGraph 自动 Trace。
- [√] 合并重复 Trace，只保留一个 `skill-doctor.run` 根 Trace。
- [√] Codex SDK 内部事件作为 `execute` 节点子 Trace。
- [√] 将 Token 映射为 LangSmith 标准 usage 字段。
- [√] 前端提供 `OPEN IN LANGSMITH` 入口。
- [√] LangSmith 不可用时不阻断本地 Agent。
- [√] 已完成 API Key 轮换和单根 Trace 验证。

### 3.6 前后端统一 Run 数据链路

- [√] `/runs/stream` 输出 NDJSON 状态流。
- [√] 建立前端唯一 `RunStore`。
- [√] 将 LangGraph `AgentState` 适配为全部前端视图模型。
- [√] 概览页面读取当前 Run。
- [√] Trace 页面读取当前 Run。
- [√] Token 页面读取当前 Run。
- [√] 归因页面读取当前 Run。
- [√] 修复页面读取当前 Run。
- [√] 验证页面读取当前 Run。
- [√] Benchmark 页面关联当前 Run、Evidence 和 LangSmith。
- [√] 样例数据只作为尚未运行 Agent 时的离线输入。
- [√] 移除前端伪造的闭环进度动画和固定评测数字。

### 3.7 Run Registry 与 SSE

- [√] 实现跨进程文件型 Run Registry。
- [√] 使用原子 JSON 快照避免读取半写入文件。
- [√] `RunService.run()` 自动登记状态。
- [√] `RunService.stream()` 自动登记状态。
- [√] CLI Run 自动进入 Registry。
- [√] `POST /runs` 产生的 Run 自动进入 Registry。
- [√] `POST /runs/stream` 产生的 Run 自动进入 Registry。
- [√] 实现 `GET /runs` 最近运行列表。
- [√] 实现 `GET /runs/{run_id}` 当前或历史快照读取。
- [√] 实现 `GET /runs/events` SSE。
- [√] 实现 SSE heartbeat。
- [√] 前端自动订阅后端 Run。
- [√] 前端展示 Registry 连接状态。
- [√] 前端展示最近 Run 列表。
- [√] 前端支持切换任意 Run。
- [√] 切换 Run 后全部面板同步更新。
- [√] 真实离线 Run 已完成 Registry 发现验证。

### 3.8 当前测试与代码管理

- [√] Node 测试 46 项通过。
- [√] Python 测试 19 项通过。
- [√] HTTP SSE 集成测试通过。
- [√] 跨实例 Registry 测试通过。
- [√] 生产构建通过。
- [√] ESLint 通过。
- [√] 项目已推送至 GitHub。
- [√] 统一 Run 数据链路 commit：`5e44e65`。
- [√] Run Registry + SSE commit：`9f37db1`。

---

## 4. 后续 2～3 周核心排期

## 第 1 周：动态 with-skill / without-skill 配对评测

### 目标

将当前静态 `public/benchmarks/latest.json` 升级为由前端触发、后端执行、SSE
实时展示的动态评测。完成后，整个项目真正统一在 RunService 数据链路上。

### 后端任务

- [ ] 定义 `BenchmarkRequest`、`BenchmarkState` 和 `BenchmarkResult`。
- [ ] 实现 `BenchmarkService`。
- [ ] 为一次配对实验生成 Benchmark 父 Run ID。
- [ ] 生成 without-skill Control 子 Run。
- [ ] 生成 with-skill Treatment 子 Run。
- [ ] 父子 Run 记录 `parent_run_id` 和 `condition`。
- [ ] 保证两组使用相同任务、模型、超时和工作区基线。
- [ ] 实现 `POST /benchmarks`。
- [ ] 实现 `GET /benchmarks`。
- [ ] 实现 `GET /benchmarks/{benchmark_id}`。
- [ ] 将 Benchmark 状态发布到 Run Registry/SSE。
- [ ] 保存每组 Codex JSONL、pytest 输出、git diff 和 Evidence Snapshot。
- [ ] 聚合 pass rate delta。
- [ ] 聚合 Token overhead。
- [ ] 聚合 duration delta。
- [ ] 聚合 regression rate。
- [ ] 记录配对实验停止原因和失败分类。

### 前端任务

- [ ] 增加“启动配对评测”入口。
- [ ] 增加 Skill、任务、执行器和超时选择。
- [ ] 实时展示 Control 与 Treatment 的运行状态。
- [ ] 实时展示两组 Trace 和 Token。
- [ ] 展示 pass rate、Token、耗时和回归率对照。
- [ ] 支持从 Benchmark 跳转到两个子 Run。
- [ ] 支持从子 Run 返回 Benchmark 父 Run。
- [ ] 将 `latest.json` 降级为离线兜底数据。

### 测试任务

- [ ] 测试父子 Run 关联关系。
- [ ] 测试两组实验输入条件一致。
- [ ] 测试 Token overhead 计算。
- [ ] 测试回归率计算。
- [ ] 测试其中一组失败时 Benchmark 仍能安全结束。
- [ ] 测试 Benchmark SSE 实时更新。

### 第 1 周验收标准

- [ ] 前端能够一键启动一组真实配对评测。
- [ ] Control 与 Treatment 都能在 Run Registry 中查看。
- [ ] 页面实时展示两组运行进度。
- [ ] 最终输出 pass rate、Token、耗时和回归率。
- [ ] 所有指标可追溯到对应 Evidence Snapshot 和 LangSmith Trace。
- [ ] 完整测试、构建和 lint 通过。

---

## 第 2 周：真实代码任务与证据驱动修复

### 目标

从“只读知识/计划 Probe”升级到可复现的小型代码修改任务，让项目能够证明
Skill 对 Agent 实际工程行为的影响。

### 数据集任务

- [ ] 选择 3～5 个规模可控的真实代码任务。
- [ ] 每个任务固定 Git 仓库和 commit。
- [ ] 每个任务固定任务描述。
- [ ] 每个任务固定目标 Skill。
- [ ] 每个任务固定允许修改的目录。
- [ ] 每个任务固定 pytest 或 verifier 命令。
- [ ] 每个任务固定超时、网络和权限策略。
- [ ] 每个任务定义 task-owned、skill-owned 和 system-owned assertions。

建议首批任务：

- [ ] Python 函数缺陷修复。
- [ ] pytest 测试补全。
- [ ] 日志或 Trace 插桩。
- [ ] 配置文件错误修复。
- [ ] 小型安全重构。

### 隔离执行任务

- [ ] 为每次执行创建隔离临时 Git 工作区。
- [ ] 校验 baseline commit。
- [ ] 安装或禁用目标 Skill。
- [ ] 限制可修改目录。
- [ ] 执行 Codex SDK 多轮修复。
- [ ] 执行 pytest/verifier。
- [ ] 收集 git diff。
- [ ] 保存退出码、stdout、stderr 和超时信息。
- [ ] 运行结束后保留可复现元数据。

### Evidence Snapshot 任务

- [ ] 合并 Codex SDK JSONL。
- [ ] 合并 pytest 输出。
- [ ] 合并 git diff。
- [ ] 合并 Token usage。
- [ ] 合并执行耗时。
- [ ] 合并 Skill 版本和加载状态。
- [ ] 合并权限、网络、超时和进程错误。
- [ ] 为每类证据生成 SHA-256。
- [ ] Evidence Ref 可从前端点击查看。

### 归因与修复任务

- [ ] 使用真实 assertion ownership 计算 Skill 责任。
- [ ] 区分代码实现错误和 Skill 内容缺口。
- [ ] 区分 Skill Loading Miss 和 Selection Error。
- [ ] 区分工具错误、平台错误和网络错误。
- [ ] 低置信度归因进入 `NEEDS_REVIEW`。
- [ ] 高置信度 Skill 归因生成最小化 Patch。
- [ ] Patch 带 base version、next version 和 rollback ref。
- [ ] 修复后自动重新执行相同任务。
- [ ] 修复后重新执行完整回归门禁。

### 第 2 周验收标准

- [ ] 至少 3 个真实代码任务可重复运行。
- [ ] 每个任务都有明确 verifier。
- [ ] 每个失败都能生成 Evidence Snapshot。
- [ ] 至少 1 个任务展示 Skill 修复前后提升。
- [ ] 至少 1 个任务展示非 Skill 原因并拒绝修改 Skill。
- [ ] 修复前后 diff、Trace、Token 和测试结果均可视化。
- [ ] 完整测试、构建和 lint 通过。

---

## 第 3 周：跨平台、演示与秋招交付

### 目标

把工程 Demo 收敛成面试官可以在 2～3 分钟内理解、在本地可以按文档启动的项目。

### Windows/macOS 跨平台任务

- [ ] 增加统一 Python Launcher。
- [ ] 支持 `PYTHON` 环境变量显式指定解释器。
- [ ] 自动探测 `python3` 和 `python`。
- [ ] 移除 npm scripts 中固定的 `python` 命令。
- [ ] 移除 Benchmark 脚本中固定的 `python` 命令。
- [ ] 为数据下载增加 macOS/Linux `curl` fallback。
- [ ] README 同时提供 PowerShell 命令。
- [ ] README 同时提供 Bash/zsh 命令。
- [ ] GitHub Actions 增加 `windows-latest`。
- [ ] GitHub Actions 增加 `macos-latest`。
- [ ] 两个平台执行前端生产构建。
- [ ] 两个平台执行 Fixture/Replay 测试。
- [ ] Apple Silicon Mac 手工执行一次 Codex SDK smoke test。

### 稳定性与易用性任务

- [ ] 增加一键安装说明。
- [ ] 增加一键启动前后端脚本。
- [ ] 增加端口占用检测与明确提示。
- [ ] 增加 Python API 未启动时的前端提示。
- [ ] 增加 SSE 断线重连状态。
- [ ] 增加 Run 失败和超时提示。
- [ ] 增加空 Benchmark 和无 Evidence 状态。
- [ ] 确认 `.env.example` 完整且不包含密钥。
- [ ] 增加敏感信息提交检查。

### 项目展示任务

- [ ] README 首屏增加一句话项目介绍。
- [ ] README 增加系统架构图。
- [ ] README 增加统一 Run 数据链路图。
- [ ] README 增加 with-skill / without-skill 对照表。
- [ ] README 增加 3 个典型失败案例。
- [ ] README 增加快速启动步骤。
- [ ] README 增加验证命令和预期输出。
- [ ] 准备 2～3 分钟面试演示脚本。
- [ ] 录制演示 GIF 或视频。
- [ ] 准备一张真实 Benchmark 结果截图。
- [ ] 准备一张 Trace + Token 截图。
- [ ] 准备一张修复前后对照截图。
- [ ] 准备常见面试追问与回答。

### 简历交付任务

- [ ] 完成 50～80 字项目简介。
- [ ] 准备 3 条量化项目亮点。
- [ ] 写清 LangGraph、Codex SDK、LangSmith、SSE 技术栈。
- [ ] 写清统一 Run ID 与 Evidence Snapshot 设计。
- [ ] 写清 pass rate、Token overhead 和 regression rate 指标。
- [ ] 所有简历数字来自可复现 Benchmark，不使用模拟数据。

### 第 3 周验收标准

- [ ] 新环境能够按 README 在 15 分钟内启动项目。
- [ ] Windows CI 通过。
- [ ] macOS CI 通过。
- [ ] 演示全程不依赖手工修改文件或数据库。
- [ ] 2～3 分钟内能讲清失败、归因、修复和验证闭环。
- [ ] GitHub 首页能直接看到架构、结果和启动方式。
- [ ] 简历中的每个量化数据均有报告或 Trace 支撑。

---

## 5. 面试版最终演示流程

- [ ] 启动 Python Agent API。
- [ ] 启动前端。
- [ ] 从前端选择一个真实任务和目标 Skill。
- [ ] 启动 with-skill / without-skill 配对实验。
- [ ] 在 Run Registry 中实时看到父 Run 和两个子 Run。
- [ ] 打开 Trace 页面展示 LangGraph 与 Codex SDK 事件。
- [ ] 打开 Token 页面展示消耗和热点步骤。
- [ ] 展示 pytest、git diff 和 Evidence Snapshot。
- [ ] 展示失败分类、责任权重和证据引用。
- [ ] 展示系统生成的最小化 Skill Patch。
- [ ] 展示修复后自动重跑。
- [ ] 展示 pass rate、Token、耗时和回归率变化。
- [ ] 打开 LangSmith 展示单根 Trace 和子事件。
- [ ] 最后展示 ADOPT、REJECT 或 ROUTE 决策。

---

## 6. MVP、秋招版与后续版边界

### 当前 MVP

- [√] Agent 闭环可以运行。
- [√] Trace 和 Token 可以实时展示。
- [√] 失败可以归因。
- [√] Skill 可以安全修复或拒绝修复。
- [√] 修复可以经过验证门禁。
- [√] LangSmith 可查看单根 Trace。
- [√] 前端、后端、Agent、Trace 和验证共享同一 Run。
- [√] CLI/API Run 可以通过 Registry 和 SSE 被前端发现。

### 秋招可讲版本

- [ ] 动态配对 Benchmark 完成。
- [ ] 至少 3 个真实代码任务完成。
- [ ] 真实 Benchmark 指标完成。
- [ ] Windows/macOS CI 完成。
- [ ] README、截图和演示视频完成。
- [ ] 简历描述完成。

### 秋招后再做

- [ ] 接入 SkillsBench 非编码任务。
- [ ] 接入 Skill-Usage 34K 检索数据。
- [ ] 评测大规模 Skill 召回、选择和路由。
- [ ] 使用 LangSmith Dataset 和 Evaluator。
- [ ] 增加人工标注和 Annotation Queue。
- [ ] 将文件型 Registry 升级为 SQLite/PostgreSQL。
- [ ] 支持多用户与权限隔离。
- [ ] 支持分布式 Worker。
- [ ] 支持任务队列、暂停、取消和重试。
- [ ] 支持远程部署 Python Agent API。
- [ ] 需要双向控制时再评估 WebSocket。

---

## 7. 每次开发迭代的固定流程

每完成一个任务，按以下顺序执行：

1. 实现最小范围改动。
2. 增加或更新自动化测试。
3. 运行生产构建。
4. 运行 Node 测试。
5. 运行 Python 测试。
6. 运行 ESLint。
7. 执行一次与改动相关的真实 smoke test。
8. 检查 `git diff --check`。
9. 在本文档中将对应任务改为 `[√]`。
10. commit。
11. push 到 GitHub `main`。

建议 commit 格式：

```text
feat: add dynamic paired benchmark service
test: cover benchmark parent child lifecycle
fix: preserve evidence when sse reconnects
docs: update verified project roadmap
```

---

## 8. 当前最优先的下一项

当前下一项固定为：

> **实现动态 `BenchmarkService`，让前端能够触发真正的 with-skill /
> without-skill 配对运行，并实时展示 pass rate、Token、耗时和回归率。**

在这项完成前，不优先增加新的 Agent 框架、数据库、复杂多 Agent 协作或大规模
数据集，以免分散秋招版本的交付重点。
