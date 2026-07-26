import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import process from "node:process";
import { Codex } from "@openai/codex-sdk";

function emit(kind, payload) {
  process.stdout.write(`${JSON.stringify({ kind, ...payload })}\n`);
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function git(workspace, args) {
  return spawnSync("git", args, {
    cwd: workspace,
    encoding: "utf8",
    windowsHide: true,
  });
}

function skillDocument(skillId, content) {
  if (content.trimStart().startsWith("---")) return content;
  return [
    "---",
    `name: ${skillId}`,
    "description: Candidate Skill evaluated by Skill Doctor.",
    "---",
    "",
    content,
    "",
  ].join("\n");
}

async function benchmarkContext(projectRoot, skillId) {
  try {
    const [manifest, rows] = await Promise.all([
      readFile(join(projectRoot, "benchmarks", "paired", "manifest.json"), "utf8")
        .then(JSON.parse),
      readFile(
        join(projectRoot, "benchmarks", "cache", "swe_skills_bench.jsonl"),
        "utf8",
      ).then((text) =>
        text
          .split(/\r?\n/)
          .filter(Boolean)
          .map(JSON.parse),
      ),
    ]);
    return {
      probe: manifest.probes.find((item) => item.skillId === skillId) ?? null,
      row: rows.find((item) => item.skill_id === skillId) ?? null,
    };
  } catch {
    return { probe: null, row: null };
  }
}

function usageFromEvents(events) {
  const event = events.findLast((candidate) => candidate.type === "turn.completed");
  if (!event) {
    return {
      input_tokens: 0,
      output_tokens: 0,
      cached_input_tokens: 0,
      reasoning_tokens: 0,
    };
  }
  return {
    input_tokens: event.usage.input_tokens,
    output_tokens: event.usage.output_tokens,
    cached_input_tokens: event.usage.cached_input_tokens,
    reasoning_tokens: event.usage.reasoning_output_tokens,
  };
}

function finalResponse(events) {
  return (
    events
      .filter(
        (event) =>
          event.type === "item.completed" &&
          event.item?.type === "agent_message",
      )
      .at(-1)?.item?.text ?? ""
  );
}

function evaluateAssertions(response, configured) {
  if (!configured?.length) {
    return [
      {
        id: "codex-response",
        source: "task",
        passed: response.trim().length > 0,
        detail: response.trim()
          ? "Codex returned a non-empty final response."
          : "Codex returned no final response.",
      },
    ];
  }
  const haystack = response.toLowerCase();
  return configured.map((assertion) => {
    const candidates = assertion.allOf ?? assertion.anyOf ?? [];
    const matched = candidates.filter((candidate) =>
      haystack.includes(candidate.toLowerCase()),
    );
    const passed = assertion.allOf
      ? matched.length === candidates.length
      : matched.length > 0;
    return {
      id: assertion.id,
      source: assertion.source ?? "system",
      passed,
      detail: passed
        ? `Matched: ${matched.join(", ")}`
        : `Expected ${assertion.allOf ? "all of" : "any of"}: ${candidates.join(", ")}`,
    };
  });
}

async function main() {
  const input = await readStdin();
  const projectRoot = resolve(input.projectRoot);
  const context = await benchmarkContext(projectRoot, input.skillId);
  const task =
    input.task === "Use the target Skill to produce a verified implementation plan." &&
    context.row?.task_prompt
      ? context.row.task_prompt
      : input.task;
  const content =
    input.skillContent ===
      "Inspect the task, execute the required procedure, and verify the result." &&
    context.row?.skill_document
      ? context.row.skill_document
      : input.skillContent;

  const workspace = await mkdtemp(
    join(tmpdir(), `skill-doctor-${input.skillId}-${input.attempt}-`),
  );
  const skillDirectory = join(workspace, ".agents", "skills", input.skillId);
  if (input.condition !== "without_skill") {
    await mkdir(skillDirectory, { recursive: true });
    await writeFile(
      join(skillDirectory, "SKILL.md"),
      skillDocument(input.skillId, content),
      "utf8",
    );
  }
  await writeFile(
    join(workspace, "README.md"),
    "# Skill Doctor isolated Codex execution\n",
    "utf8",
  );
  git(workspace, ["init", "--quiet"]);
  git(workspace, ["config", "user.email", "skill-doctor@local.invalid"]);
  git(workspace, ["config", "user.name", "Skill Doctor"]);
  git(workspace, ["add", "."]);
  git(workspace, ["commit", "--quiet", "-m", "isolated baseline"]);

  const artifactDirectory = join(
    projectRoot,
    "reports",
    "langgraph",
    input.runId,
    `attempt-${input.attempt}`,
  );
  await mkdir(artifactDirectory, { recursive: true });

  const prompt = [
    input.condition === "without_skill"
      ? "Complete the task without access to the target Skill."
      : "Use the installed Skill when it is applicable to the task.",
    "This is an isolated, read-only reliability evaluation.",
    "Do not modify files and do not execute the implementation.",
    "Return a concise implementation plan containing exact constraints and verification requirements.",
    "",
    task,
  ].join("\n");
  const events = [];
  let error = null;
  const started = performance.now();
  try {
    const codex = new Codex();
    const thread = codex.startThread({
      workingDirectory: workspace,
      sandboxMode: "read-only",
      approvalPolicy: "never",
      networkAccessEnabled: false,
      modelReasoningEffort: input.reasoningEffort,
    });
    const streamed = await thread.runStreamed(prompt, {
      signal: AbortSignal.timeout(input.timeoutMs),
    });
    for await (const event of streamed.events) {
      events.push(event);
      emit("event", {
        sequence: events.length,
        occurredAt: new Date().toISOString(),
        event,
      });
      if (event.type === "turn.failed") error = event.error.message;
      if (event.type === "error") error = event.message;
    }
  } catch (caught) {
    error = caught instanceof Error ? caught.message : String(caught);
  }
  const durationMs = Math.round(performance.now() - started);
  const response = finalResponse(events);
  if (
    response &&
    events.some((event) => event.type === "turn.completed")
  ) {
    error = null;
  }
  const assertions = evaluateAssertions(response, context.probe?.assertions);
  const passedCount = assertions.filter((item) => item.passed).length;
  const passRate =
    assertions.length === 0 ? 0 : passedCount / assertions.length;
  const codexJsonl = events.map((event) => JSON.stringify(event)).join("\n");
  const gitDiff = git(workspace, ["diff", "--no-ext-diff", "--"]).stdout ?? "";
  const evidence = {
    schemaVersion: "1.0",
    runId: input.runId,
    attempt: input.attempt,
    executor: "codex-sdk-live",
    skillId: input.skillId,
    threadId:
      events.find((event) => event.type === "thread.started")?.thread_id ?? null,
    durationMs,
    error,
    hashes: {
      codexJsonl: createHash("sha256").update(codexJsonl).digest("hex"),
      finalResponse: createHash("sha256").update(response).digest("hex"),
      gitDiff: createHash("sha256").update(gitDiff).digest("hex"),
    },
    assertions,
  };
  const files = {
    evidenceSnapshot: join(artifactDirectory, "evidence-snapshot.json"),
    codexJsonl: join(artifactDirectory, "codex.jsonl"),
    finalResponse: join(artifactDirectory, "final-response.txt"),
    gitDiff: join(artifactDirectory, "git.diff"),
  };
  await Promise.all([
    writeFile(
      files.evidenceSnapshot,
      `${JSON.stringify(evidence, null, 2)}\n`,
      "utf8",
    ),
    writeFile(files.codexJsonl, `${codexJsonl}\n`, "utf8"),
    writeFile(files.finalResponse, response, "utf8"),
    writeFile(files.gitDiff, gitDiff, "utf8"),
  ]);

  const result = {
    executor: "codex-sdk-live",
    condition:
      input.condition === "without_skill" ||
      input.condition === "with_skill"
        ? input.condition
        : input.attempt === 0
          ? "with_skill"
          : "with_repaired_skill",
    passed: error === null && assertions.every((item) => item.passed),
    pass_rate: passRate,
    duration_ms: durationMs,
    usage: usageFromEvents(events),
    assertions,
    regression_rate: 0,
    summary:
      error === null
        ? `Codex SDK completed ${assertions.length} verifier checks (${passedCount} passed).`
        : "Codex SDK execution failed before completing verification.",
    artifacts: Object.fromEntries(
      Object.entries(files).map(([key, path]) => [
        key,
        relative(projectRoot, path).replaceAll("\\", "/"),
      ]),
    ),
    error,
  };
  emit("result", { result });
}

main().catch((error) => {
  const message =
    error instanceof Error ? error.stack ?? error.message : String(error);
  emit("bridge_error", { error: message });
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
