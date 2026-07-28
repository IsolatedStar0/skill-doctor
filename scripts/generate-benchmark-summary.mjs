import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { summarizeBenchmarkReports } from "../lib/benchmark-summary.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const benchmarkDir = path.join(root, "reports", "benchmarks");
const outputPath = path.join(root, "reports", "evaluation-summary.json");

const files = (await readdir(benchmarkDir))
  .filter((file) => file.endsWith(".json"))
  .sort();
const reports = await Promise.all(
  files.map(async (file) => JSON.parse(await readFile(path.join(benchmarkDir, file), "utf-8"))),
);
const summary = summarizeBenchmarkReports(reports);

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(summary, null, 2)}\n`, "utf-8");

console.log(
  `Wrote ${path.relative(root, outputPath)} from ${summary.sourceCount} benchmark reports.`,
);

