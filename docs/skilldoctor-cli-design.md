# Skill Doctor CLI 总设计方案

## 1. 产品定位

`skilldoctor-cli` 是面向 Skill 开发者和 CI 流水线的本地诊断与质量评估工具。

它不替代现有后端平台，而是作为一个 **CLI-first 的本地产品层**，复用现有 Skill Doctor 后端能力，帮助开发者在以下场景中快速判断 Skill 是否可发布：

1. 本地调试单条 Skill 执行 trace
2. 判断失败是否由 Skill 自身导致
3. 对成功运行进行质量评分，发现“表面成功但质量较差”的问题
4. 批量跑 case set 做回归测试
5. 比较两个版本之间是否存在质量退化
6. 在 CI 中用明确 exit code 阻断不合格发布

核心原则：

> CLI 只做产品编排、输入输出适配和 CI 门禁，不复制后端诊断、归因、存储、benchmark 核心逻辑。

---

## 2. 总体架构

整体采用三层架构：

```text
skilldoctor-cli
  ├── CLI 命令层
  │   ├── diagnose
  │   ├── evaluate
  │   ├── bench
  │   ├── compare
  │   └── report
  │
  ├── CLI 产品适配层
  │   ├── 参数解析
  │   ├── JSON / JSONL 输入
  │   ├── Markdown / JSON 输出
  │   ├── 质量评分
  │   ├── CI exit code
  │   └── 报告落盘
  │
  └── Backend 复用层
      ├── RunService
      ├── BenchmarkService
      ├── TraceIngestRequest
      ├── DiagnosticCaseRequest
      ├── SQLite/File Storage
      └── attribution pipeline
```

现有后端能力继续作为核心：

- Trace 归因入口：`RunService.ingest_trace(...)`
- Diagnostic case 执行：`RunService._run_diagnostic_case(...)`
- Benchmark 执行：`BenchmarkService.run(...)`
- 持久化：`FileStorageBackend` / `SQLiteStorageBackend`
- 上传 trace worker：`UploadedTraceWorker`

---

## 3. 子项目结构设计

独立子项目放在：

```text
skilldoctor-cli/
```

建议结构：

```text
skilldoctor-cli/
  ├── pyproject.toml
  ├── skilldoctor_cli/
  │   ├── __init__.py
  │   ├── main.py
  │   ├── backend.py
  │   ├── workspace.py
  │   ├── quality.py
  │   ├── commands/
  │   │   ├── diagnose.py
  │   │   ├── evaluate.py
  │   │   ├── bench.py
  │   │   ├── compare.py
  │   │   └── report.py
  │   └── output/
  │       ├── console.py
  │       ├── json_writer.py
  │       └── markdown_writer.py
  └── examples/
      ├── traces/
      └── cases/
```

职责划分：

| 模块 | 职责 |
|---|---|
| `main.py` | CLI 入口、subcommand 注册、统一异常处理 |
| `backend.py` | 动态加载已有 backend，屏蔽本地路径差异 |
| `workspace.py` | 项目根目录解析、JSON/JSONL 读写、路径工具 |
| `quality.py` | CLI 侧 6 维质量评分 |
| `commands/*` | 五个核心命令的业务编排 |
| `output/*` | JSON、Markdown、console 输出 |

---

## 4. 五个核心命令设计

MVP 固定只实现以下五个命令：

```bash
skilldoctor diagnose
skilldoctor evaluate
skilldoctor bench
skilldoctor compare
skilldoctor report
```

### 4.1 `diagnose`

目标：诊断一条 Skill trace，判断运行是否失败、失败是否由 Skill 导致、是否有 repair 建议，以及是否应该在 CI 中失败退出。

示例：

```bash
skilldoctor diagnose \
  --trace skilldoctor-cli/examples/traces/failed-skill-trace.json \
  --out reports/cli/diagnose.json \
  --markdown reports/cli/diagnose.md
```

核心流程：

```text
读取 trace JSON
  → 转成 TraceIngestRequest
  → 调用 RunService.ingest_trace
  → 获取 backend attribution 结果
  → 生成 CLI summary
  → 输出 JSON / Markdown
  → 根据结果返回 exit code
```

### 4.2 `evaluate`

目标：对一条运行进行质量评估，既关注失败归因，也关注成功运行是否“低质量”。

示例：

```bash
skilldoctor evaluate \
  --trace skilldoctor-cli/examples/traces/healthy-skill-trace.json \
  --min-score 0.75 \
  --out reports/cli/evaluate.json \
  --markdown reports/cli/evaluate.md
```

固定使用六个质量维度：

| 维度 | 含义 |
|---|---|
| `output_quality` | 输出结果质量 |
| `contract_compliance` | 是否符合 Skill 契约/流程要求 |
| `evidence_support` | 是否有足够证据支撑结论 |
| `cost_efficiency` | token、耗时、重试成本是否合理 |
| `safety_boundary` | 是否遵守安全边界，是否误归因、越权 |
| `stability` | 是否稳定，是否存在回归风险 |

