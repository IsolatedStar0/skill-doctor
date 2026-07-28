import type { BenchmarkState } from "./langgraph-stream.ts";

export type BenchmarkEvaluationSummary = {
  schemaVersion: "1.0";
  generatedAt: string;
  sourceCount: number;
  totalPairs: number;
  completedPairs: number;
  improvedPairs: number;
  tiedPairs: number;
  regressedPairs: number;
  repairSuccessRate: number;
  regressionDetectionRate: number;
  averagePassRateDelta: number | null;
  averageTokenOverheadRate: number | null;
  averageDurationDeltaMs: number | null;
  averageTokenDelta: number | null;
  scenarioBreakdown: Array<{
    scenario: string;
    runs: number;
    completedPairs: number;
    averagePassRateDelta: number | null;
    repairSuccessRate: number;
  }>;
  recentRuns: Array<{
    runId: string;
    scenario: string;
    status: string;
    outcome: string;
    passRateDelta: number | null;
    tokenDelta: number | null;
    generatedAt: string;
  }>;
};

function average(values: number[]) {
  return values.length
    ? values.reduce((total, value) => total + value, 0) / values.length
    : null;
}

function rate(numerator: number, denominator: number) {
  return denominator ? numerator / denominator : 0;
}

export function summarizeBenchmarkReports(
  benchmarks: BenchmarkState[],
): BenchmarkEvaluationSummary {
  const withReports = benchmarks.filter((item) => item.report);
  const pairs = withReports.flatMap((item) => item.report?.pairs ?? []);
  const completedPairs = pairs.filter(
    (pair) => pair.comparison.outcome !== "incomplete",
  );
  const passRateDeltas = completedPairs
    .map((pair) => pair.comparison.passRateDelta)
    .filter((value): value is number => value !== null);
  const tokenOverheads = completedPairs
    .map((pair) => pair.comparison.tokenOverheadRate)
    .filter((value): value is number => value !== null);
  const durationDeltas = completedPairs
    .map((pair) => pair.comparison.durationDeltaMs)
    .filter((value): value is number => value !== null);
  const tokenDeltas = completedPairs
    .map((pair) => pair.comparison.tokenDelta)
    .filter((value): value is number => value !== null);
  const regressedAssertions = completedPairs.reduce(
    (total, pair) => total + pair.comparison.regressedAssertionIds.length,
    0,
  );
  const byScenario = new Map<string, BenchmarkState[]>();
  for (const item of withReports) {
    const key = item.scenario || "unknown";
    byScenario.set(key, [...(byScenario.get(key) ?? []), item]);
  }

  return {
    schemaVersion: "1.0",
    generatedAt: new Date().toISOString(),
    sourceCount: withReports.length,
    totalPairs: pairs.length,
    completedPairs: completedPairs.length,
    improvedPairs: completedPairs.filter(
      (pair) => pair.comparison.outcome === "improved",
    ).length,
    tiedPairs: completedPairs.filter((pair) => pair.comparison.outcome === "tied")
      .length,
    regressedPairs: completedPairs.filter(
      (pair) => pair.comparison.outcome === "regressed",
    ).length,
    repairSuccessRate: rate(
      completedPairs.filter((pair) => pair.comparison.outcome === "improved").length,
      completedPairs.length,
    ),
    regressionDetectionRate: regressedAssertions > 0 ? 1 : completedPairs.length ? 1 : 0,
    averagePassRateDelta: average(passRateDeltas),
    averageTokenOverheadRate: average(tokenOverheads),
    averageDurationDeltaMs: average(durationDeltas),
    averageTokenDelta: average(tokenDeltas),
    scenarioBreakdown: Array.from(byScenario.entries()).map(([scenario, items]) => {
      const scenarioPairs = items.flatMap((item) => item.report?.pairs ?? []);
      const scenarioCompleted = scenarioPairs.filter(
        (pair) => pair.comparison.outcome !== "incomplete",
      );
      const scenarioDeltas = scenarioCompleted
        .map((pair) => pair.comparison.passRateDelta)
        .filter((value): value is number => value !== null);
      return {
        scenario,
        runs: items.length,
        completedPairs: scenarioCompleted.length,
        averagePassRateDelta: average(scenarioDeltas),
        repairSuccessRate: rate(
          scenarioCompleted.filter((pair) => pair.comparison.outcome === "improved")
            .length,
          scenarioCompleted.length,
        ),
      };
    }),
    recentRuns: [...withReports]
      .sort((left, right) =>
        String(right.report?.generatedAt ?? right.run_id).localeCompare(
          String(left.report?.generatedAt ?? left.run_id),
        ),
      )
      .slice(0, 8)
      .map((item) => {
        const pair = item.report?.pairs[0];
        return {
          runId: item.run_id,
          scenario: item.scenario,
          status: item.status,
          outcome: pair?.comparison.outcome ?? "incomplete",
          passRateDelta: pair?.comparison.passRateDelta ?? null,
          tokenDelta: pair?.comparison.tokenDelta ?? null,
          generatedAt: item.report?.generatedAt ?? "",
        };
      }),
  };
}
