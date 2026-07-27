import { spawnSync } from "node:child_process";

const configured = process.env.PYTHON?.trim();
const candidates = configured
  ? [configured]
  : process.platform === "win32"
    ? ["py", "python"]
    : ["python3.11", "python3", "python"];

const args = process.argv.slice(2);
let lastError;

for (const command of candidates) {
  const commandArgs = command === "py" && !configured ? ["-3.11", ...args] : args;
  const result = spawnSync(command, commandArgs, {
    stdio: "inherit",
    shell: false,
  });

  if (!result.error) {
    process.exit(result.status ?? 1);
  }

  if (result.error.code !== "ENOENT") {
    throw result.error;
  }
  lastError = result.error;
}

console.error(
  `Unable to find Python 3.11+. Tried: ${candidates.join(", ")}. ` +
    "Set the PYTHON environment variable to the desired executable.",
);
if (lastError) {
  console.error(lastError.message);
}
process.exit(1);
