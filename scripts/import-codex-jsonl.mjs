import { readFile, writeFile } from "node:fs/promises";
import process from "node:process";
import { codexJsonlToTraceSession } from "../lib/codex-jsonl-adapter.ts";
import { parseTraceSession } from "../lib/trace-adapter.ts";

const [, , jsonlPath, contextPath, outputPath] = process.argv;

if (!jsonlPath || !contextPath) {
  console.error(
    "Usage: npm run codex:import -- <codex.jsonl> <context.json> [trace.json]",
  );
  process.exitCode = 1;
} else {
  const [jsonl, contextText] = await Promise.all([
    readFile(jsonlPath, "utf8"),
    readFile(contextPath, "utf8"),
  ]);
  const trace = codexJsonlToTraceSession(jsonl, JSON.parse(contextText));
  const parsed = parseTraceSession(trace);
  const serialized = `${JSON.stringify(trace, null, 2)}\n`;

  if (outputPath) {
    await writeFile(outputPath, serialized, "utf8");
    console.error(`Trace 1.1 written to ${outputPath}`);
  } else {
    process.stdout.write(serialized);
  }

  console.error(
    `Validated ${parsed.trace.length} steps for ${parsed.id}; fault=${
      parsed.trace.find((step) => step.status === "fault")?.id
    }.`,
  );
}
