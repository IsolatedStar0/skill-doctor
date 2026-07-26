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
  getAgentRun,
  listAgentRuns,
  subscribeAgentRuns,
  type LangGraphState,
  type RunSummary,
} from "../lib/langgraph-stream";

type RunStoreValue = {
  snapshot: LangGraphState | null;
  setSnapshot: (snapshot: LangGraphState | null) => void;
  clearRun: () => void;
  runs: RunSummary[];
  registryStatus: "connecting" | "connected" | "reconnecting";
  selectRun: (runId: string) => Promise<void>;
};

const RunStoreContext = createContext<RunStoreValue | null>(null);

export function RunStoreProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<LangGraphState | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [registryStatus, setRegistryStatus] =
    useState<RunStoreValue["registryStatus"]>("connecting");

  const selectRun = useCallback(async (runId: string) => {
    try {
      const state = await getAgentRun(runId);
      setSnapshot(state);
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
        if (items[0]) {
          const state = await getAgentRun(items[0].run_id);
          if (active) setSnapshot((current) => current ?? state);
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
          run_id: state.run_id,
          skill_id: state.skill_id,
          skill_version: state.skill_version,
          executor: state.executor,
          scenario: state.scenario,
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
      runs,
      registryStatus,
      selectRun,
    }),
    [registryStatus, runs, selectRun, snapshot],
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