### 4.3 `bench`

目标：批量执行 case set，适合本地回归和 CI release gate。

示例：

```bash
skilldoctor bench \
  --cases skilldoctor-cli/examples/cases/release-checklist.jsonl \
  --min-pass-rate 0.95 \
  --out reports/cli/bench.json \
  --markdown reports/cli/bench.md
```

核心流程：

```text
读取 JSONL case set
  → 逐条构造 DiagnosticCaseRequest
  → 调用后端 diagnostic case 逻辑
  → 汇总 pass/fail/category/repairable
  → 计算通过率
  → 输出 benchmark report
  → 根据 min-pass-rate 返回 exit code
```

### 4.4 `compare`

目标：比较两个版本的 benchmark/evaluate 报告，判断新版是否发生回归。

示例：

```bash
skilldoctor compare \
  --baseline reports/cli/baseline-bench.json \
  --candidate reports/cli/candidate-bench.json \
  --max-regression 0.05 \
  --out reports/cli/compare.json \
  --markdown reports/cli/compare.md
```

至少比较：

1. case 通过率变化
2. Skill-owned failure 数量变化
3. non-skill failure 数量变化
4. 平均质量分变化
5. 六维质量分变化
6. token / duration 成本变化
7. 新增失败 case
8. 修复 case
9. 持续失败 case

### 4.5 `report`

目标：把已有 JSON 报告重新渲染成 Markdown，便于人工阅读或 CI artifact 展示。

示例：

```bash
skilldoctor report \
  --input reports/cli/bench.json \
  --format markdown \
  --out reports/cli/bench.md
```

通过 JSON 中的 `command` 字段自动识别报告类型。

---

## 5. Exit Code 设计

| Exit Code | 含义 |
|---:|---|
| `0` | 通过 |
| `1` | CLI 使用错误、参数错误、未分类异常 |
| `10` | diagnose 发现 Skill-owned failure |
| `20` | evaluate 质量分低于阈值 |
| `30` | bench 通过率低于阈值 |
| `40` | compare 发现不可接受回归 |
| `130` | 用户中断 |

原则：

- 平台/工具/网络类问题默认不直接用 Skill release gate 阻断，除非用户显式开启严格模式
- Skill-owned failure 应该阻断发布
- 质量分低于阈值应该阻断发布
- 版本回归应该阻断发布

---

## 6. 报告设计

每个命令原则上都支持两类输出：

1. 机器可读 JSON：用于 CI、compare、归档和自动化分析。
2. 人类可读 Markdown：用于本地查看、CI artifact、PR 评论和 release review。

JSON 要求：

- 字段稳定
- 包含 `command`
- 包含 `exit_code`
- 包含 summary
- 尽量保留 backend 原始 attribution 信息

Markdown 统一结构：

```markdown
# Skill Doctor Report

## Summary

## Attribution / Quality / Benchmark Result

## Failed Cases / Regressions

## Evidence

## Suggested Actions
```

---

## 7. 数据输入格式设计

### 7.1 Trace JSON

CLI 应兼容后端 `TraceIngestRequest`。

最小输入：

```json
{
  "task": "Run target skill.",
  "skill_id": "demo-skill",
  "skill_version": "1.0.0",
  "skill_content": "Follow the complete workflow.",
  "repair_enabled": false,
  "execution": {
    "executor": "aime-skill-trace",
    "condition": "with_skill",
    "passed": false,
    "pass_rate": 0.5,
    "duration_ms": 1200,
    "summary": "Skill skipped required step.",
    "assertions": [
      {
        "id": "complete-procedure",
        "source": "skill",
        "passed": false,
        "detail": "Required step was skipped."
      }
    ]
  }
}
```

### 7.2 Case JSONL

每行一个 case：

```json
{
  "case_id": "case-001",
  "name": "Skill skips required step",
  "description": "Expected to be attributed to skill",
  "source": "local",
  "trace": {},
  "expectation": {
    "status": "failed",
    "cause": "skill",
    "fault_type": "content_gap",
    "action": "revise_skill",
    "should_repair": true,
    "should_call_llm": false
  }
}
```

---

## 8. Backend 复用策略

CLI 可以实现：

- 参数解析
- 文件读取
- JSON/JSONL 解析
- 调用 backend service
- 输出 JSON/Markdown
- 质量评分
- 结果聚合
- CI exit code
- 本地 workspace 适配

CLI 不重新实现：

- trace attribution 核心判断
- repair patch 生成逻辑
- verification 核心逻辑
- benchmark pair 执行逻辑
- storage backend
- backend models

---

## 9. CI 使用方案

典型 CI 流程：

```bash
python3.11 -m pip install -e ./skilldoctor-cli

skilldoctor bench \
  --cases skilldoctor-cli/examples/cases/release-checklist.jsonl \
  --min-pass-rate 0.95 \
  --out reports/skilldoctor/bench.json \
  --markdown reports/skilldoctor/bench.md

skilldoctor compare \
  --baseline reports/baseline/bench.json \
  --candidate reports/skilldoctor/bench.json \
  --max-regression 0.03 \
  --out reports/skilldoctor/compare.json \
  --markdown reports/skilldoctor/compare.md
```

