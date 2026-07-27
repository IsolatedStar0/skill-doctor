import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  mkdir,
  mkdtemp,
  readFile,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative, resolve } from "node:path";
import process from "node:process";
import { Codex } from "@openai/codex-sdk";
import {
  compareBenchmarkRuns,
  summarizePairedResults,
} from "../lib/benchmark-engine.ts";

const root = resolve(import.meta.dirname, "..");
const datasetPath = join(
  root,
  "benchmarks",
  "cache",
  "swe_skills_bench.jsonl",
);
const manifestPath = join(root, "benchmarks", "paired", "manifest.json");
const publicReportPath = join(
  root,
  "public",
  "benchmarks",
  "latest.json",
);

function argument(name) {
  const index = process.argv.indexOf(name);
  return index < 0 ? null : process.argv[index + 1];
}

function parseDataset(text) {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .map(JSON.parse);
}

function usageFromEvent(event) {
  if (event.type !== "turn.completed") return null;
  const usage = event.usage;
  return {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    cachedInputTokens: usage.cached_input_tokens,
    reasoningTokens: usage.reasoning_output_tokens,
    totalTokens: usage.input_tokens + usage.output_tokens,
  };
}

function finalResponseFromEvents(events) {
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

function evaluateAssertions(response, assertions) {
  const haystack = response.toLowerCase();
  return assertions.map((assertion) => {
    const candidates = assertion.allOf ?? assertion.anyOf ?? [];
    const matches = candidates.filter((candidate) =>
      haystack.includes(candidate.toLowerCase()),
    );
    const passed = assertion.allOf
      ? matches.length === candidates.length
      : matches.length > 0;
    return {
      id: assertion.id,
      label: assertion.label,
      source: assertion.source,
      passed,
      matched: passed ? matches.join(", ") : null,
    };
  });
}

async function runPytestVerifier(workspace, response, assertions) {
  const verifierDirectory = join(workspace, ".benchmark-verifier");
  await mkdir(verifierDirectory, { recursive: true });
  await writeFile(
    join(verifierDirectory, "response.txt"),
    response,
    "utf8",
  );
  await writeFile(
    join(verifierDirectory, "assertions.json"),
    `${JSON.stringify(assertions, null, 2)}\n`,
    "utf8",
  );
  const testCode = [
    "import json",
    "from pathlib import Path",
    "import pytest",
    "",
    "ROOT = Path(__file__).parent",
    "RESPONSE = (ROOT / 'response.txt').read_text(encoding='utf-8').lower()",
    "ASSERTIONS = json.loads((ROOT / 'assertions.json').read_text(encoding='utf-8'))",
    "",
    "@pytest.mark.parametrize('rule', ASSERTIONS, ids=lambda rule: rule['id'])",
    "def test_skill_contract(rule):",
    "    candidates = rule.get('allOf') or rule.get('anyOf') or []",
    "    matches = [item for item in candidates if item.lower() in RESPONSE]",
    "    if rule.get('allOf'):",
    "        assert len(matches) == len(candidates), f\"missing required terms: {set(candidates) - set(matches)}\"",
    "    else:",
    "        assert matches, f\"none of the accepted terms found: {candidates}\"",
    "",
  ].join("\n");
  await writeFile(
    join(verifierDirectory, "test_skill_contract.py"),
    testCode,
    "utf8",
  );
  const result = spawnSync(
    process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3.11"),
    [
      "-m",
      "pytest",
      join(verifierDirectory, "test_skill_contract.py"),
      "-q",
      "--disable-warnings",
    ],
    {
      cwd: workspace,
      encoding: "utf8",
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
      },
    },
  );
  return {
    output: `${result.stdout ?? ""}${result.stderr ?? ""}`,
    exitCode: result.status,
  };
}

function git(workspace, args) {
  return spawnSync("git", args, {
    cwd: workspace,
    encoding: "utf8",
    windowsHide: true,
  });
}

