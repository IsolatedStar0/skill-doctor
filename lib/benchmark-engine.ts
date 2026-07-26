export type BenchmarkCondition = "without_skill" | "with_skill";
export type BenchmarkRunStatus = "completed" | "failed" | "blocked";

export type BenchmarkUsage = {
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number;
  reasoningTokens: number;
  totalTokens: number;
};

export type BenchmarkAssertion = {
  id: string;
  label: string;
  source: "task" | "skill";
  passed: boolean;
  matched: string | null;
};

export type BenchmarkRun = {
  id: string;
  condition: BenchmarkCondition;
  status: BenchmarkRunStatus;
  executor: string;
  taskKind: "knowledge-probe" | "coding-task";
  startedAt: string;
  durationMs: number;
  usage: BenchmarkUsage | null;
  verifier: {
    framework: "pytest";
    passed: number;
    failed: number;
    total: number;
    passRate: number;
    assertions: BenchmarkAssertion[];
  };
  artifacts: {
    evidenceSnapshot: string;
    codexJsonl: string;
    pytestOutput: string;
    gitDiff: string;
  };
  error: string | null;
};

export type PairedBenchmarkResult = {
  skillId: string;
  name: string;
  dimension: string;
  control: BenchmarkRun;
  treatment: BenchmarkRun;
  comparison: {
    outcome: "improved" | "tied" | "regressed" | "incomplete";
    passRateDelta: number | null;
    tokenDelta: number | null;
    tokenOverheadRate: number | null;
    durationDeltaMs: number | null;
    regressionRate: number | null;
    regressedAssertionIds: string[];
  };
};

export type PairedBenchmarkReport = {
  schemaVersion: "1.0";
  runId: string;
  generatedAt: string;
  executor: string;
  taskKind: "knowledge-probe" | "coding-task";
  isModelResult: boolean;
  dataset: {
    name: string;
    sha256: string;
    selectedSkills: string[];
  };
  summary: {
    pairs: number;
    completedPairs: number;
    improved: number;
    tied: number;
    regressed: number;
    averagePassRateDelta: number | null;
    averageTokenOverheadRate: number | null;
    averageDurationDeltaMs: number | null;
    regressionRate: number | null;
  };
  pairs: PairedBenchmarkResult[];
};

function rate(numerator: number, denominator: number) {
  return denominator === 0 ? 0 : numerator / denominator;
}

function average(values: number[]) {
  return values.length === 0
    ? null
    : values.reduce((total, value) => total + value, 0) / values.length;
}

export function compareBenchmarkRuns(
  skillId: string,
  name: string,
  dimension: string,
  control: BenchmarkRun,
  treatment: BenchmarkRun,
): PairedBenchmarkResult {
  if (
    control.condition !== "without_skill" ||
    treatment.condition !== "with_skill"
  ) {
    throw new Error(
      "Paired benchmark requires without_skill control and with_skill treatment.",
    );
  }

  const complete =
    control.status === "completed" && treatment.status === "completed";
  const passRateDelta = complete
    ? treatment.verifier.passRate - control.verifier.passRate
    : null;
  const tokenDelta =
    complete && control.usage && treatment.usage
      ? treatment.usage.totalTokens - control.usage.totalTokens
      : null;
  const tokenOverheadRate =
    tokenDelta !== null && control.usage
      ? rate(tokenDelta, control.usage.totalTokens)
      : null;
  const durationDeltaMs = complete
    ? treatment.durationMs - control.durationMs
    : null;
  const treatmentAssertions = new Map(
    treatment.verifier.assertions.map((assertion) => [
      assertion.id,
      assertion,
    ]),
  );
  const regressedAssertionIds = complete
    ? control.verifier.assertions
        .filter(
          (assertion) =>
            assertion.passed &&
            treatmentAssertions.get(assertion.id)?.passed === false,
        )
        .map((assertion) => assertion.id)
    : [];
  const controlPassed = control.verifier.assertions.filter(
    (assertion) => assertion.passed,
  ).length;
  const regressionRate = complete
    ? rate(regressedAssertionIds.length, controlPassed)
    : null;

  return {
    skillId,
    name,
    dimension,
    control,
    treatment,
    comparison: {
      outcome: !complete
        ? "incomplete"
        : passRateDelta! > 0
          ? "improved"
          : passRateDelta! < 0 || regressedAssertionIds.length > 0
            ? "regressed"
            : "tied",
      passRateDelta,
      tokenDelta,
      tokenOverheadRate,
      durationDeltaMs,
      regressionRate,
      regressedAssertionIds,
    },
  };
}

export function summarizePairedResults(
  pairs: PairedBenchmarkResult[],
): PairedBenchmarkReport["summary"] {
  const completed = pairs.filter(
    (pair) => pair.comparison.outcome !== "incomplete",
  );
  const passRateDeltas = completed
    .map((pair) => pair.comparison.passRateDelta)
    .filter((value): value is number => value !== null);
  const tokenOverheads = completed
    .map((pair) => pair.comparison.tokenOverheadRate)
    .filter((value): value is number => value !== null);
  const durationDeltas = completed
    .map((pair) => pair.comparison.durationDeltaMs)
    .filter((value): value is number => value !== null);
  const regressionNumerator = completed.reduce(
    (total, pair) =>
      total + pair.comparison.regressedAssertionIds.length,
    0,
  );
  const regressionDenominator = completed.reduce(
    (total, pair) =>
      total +
      pair.control.verifier.assertions.filter(
        (assertion) => assertion.passed,
      ).length,
    0,
  );

  return {
    pairs: pairs.length,
    completedPairs: completed.length,
    improved: completed.filter(
      (pair) => pair.comparison.outcome === "improved",
    ).length,
    tied: completed.filter((pair) => pair.comparison.outcome === "tied")
      .length,
    regressed: completed.filter(
      (pair) => pair.comparison.outcome === "regressed",
    ).length,
    averagePassRateDelta: average(passRateDeltas),
    averageTokenOverheadRate: average(tokenOverheads),
    averageDurationDeltaMs: average(durationDeltas),
    regressionRate:
      regressionDenominator === 0
        ? completed.length === 0
          ? null
          : 0
        : regressionNumerator / regressionDenominator,
  };
}
