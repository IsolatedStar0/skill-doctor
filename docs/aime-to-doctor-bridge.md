# Aime → skill-doctor 打通说明

> 目标：**Aime 执行真实 skill → 自动推完整 trace → DeepSeek 真实分析 → 前端展示 agent 生成的结论**。
> 本次改动已经把链路上原本"空转"的两处堵点打通，本文只讲怎么用 + 关键改动点。

## 一、一次完整调用

前后端已经在跑（uvicorn 8010 + vinext 3000），`.env` 中的 `DEEPSEEK_API_KEY` / `SKILL_DOCTOR_INGEST_API_KEY` 均已生效。任意时候只要 Aime 侧跑完一次 skill，把 trace 写成 JSON，就可以：

```bash
cd /Users/bytedance/Projects/skill-doctor
python3 scripts/push_aime_trace.py \
    --from-file examples/traces/puck-rule-rca-live.json
```

预期输出（示例，DeepSeek 每次生成不同）：

```
=== skill-doctor snapshot ===
run_id     : lg-ad92f42b91ca
skill_id   : puck-rule-rca
status     : failed
execution  : passed=False pass_rate=0.75 runtime_events=13 assertions=4
attribution: taxonomy=Content Gap cause=skill source=llm fault_type=skill_wrong
--- 🤖 AI 归因结论 ---
The skill's confidence threshold should be lowered to 0.60 for S-level
campaign first-day scenarios, or the noise judgment logic should incorporate
calendar-based uplift ranges to produce confidence >= 0.75.
--- 归因理由 ---
The puck-rule-rca skill correctly identified the anomaly as noise
(rca_filter=true) with confidence 0.64 ...
```

关键校验点：
- `source=llm` → **DeepSeek 真实被调用**（`rule-based` 表示回退到模板）。
- `agent_conclusion` / `agent_reason` 是 DeepSeek 的原文，不再是硬编码的 `"Execution checks failed (code bug or missing constraints)."`。

同一 run 会通过 `GET /runs/events` SSE 广播到前端，页面顶部 **ATTRIBUTION** 面板会显示带绿色边框的「🤖 AI 归因结论 · DeepSeek」卡片。

## 二、Aime → skill-doctor 的桥接姿势

`scripts/push_aime_trace.py` 是唯一入口，接收一份符合 `TraceIngestRequest`（`backend/skilldoctor/models.py`）的 JSON。最小可用字段：

```jsonc
{
  "task":         "<用户 query>",
  "skill_id":     "<aime skill id>",
  "skill_version":"1.0.0",
  "skill_content":"<skill 原文，DeepSeek 会读它进行归因>",

  // 至少填一个，否则脚本会拒收（避免再次出现空 trace 问题）
  "runtime_events": [ { "stage": "...", "status": "completed|failed", "message": "..." } ],
  "tool_calls":     [ { "name": "...", "status": "completed|failed", "arguments": {} } ],
  "model_messages": [ { "role": "assistant|user|system|tool", "content": "..." } ],
  "trace_metadata": { "aime_session": "...", "aime_assistant": "..." }
}
```

样例见 `examples/traces/puck-rule-rca-live.json`，完全基于真实 puck-rule-rca 语义构造：dispatch → fetch_rule_metric_info → fetch_timeseries → calendar_check → noise_judge → write_conclusion。

推荐的自动化姿势：
1. **在你 Aime 侧的 skill 运行结束回调里**，把 trajectory 落成上述 JSON（可以直接 `json.dump(sys.stdout)` 到临时文件）。
2. 然后执行 `python3 scripts/push_aime_trace.py --from-file /tmp/aime_trace_<run_id>.json`。
3. 打开 http://localhost:3000 就能实时看到 DeepSeek 分析结果。

如果暂时不想改 Aime 侧代码，也可以手动把 puck 派单里保存的 rca_content / rca_detail、加上 tool 调用列表拼一份 JSON，效果一样。

## 方案 A：Aime skill on_finish 直连 HTTP 推送

如果 Aime 侧希望**跳过写临时 JSON 文件**、由 skill 执行结束回调直接 HTTP POST 一次，就用 `scripts/aime_skill_hook.py`。这是 `push_aime_trace.py` 的 in-process 版本：只依赖 stdlib（`urllib.request` / `json` / `os`），可以直接 import 到 Aime skill 的 `on_finish` 回调里。