async function prepareWorkspace(row, probe, condition) {
  const workspace = await mkdtemp(
    join(tmpdir(), `skill-pair-${row.skill_id}-${condition}-`),
  );
  await writeFile(
    join(workspace, "README.md"),
    [
      `# ${row.name} paired benchmark`,
      "",
      "This isolated repository is used by the benchmark runner.",
      "",
    ].join("\n"),
    "utf8",
  );

  // Initialize task-specific files
  if (probe.initialFiles) {
    for (const [path, content] of Object.entries(probe.initialFiles)) {
      const fullPath = join(workspace, path);
      await mkdir(dirname(fullPath), { recursive: true });
      await writeFile(fullPath, content, "utf8");
    }
  }

  if (condition === "with_skill") {
    const skillDirectory = join(
      workspace,
      ".agents",
      "skills",
      row.skill_id,
    );
    await mkdir(skillDirectory, { recursive: true });
    await writeFile(
      join(skillDirectory, "SKILL.md"),
      row.skill_document,
      "utf8",
    );
  }
  git(workspace, ["init", "--quiet"]);
  git(workspace, ["config", "user.email", "benchmark@skill-doctor.local"]);
  git(workspace, ["config", "user.name", "Skill Doctor Benchmark"]);
  git(workspace, ["add", "."]);
  git(workspace, ["commit", "--quiet", "-m", "benchmark baseline"]);
  return workspace;
}

function outputSchema() {
  return {
    type: "object",
    properties: {
      skill_id: { type: "string" },
      first_action: { type: "string" },
      implementation_constraints: {
        type: "array",
        items: { type: "string" },
      },
      verification_checks: {
        type: "array",
        items: { type: "string" },
      },
    },
    required: [
      "skill_id",
      "first_action",
      "implementation_constraints",
      "verification_checks",
    ],
    additionalProperties: false,
  };
}

