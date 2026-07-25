import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { analyzeCase } from "../lib/demo-engine.ts";
import {
  parseTraceSession,
  TraceValidationError,
} from "../lib/trace-adapter.ts";

const [, , inputPath] = process.argv;

if (!inputPath) {
  console.error(
    "Usage: npm run analyze -- examples/traces/content-gap.json",
  );
  process.exitCode = 2;
} else {
  try {
    const absolutePath = resolve(process.cwd(), inputPath);
    const payload = JSON.parse(await readFile(absolutePath, "utf8"));
    const result = analyzeCase(parseTraceSession(payload));
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    if (error instanceof TraceValidationError) {
      console.error(error.message);
      process.exitCode = 3;
    } else {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    }
  }
}
