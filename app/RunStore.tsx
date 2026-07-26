"use client";

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { LangGraphState } from "../lib/langgraph-stream";

type RunStoreValue = {
  snapshot: LangGraphState | null;
  setSnapshot: (snapshot: LangGraphState | null) => void;
  clearRun: () => void;
};

const RunStoreContext = createContext<RunStoreValue | null>(null);

export function RunStoreProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<LangGraphState | null>(null);
  const value = useMemo(
    () => ({
      snapshot,
      setSnapshot,
      clearRun: () => setSnapshot(null),
    }),
    [snapshot],
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
