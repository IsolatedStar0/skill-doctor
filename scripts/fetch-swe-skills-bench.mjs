import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import process from "node:process";

const source =
  "https://huggingface.co/datasets/GeniusHTX/SWE-Skills-Bench/raw/main/swe_skills_bench.jsonl";
const expectedSha256 =
  "616373200363f5faf1cda6699f8952ee47f6b3a506bfdd8026f3655bf5dc6fe6";
const output = resolve(
  process.argv[2] ??
    join(
      import.meta.dirname,
      "..",
      "benchmarks",
      "cache",
      "swe_skills_bench.jsonl",
    ),
);

async function download() {
  try {
    const response = await fetch(source);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return Buffer.from(await response.arrayBuffer());
  } catch (fetchError) {
    if (process.platform !== "win32") throw fetchError;

    const temporaryDirectory = await mkdtemp(
      join(tmpdir(), "swe-skills-bench-download-"),
    );
    const temporaryFile = join(temporaryDirectory, "dataset.jsonl");
    const fallback = spawnSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$ErrorActionPreference='Stop'; Invoke-WebRequest -Uri $env:SWE_SKILLS_SOURCE -OutFile $env:SWE_SKILLS_OUTPUT",
      ],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          SWE_SKILLS_SOURCE: source,
          SWE_SKILLS_OUTPUT: temporaryFile,
        },
        windowsHide: true,
      },
    );
    if (fallback.status !== 0) {
      throw new Error(
        `Dataset download failed via fetch and PowerShell fallback.\nfetch: ${
          fetchError instanceof Error ? fetchError.message : String(fetchError)
        }\nPowerShell: ${fallback.stderr || fallback.error?.message || "unknown"}`,
      );
    }
    return readFile(temporaryFile);
  }
}

const bytes = await download();
const actualSha256 = createHash("sha256").update(bytes).digest("hex");
if (actualSha256 !== expectedSha256) {
  throw new Error(
    `Dataset checksum changed: expected ${expectedSha256}, got ${actualSha256}. Review the upstream revision before updating the manifest.`,
  );
}

await mkdir(dirname(output), { recursive: true });
await writeFile(output, bytes);
console.log(`Downloaded ${bytes.length} bytes to ${output}`);
console.log(`sha256=${actualSha256}`);