推荐 release gate：

| 阶段 | 命令 | 阈值 |
|---|---|---|
| 单条失败诊断 | `diagnose` | Skill-owned failure 阻断 |
| 单条质量评估 | `evaluate` | `overall >= 0.75` |
| 批量回归 | `bench` | `pass_rate >= 0.95` |
| 版本比较 | `compare` | `regression <= 0.03` |

---

## 10. 测试策略

### 10.1 CLI smoke test

优先使用 Python 3.11：

```bash
/opt/homebrew/bin/python3.11 -m skilldoctor_cli.main diagnose ...
/opt/homebrew/bin/python3.11 -m skilldoctor_cli.main evaluate ...
/opt/homebrew/bin/python3.11 -m skilldoctor_cli.main bench ...
/opt/homebrew/bin/python3.11 -m skilldoctor_cli.main compare ...
/opt/homebrew/bin/python3.11 -m skilldoctor_cli.main report ...
```

### 10.2 Backend regression test

```bash
/opt/homebrew/bin/python3.11 -m pytest backend/tests/test_trace_ingest.py
/opt/homebrew/bin/python3.11 -m pytest backend/tests/test_benchmark.py
```

### 10.3 CLI command behavior test

后续补充 CLI 自身测试，重点覆盖：

- 参数缺失
- JSON 格式错误
- JSONL 跳过空行和注释
- exit code 是否正确
- Markdown 是否生成
- compare 对新增 failure 的判断
- evaluate 阈值判断

---

## 11. 后续迭代路线

### 阶段 1：MVP 稳定化

目标：让五个命令稳定可用。

任务：

1. 固化五个命令参数
2. 固化 JSON report schema
3. 固化 exit code
4. 增加 CLI 单测
5. 确保 Python 3.11 下可安装、可运行
6. 清理临时报告文件和无关 untracked 文件

验收标准：

- 五个命令均可运行
- 示例 trace/case 可跑通
- JSON 和 Markdown 输出正确
- exit code 符合预期
- 不复制 backend 核心逻辑

### 阶段 2：质量评分增强

目标：让 `evaluate` 更有产品价值。

增强方向：

1. 增加每个维度的 reasons
2. 增加 evidence references
3. 增加 score breakdown
4. 支持用户自定义权重
5. 支持 fail-on-dimension

### 阶段 3：Benchmark 数据集标准化

目标：让 case set 能长期维护。

增强方向：

1. 定义 case schema 版本
2. 支持 case tags
3. 支持 include/exclude tag
4. 支持 fail-fast
5. 支持 flaky 标记
6. 支持 expected regression risk

### 阶段 4：Compare 能力增强

目标：让版本回归判断更可靠。

增强方向：

1. case-level diff
2. quality dimension diff
3. 新增失败 / 修复失败 / 持续失败分类
4. cost regression 判断
5. safety regression 强阻断
6. 支持 markdown 表格化展示

### 阶段 5：CI/PR 集成

目标：让结果直接进入研发流程。

增强方向：

1. GitHub Actions 示例
2. PR comment markdown
3. artifact 上传
4. release gate preset
5. baseline 自动发现
6. 支持 `--strict`

---

## 12. 关键设计决策

1. `skilldoctor-cli` 作为独立子项目，避免污染 backend 包结构，便于独立安装和 CI 使用。
2. 不直接扩展 `backend/skilldoctor/cli.py`，因为后者主要是后端 orchestration 调试入口。
3. 六维质量评分放在 CLI 层，作为产品化 release gate 能力。
4. 后端继续坚持 rule-first + LLM fallback，保证本地和 CI 结果可复现。

---

## 13. 后续执行原则

后续所有开发按以下原则推进：

1. 先复用 backend，再写 CLI 适配
2. 先保证 JSON schema 稳定，再优化 Markdown
3. 先保证 exit code 正确，再优化 console 体验
4. 先支持本地文件，再考虑远程服务
5. 先 rule-based 稳定判断，再考虑 LLM 增强
6. 每次改动后跑 CLI smoke test
7. 涉及 backend 行为时跑 backend regression test
8. 不新增无必要的文档文件
9. 不复制已有 backend 诊断逻辑
10. 所有报告都要同时服务人和机器

---

## 14. 最终目标状态

开发者可以用：

```bash
skilldoctor diagnose --trace failed.json
```

快速知道：

```text
Skill-owned failure: content_gap
Action: revise_skill
Confidence: 0.92
```

也可以在 CI 中使用：

```bash
skilldoctor bench \
  --cases release-checklist.jsonl \
  --min-pass-rate 0.95

skilldoctor compare \
  --baseline baseline.json \
  --candidate candidate.json \
  --max-regression 0.03
```

从而实现：

```text
如果 Skill 引入失败或质量回归，则自动阻断发布。
```