async function runCondition({
  runId,
  row,
  probe,
  condition,
  artifactRoot,
  timeoutMs,
}) {
  const workspace = await prepareWorkspace(row, probe, condition);
  const artifactDirectory = join(
    artifactRoot,
    row.skill_id,
    condition,
  );
  await mkdir(artifactDirectory, { recursive: true });
  const startedAt = new Date().toISOString();
  const started = performance.now();
  const events = [];
  let status = "completed";
  let error = null;
  let usage = null;
  const abort = AbortSignal.timeout(timeoutMs);

  const isRepair = probe.taskKind === "code-repair";
  const taskPrompt = probe.taskPrompt || row.task_prompt;
  const skillContent = probe.skillDocument || row.skill_document;

  const prompt = [
    condition === "with_skill"
      ? "Use the installed Skill when it is applicable. Extract its concrete procedural requirements."
      : "No Skill is installed. Use only the task statement and your baseline knowledge.",
    "This is an isolated reliability evaluation.",
    isRepair
      ? "Modify the files in the workspace to satisfy the task requirements. You can execute commands to verify your work."
      : "Do not inspect unrelated files, do not modify files, and do not execute the implementation. Return the requested JSON only. Keep every checklist item concise but preserve exact filenames, thresholds, components, and verification requirements.",
    "",
    taskPrompt,
  ].join("\n");

  try {
    const codex = new Codex();
    const thread = codex.startThread({
      workingDirectory: workspace,
      sandboxMode: isRepair ? "none" : "read-only",
      approvalPolicy: "never",
    });
    const streamed = await thread.runStreamed(prompt, {
      outputSchema: isRepair ? undefined : outputSchema(),
      signal: abort,
    });
    for await (const event of streamed.events) {
      events.push(event);
      usage = usageFromEvent(event) ?? usage;
      if (event.type === "turn.failed" || event.type === "error") {
        status = "failed";
        error =
          event.type === "turn.failed"
            ? event.error.message
            : event.message;
      }
    }
  } catch (caught) {
    status =
      caught instanceof Error && caught.name === "TimeoutError"
        ? "blocked"
        : "failed";
    error = caught instanceof Error ? caught.message : String(caught);
  }

  const durationMs = Math.round(performance.now() - started);
  const response = finalResponseFromEvents(events);
  if (usage && response) {
    status = "completed";
    error = null;
  }
  const assertions = evaluateAssertions(response, probe.assertions);
  const gitDiff = git(workspace, ["diff", "--no-ext-diff", "--"]).stdout ?? "";

  let pytest;
  if (isRepair && probe.verifierCommand) {
    const [cmd, ...args] = probe.verifierCommand.split(" ");
    const result = spawnSync(cmd, args, {
      cwd: workspace,
      encoding: "utf8",
      windowsHide: true,
      timeout: 60_000,
    });
    pytest = {
      output: [
        `$ ${probe.verifierCommand}`,
        result.stdout,
        result.stderr,
        `Exited with code ${result.status}`,
      ].join("\n"),
      exitCode: result.status,
    };

    // Update assertions based on verifier outcome
    const passed = pytest.output.includes("Exited with code 0");
    const systemAssertion = assertions.find(a => a.id === "pass-pytest");
    if (systemAssertion) {
      systemAssertion.passed = passed;
      systemAssertion.matched = passed ? "Verifier command passed." : "Verifier command failed.";
    }
  } else {
    pytest =
      response === ""
        ? { output: "Verifier skipped because Codex produced no response.\n", exitCode: 1 }
        : await runPytestVerifier(workspace, response, probe.assertions);
  }

  const codexJsonl = events
    .map((event) => JSON.stringify(event))
    .join("\n");
  const evidenceSnapshot = {
    schemaVersion: "1.0",
    runId,
    skillId: row.skill_id,
    condition,
    executor: "codex-sdk",
    taskKind: probe.taskKind || "knowledge-probe",
    startedAt,
    durationMs,
    status,
    error,
    usage,
    hashes: {
      codexJsonl: createHash("sha256").update(codexJsonl).digest("hex"),
      pytestOutput: createHash("sha256")
        .update(pytest.output)
        .digest("hex"),
      gitDiff: createHash("sha256").update(gitDiff).digest("hex"),
    },
    verifier: {
      framework: "pytest",
      exitCode: pytest.exitCode,
      assertions,
    },
  };

  const files = {
    evidenceSnapshot: join(artifactDirectory, "evidence-snapshot.json"),
    codexJsonl: join(artifactDirectory, "codex.jsonl"),
    pytestOutput: join(artifactDirectory, "pytest.txt"),
    gitDiff: join(artifactDirectory, "git.diff"),
  };
  await Promise.all([
    writeFile(
      files.evidenceSnapshot,
      `${JSON.stringify(evidenceSnapshot, null, 2)}\n`,
      "utf8",
    ),
    writeFile(files.codexJsonl, `${codexJsonl}\n`, "utf8"),
    writeFile(files.pytestOutput, pytest.output, "utf8"),
    writeFile(files.gitDiff, gitDiff, "utf8"),
  ]);

  const passed = assertions.filter((assertion) => assertion.passed).length;
  const failed = assertions.length - passed;
  return {
    id: `${runId}:${row.skill_id}:${condition}`,
    condition,
    status,
    executor: "codex-sdk",
    taskKind: probe.taskKind || "knowledge-probe",
    startedAt,
    durationMs,
    usage,
    verifier: {
      framework: "pytest",
      passed,
      failed,
      total: assertions.length,
      passRate: assertions.length === 0 ? 0 : passed / assertions.length,
      assertions,
    },
    artifacts: Object.fromEntries(
      Object.entries(files).map(([key, value]) => [
        key,
        relative(root, value).replaceAll("\\", "/"),
      ]),
    ),
    error,
  };
}

async function loadCompletedRun({
  runId,
  row,
  probe,
  condition,
  artifactRoot,
}) {
  const artifactDirectory = join(
    artifactRoot,
    row.skill_id,
    condition,
  );
  const snapshotPath = join(artifactDirectory, "evidence-snapshot.json");
  try {
    const [snapshot, codexJsonl] = await Promise.all([
      readFile(snapshotPath, "utf8").then(JSON.parse),
      readFile(join(artifactDirectory, "codex.jsonl"), "utf8"),
    ]);
    const events = codexJsonl
      .split(/\r?\n/)
      .filter(Boolean)
      .map(JSON.parse);
    const response = finalResponseFromEvents(events);
    const usage =
      events.map(usageFromEvent).find((value) => value !== null) ??
      snapshot.usage;
    if (!response || !usage) return null;
    const assertions = evaluateAssertions(response, probe.assertions);
    const passed = assertions.filter((assertion) => assertion.passed).length;
    const files = {
      evidenceSnapshot: snapshotPath,
      codexJsonl: join(artifactDirectory, "codex.jsonl"),
      pytestOutput: join(artifactDirectory, "pytest.txt"),
      gitDiff: join(artifactDirectory, "git.diff"),
    };
    if (snapshot.status !== "completed" || snapshot.error !== null) {
      snapshot.status = "completed";
      snapshot.error = null;
      snapshot.usage = usage;
      await writeFile(
        snapshotPath,
        `${JSON.stringify(snapshot, null, 2)}\n`,
        "utf8",
      );
    }
    return {
      id: `${runId}:${row.skill_id}:${condition}`,
      condition,
      status: "completed",
      executor: "codex-sdk",
      taskKind: "knowledge-probe",
      startedAt: snapshot.startedAt,
      durationMs: snapshot.durationMs,
      usage,
      verifier: {
        framework: "pytest",
        passed,
        failed: assertions.length - passed,
        total: assertions.length,
        passRate: assertions.length === 0 ? 0 : passed / assertions.length,
        assertions,
      },
      artifacts: Object.fromEntries(
        Object.entries(files).map(([key, value]) => [
          key,
          relative(root, value).replaceAll("\\", "/"),
        ]),
      ),
      error: null,
    };
  } catch {
    return null;
  }
}