关键特性：
- **纯 stdlib**：无第三方依赖，Aime sandbox 直接可用。
- **绝不抛异常**：桥接失败只打 stderr 日志，不阻塞 skill 主流程。
- **空 trace 自动丢弃**：`runtime_events` / `tool_calls` / `model_messages` 全空时直接跳过，避免污染 run 列表。
- **自动读 `.env`**：不传 `endpoint` / `api_key` 时，会依次尝试参数 → 环境变量 → `.env`。

最小接入示例：

```python
from scripts.aime_skill_hook import push_to_skill_doctor

def on_finish(ctx):
    push_to_skill_doctor(
        skill_id       = ctx.skill_id,
        skill_content  = ctx.skill_body,           # 原文，DeepSeek 会读它
        runtime_events = ctx.runtime_events,       # [{stage,status,message}, ...]
        tool_calls     = ctx.tool_calls,           # [{name,status,arguments,result}, ...]
        model_messages = ctx.model_messages,       # [{role,content}, ...]
        business_result= ctx.final_output,         # 会挂到 trace_metadata.business_result
        task           = ctx.user_query,
        trace_metadata = {"aime_session": ctx.session_id, "aime_assistant": ctx.assistant_id},
        # endpoint / api_key 可省略，默认取 .env
    )
```

冒烟测试（后端跑在 8010、`.env` 已配置）：

```bash
cd /Users/bytedance/Projects/skill-doctor
python3 scripts/aime_skill_hook.py
```

预期 stderr/stdout 里能看到：

```
[skill-doctor] pushed ok — run_id=lg-xxxxxxxx status=... attribution.source=llm ...
[skill-doctor] 🤖 <DeepSeek 结论>
```

如果打印 `skip: ... are all empty`，说明本次 skill 没有可诊断的执行痕迹，属于预期行为。

## 三、这次到底改了什么

| 位置 | 改动 | 说明 |
| --- | --- | --- |
| `backend/skilldoctor/models.py` | `AttributionResult` 新增 `agent_conclusion` / `agent_reason` / `agent_source` | DeepSeek 输出以结构化字段透出，不再被埋在 `improvement_principle` 里 |
| `backend/skilldoctor/adaptor.py` | `LocalizedFault.source`、`Localizer.localize(..., force_llm=True)` | 允许对健康 trace 也强制调 DeepSeek；调用一次就打一条 log 便于排查 |
| `backend/skilldoctor/graph.py` | `attribute()` 优先用 LLM 文案覆盖 `explanation`；`route_after_evidence` 对 `executor=="aime-skill-trace"` 强制走 `attribute` | 上传的 Aime trace 无论通过与否都会经过 DeepSeek；rule-based 输出仅作为 fallback |
| `lib/langgraph-stream.ts` | 新字段进入前端类型 | 编译期防止字段被静默丢失 |
| `app/LangGraphDashboard.tsx` | ATTRIBUTION 面板显示「🤖 AI 归因结论」绿色卡片 + 归因理由 + `fault_type` / `t*` / `source` | 用户可以一眼分辨这是 DeepSeek 生成的还是规则模板兜底的 |
| `scripts/push_aime_trace.py` | 新增桥接脚本，拒收空 payload；自动读取 `.env` 里的 ingest token | 保证不再退化回"POST 进去 trace_data 为空"的旧状态 |
| `examples/traces/puck-rule-rca-live.json` | 真实 puck-rule-rca 语义的样例 trace | 用于回归验证与教学 |

## 四、常见坑

- **说 `source=rule-based`？** 大概率是 uvicorn 没有 `DEEPSEEK_API_KEY`。用下面的方式重启：
  ```bash
  kill $(pgrep -f "uvicorn skilldoctor.api:app")
  cd /Users/bytedance/Projects/skill-doctor/backend
  set -a; . /Users/bytedance/Projects/skill-doctor/.env; set +a
  nohup python3 -m uvicorn skilldoctor.api:app --host 0.0.0.0 --port 8010 \
      > /tmp/skill-doctor-uvicorn.log 2>&1 &
  ```
- **401 Unauthorized？** `.env` 里的 `SKILL_DOCTOR_INGEST_API_KEY` 一定要透传给脚本，脚本已经会自动读 `.env`，但也可以显式 `--api-key`。
- **前端看不到卡片？** vinext dev 通常热更新，如果没触发就手动改一下 `LangGraphDashboard.tsx` 保存一次；页面顶部左侧 Run 列表点最新那条即可。
