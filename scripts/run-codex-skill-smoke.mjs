import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";

const root = resolve(import.meta.dirname, "..");
const manifestPath = join(root, "benchmarks", "codex-smoke", "manifest.json");
const defaultDatasetPath = join(
  root,
  "benchmarks",
  "cache",
  "swe_skills_bench.jsonl",
);

function argument(name) {
  const index = process.argv.indexOf(name);
  return index < 0 ? null : process.argv[index + 1];
}

function parseDataset(text) {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(
          `Dataset line ${index + 1} is invalid JSON: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      }
    });
}

function compileVerifier(testCode) {
  const verifierBase64 = Buffer.from(testCode, "utf8").toString("base64");
  const result = spawnSync(
    process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3.11"),
    [
      "-c",
      "import base64, os; compile(base64.b64decode(os.environ['CODEX_SMOKE_VERIFIER_B64']).decode('utf-8'), '<benchmark-verifier>', 'exec')",
    ],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        CODEX_SMOKE_VERIFIER_B64: verifierBase64,
      },
      windowsHide: true,
    },
  );
  return {
    passed: result.status === 0,
    detail:
      result.status === 0
        ? "Python verifier compiles"
        : (result.stderr || result.error?.message || "compile failed").trim(),
  };
}

function staticChecks(row, gates) {
  const assertions = (row.test_code.match(/\bassert\b/g) ?? []).length;
  const checks = [
    {
      id: "frontmatter",
      passed:
        !gates.requireFrontmatter ||
        /^---\r?\n[\s\S]+?\r?\n---\r?\n/.test(row.skill_document),
      detail: "SKILL.md frontmatter",
    },
    {
      id: "skill-document-size",
      passed:
        row.skill_document.length >= gates.minimumSkillDocumentCharacters,
      detail: `${row.skill_document.length} characters`,
    },
    {
      id: "task-prompt-size",
      passed: row.task_prompt.length >= gates.minimumTaskPromptCharacters,
      detail: `${row.task_prompt.length} characters`,
    },
    {
      id: "verifier-assertions",
      passed:
        !gates.requirePythonVerifierAssertions || assertions > 0,
      detail: `${assertions} assert statements`,
    },
    {
      id: "repository-url",
      passed:
        !gates.requireRepositoryUrl ||
        /^https:\/\/github\.com\/[^/]+\/[^/]+/.test(row.repo_url),
      detail: row.repo_url || "(missing)",
    },
    {
      id: "verifier-syntax",
      ...compileVerifier(row.test_code),
    },
  ];
  return {
    status: checks.every((check) => check.passed) ? "passed" : "failed",
    checks,
  };
}

function finalAgentMessage(jsonl) {
  let latest = null;
  for (const line of jsonl.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const event = JSON.parse(line);
      if (
        event.type === "item.completed" &&
        event.item?.type === "agent_message"
      ) {
        latest = event.item.text;
      }
    } catch {
      // The raw stream remains in the report for adapter-level diagnosis.
    }
  }
  return latest;
}

async function liveProbe(row) {
  const workspace = await mkdtemp(join(tmpdir(), `codex-skill-${row.skill_id}-`));
  const skillDirectory = join(
    workspace,
    ".agents",
    "skills",
    row.skill_id,
  );
  await mkdir(skillDirectory, { recursive: true });
  await writeFile(join(skillDirectory, "SKILL.md"), row.skill_document, "utf8");

  const git = spawnSync("git", ["init", "--quiet", workspace], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (git.status !== 0) {
    return {
      status: "blocked",
      workspace,
      reason: (git.stderr || git.error?.message || "git init failed").trim(),
    };
  }

  const prompt = [
    "This is a read-only Codex Skill integration probe.",
    `Use the installed ${row.skill_id} skill.`,
    "Do not edit files and do not execute the benchmark implementation.",
    "Return a short JSON object with keys skill_id, first_action, and verification_checks.",
    "",
    row.task_prompt,
  ].join("\n");

  const run = spawnSync(
    "codex",
    [
      "exec",
      "--json",
      "--ephemeral",
      "--sandbox",
      "read-only",
      "--cd",
      workspace,
      prompt,
    ],
    {
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
      windowsHide: true,
    },
  );

  if (run.error || run.status !== 0) {
    return {
      status: "blocked",
      workspace,
      exitCode: run.status,
      reason: (
        run.error?.message ||
        run.stderr ||
        "Codex exited without a diagnostic"
      ).trim(),
      jsonl: run.stdout || "",
    };
  }

  const message = finalAgentMessage(run.stdout);
  return {
    status: message ? "completed" : "failed",
    workspace,
    exitCode: run.status,
    finalAgentMessage: message,
    jsonl: run.stdout,
  };
}

const datasetPath =
  argument("--dataset") ||
  process.env.SWE_SKILLS_BENCH_DATASET ||
  defaultDatasetPath;
const reportPath = argument("--report");
const only = argument("--only")?.split(",").filter(Boolean) ?? null;
const live = process.argv.includes("--live");

{
  const [manifest, datasetText] = await Promise.all([
    readFile(manifestPath, "utf8").then(JSON.parse),
    readFile(resolve(datasetPath), "utf8"),
  ]);
  const rows = parseDataset(datasetText);
  const selectedIds = manifest.skills
    .map((skill) => skill.id)
    .filter((id) => !only || only.includes(id));
  const selected = selectedIds.map((id) => {
    const row = rows.find((candidate) => candidate.skill_id === id);
    if (!row) throw new Error(`Dataset does not contain selected skill: ${id}`);
    return row;
  });

  const results = [];
  for (const row of selected) {
    const staticResult = staticChecks(row, manifest.staticGates);
    const probe = live ? await liveProbe(row) : { status: "not-run" };
    results.push({
      skillId: row.skill_id,
      name: row.name,
      type: row.type,
      repository: row.repo_url,
      dockerImage: row.docker_image,
      static: staticResult,
      liveProbe: probe,
    });
  }

  const report = {
    schemaVersion: "1.0",
    generatedAt: new Date().toISOString(),
    mode: live ? "live" : "static",
    dataset: {
      path: resolve(datasetPath),
      sha256: createHash("sha256").update(datasetText).digest("hex"),
      rows: rows.length,
      source: manifest.source,
    },
    summary: {
      selected: results.length,
      staticPassed: results.filter(
        (result) => result.static.status === "passed",
      ).length,
      liveCompleted: results.filter(
        (result) => result.liveProbe.status === "completed",
      ).length,
      liveBlocked: results.filter(
        (result) => result.liveProbe.status === "blocked",
      ).length,
    },
    results,
  };

  const serialized = `${JSON.stringify(report, null, 2)}\n`;
  if (reportPath) {
    const absoluteReportPath = resolve(reportPath);
    await mkdir(dirname(absoluteReportPath), { recursive: true });
    await writeFile(absoluteReportPath, serialized, "utf8");
    console.error(`Report written to ${absoluteReportPath}`);
  }
  process.stdout.write(serialized);
}
