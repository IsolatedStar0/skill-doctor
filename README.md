# Skill Doctor Demo

Skill Doctor 是一个完全离线、确定性可复现的 Agent Skill 演化 Demo。

它使用一个固定失败案例演示：

```text
失败 Trace
→ Evidence Snapshot
→ 最早可行动故障步骤
→ 7 类归因
→ Skill 责任链接
→ scoped patch
→ original replay
→ similar-case replay
→ regression
→ ADOPT / REJECT
```

## 环境

- Node.js >= 22.13
- npm

不需要数据库、API Key 或外部服务。

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

- 最早故障步骤为 `step-03`
- 归因类型为 `Content Gap`
- patch 只修改 `procedure` 中的一行
- patch 带有 rollback reference
- 原失败 replay 修复成功
- 同类案例改善且历史成功案例无回归
- 最终发布决策为 `ADOPT`

## 代码结构

```text
app/
  DemoApp.tsx        可交互的四个演示视图
  globals.css        响应式界面样式
lib/
  demo-engine.ts     Trace、归因、修复和验证引擎
tests/
  demo-engine.test.mjs
```

## 可复现案例

任务要求汇总 100 行订单 CSV。旧 Skill 让 Agent 预览前 20 行后“计算关键统计值”，但没有要求重新读取全量数据。Agent 因此只汇总了预览数据。

系统定位 `step-03` 为最早可行动故障，将其归因为 `Content Gap`，并只替换 Skill procedure 的第 3 行：

```diff
- 计算关键统计值并生成摘要。
+ 重新读取完整数据集并计算关键统计值；断言 processed_rows == total_rows 后再生成摘要。
```

候选 patch 修复原失败和四个同类案例，并在四个历史成功案例上保持 100% 通过，因此被采纳。
