"use client";

import {
  useCallback,
  createContext,
  useEffect,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  getBenchmarkRun,
  getAgentRun,
  listAgentRuns,
  subscribeAgentRuns,
  type BenchmarkState,
  type LangGraphState,
  type RunSummary,
} from "../lib/langgraph-stream";

type RunStoreValue = {
  snapshot: LangGraphState | null;
  setSnapshot: (snapshot: LangGraphState | null) => void;
  clearRun: () => void;
  benchmarkSnapshot: BenchmarkState | null;
  setBenchmarkSnapshot: (snapshot: BenchmarkState | null) => void;
  runs: RunSummary[];
  registryStatus: "connecting" | "connected" | "reconnecting";
  selectRun: (
    runId: string,
    runKind?: "agent" | "benchmark",
  ) => Promise<void>;
};

const RunStoreContext = createContext<RunStoreValue | null>(null);

export function RunStoreProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<LangGraphState | null>(null);
  const [benchmarkSnapshot, setBenchmarkSnapshot] =
    useState<BenchmarkState | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [registryStatus, setRegistryStatus] =
    useState<RunStoreValue["registryStatus"]>("connecting");

  const selectRun = useCallback(async (
    runId: string,
    runKind: "agent" | "benchmark" = "agent",
  ) => {
    try {
      if (runKind === "benchmark") {
        setBenchmarkSnapshot(await getBenchmarkRun(runId));
      } else {
        setSnapshot(await getAgentRun(runId));
      }
    } catch {
      setRegistryStatus("reconnecting");
    }
  }, []);

  useEffect(() => {
    let active = true;
    void listAgentRuns()
      .then(async (items) => {
        if (!active) return;
        setRuns(items);
        const latestAgent = items.find(
          (item) => item.run_kind !== "benchmark",
        );
        const latestBenchmark = items.find(
          (item) => item.run_kind === "benchmark",
        );
        if (latestAgent) {
          const state = await getAgentRun(latestAgent.run_id);
          if (active) setSnapshot((current) => current ?? state);
        }
        if (latestBenchmark) {
          const state = await getBenchmarkRun(latestBenchmark.run_id);
          if (active) {
            setBenchmarkSnapshot((current) => current ?? state);
          }
        }
      })
      .catch(() => {
        if (active) setRegistryStatus("reconnecting");
      });

    const unsubscribe = subscribeAgentRuns(
      (event) => {
        if (!active) return;
        const state = event.state;
        const summary: RunSummary = {
          run_kind: state.run_kind ?? "agent",
          run_id: state.run_id,
          parent_run_id: state.parent_run_id ?? null,
          skill_id: state.skill_id,
          skill_version: state.skill_version,
          executor: state.executor,
          scenario: state.scenario,
          condition: state.condition ?? "standard",
          attempt: state.attempt,
          max_attempts: state.max_attempts,
          status: state.status,
          stop_reason: state.stop_reason,
          event_count: state.events.length,
          updated_at: event.updated_at,
        };
        setRuns((current) =>
          [summary, ...current.filter((item) => item.run_id !== state.run_id)]
            .sort((left, right) =>
              right.updated_at.localeCompare(left.updated_at),
            )
            .slice(0, 50),
        );
        if (state.run_kind === "benchmark") {
          setBenchmarkSnapshot((current) =>
            current === null || current.run_id === state.run_id
              ? state
              : current,
          );
          return;
        }
        setSnapshot((current) =>
          current === null || current.run_id === state.run_id
            ? state
            : current,
        );
      },
      setRegistryStatus,
    );

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const value = useMemo(
    () => ({
      snapshot,
      setSnapshot,
      clearRun: () => setSnapshot(null),
      benchmarkSnapshot,
      setBenchmarkSnapshot,
      runs,
      registryStatus,
      selectRun,
    }),
    [benchmarkSnapshot, registryStatus, runs, selectRun, snapshot],
  );

  return (
    <RunStoreContext.Provider value={value}>
      {children}
    </RunStoreContext.Provider>
  );
}

export function useRunStore() {
  const value = useContext(RunStoreContext);
  if (!value) {
    throw new Error("useRunStore must be used inside RunStoreProvider.");
  }
  return value;
}
