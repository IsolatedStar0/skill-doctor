import DemoApp from "./DemoApp";
import { RunStoreProvider } from "./RunStore";

export default function Home() {
  return (
    <RunStoreProvider>
      <DemoApp />
    </RunStoreProvider>
  );
}
