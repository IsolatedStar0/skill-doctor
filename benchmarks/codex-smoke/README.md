# Codex Skill smoke suite

这个套件用于验证三件事：

1. 开源 Skill 数据能被稳定读取，文档、任务、pytest verifier 和目标仓库信息完整。
2. Skill 能以 Codex 支持的 `.agents/skills/<id>/SKILL.md` 结构装入临时 Git 工作区。
3. 使用 `codex exec --json` 运行后，原始 JSONL 可以进入 `Codex JSONL -> Trace 1.1` 适配链。

默认模式只运行离线静态门禁，不调用模型：

```bash
npm run bench:skills -- --dataset /path/to/swe_skills_bench.jsonl
```

真实 Codex probe：

```bash
npm run bench:skills -- --dataset /path/to/swe_skills_bench.jsonl --live
```

数据集下载地址：

```text
https://huggingface.co/datasets/GeniusHTX/SWE-Skills-Bench/raw/main/swe_skills_bench.jsonl
```

这只是接入 smoke test，不等价于 SWE-Skills-Bench 的完整能力评分。完整复现实验需要固定目标仓库、Docker 镜像、测试执行，以及 `with-skill` / `without-skill` 配对对照。