const only = argument("--only")?.split(",").filter(Boolean) ?? null;
const resumePath = argument("--resume");
const timeoutMs = Number(argument("--timeout-ms") ?? 180_000);
if (!Number.isFinite(timeoutMs) || timeoutMs < 10_000) {
  throw new Error("--timeout-ms must be at least 10000.");
}

const [manifest, datasetText] = await Promise.all([
  readFile(manifestPath, "utf8").then(JSON.parse),
  readFile(datasetPath, "utf8"),
]);
const dataset = parseDataset(datasetText);
const selectedProbes = manifest.probes.filter(
  (probe) => !only || only.includes(probe.skillId),
);
const artifactRoot = resumePath
  ? resolve(resumePath)
  : join(
      root,
      "reports",
      "paired",
      `paired-${new Date()
        .toISOString()
        .replaceAll(/[:.]/g, "-")}-${randomUUID().slice(0, 8)}`,
    );
const runId = basename(artifactRoot);
await mkdir(artifactRoot, { recursive: true });
const pairs = [];

for (const probe of selectedProbes) {
  let row = dataset.find((candidate) => candidate.skill_id === probe.skillId);
  if (!row) {
    if (probe.taskPrompt && probe.skillDocument) {
      row = {
        skill_id: probe.skillId,
        name: probe.skillId,
        task_prompt: probe.taskPrompt,
        skill_document: probe.skillDocument
      };
    } else {
      throw new Error(`Dataset is missing ${probe.skillId} and no default prompt/doc provided.`);
    }
  }
  let control = await loadCompletedRun({
    runId,
    row,
    probe,
    condition: "without_skill",
    artifactRoot,
  });
  if (control) {
    console.error(`[${probe.skillId}] reused completed without-Skill run.`);
  } else {
    console.error(`[${probe.skillId}] running without Skill...`);
    control = await runCondition({
      runId,
      row,
      probe,
      condition: "without_skill",
      artifactRoot,
      timeoutMs,
    });
  }
  let treatment = await loadCompletedRun({
    runId,
    row,
    probe,
    condition: "with_skill",
    artifactRoot,
  });
  if (treatment) {
    console.error(`[${probe.skillId}] reused completed with-Skill run.`);
  } else {
    console.error(`[${probe.skillId}] running with Skill...`);
    treatment = await runCondition({
      runId,
      row,
      probe,
      condition: "with_skill",
      artifactRoot,
      timeoutMs,
    });
  }
  pairs.push(
    compareBenchmarkRuns(
      probe.skillId,
      row.name,
      probe.dimension,
      control,
      treatment,
    ),
  );
}

const report = {
  schemaVersion: "1.0",
  runId,
  generatedAt: new Date().toISOString(),
  executor: "codex-sdk",
  taskKind: "knowledge-probe",
  isModelResult: true,
  dataset: {
    name: manifest.dataset,
    sha256: createHash("sha256").update(datasetText).digest("hex"),
    selectedSkills: selectedProbes.map((probe) => probe.skillId),
  },
  summary: summarizePairedResults(pairs),
  pairs,
};

const reportPath = join(artifactRoot, "report.json");
await mkdir(dirname(publicReportPath), { recursive: true });
await Promise.all([
  writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8"),
  writeFile(
    publicReportPath,
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  ),
]);
console.log(JSON.stringify(report, null, 2));
console.error(`Evidence: ${artifactRoot}`);
console.error(`Public report: ${publicReportPath}`);
process.exit(0);
