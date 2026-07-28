import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import DemoApp from "./DemoApp";
import { RunStoreProvider } from "./RunStore";
import "./globals.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RunStoreProvider>
      <DemoApp />
    </RunStoreProvider>
  </StrictMode>,
);
