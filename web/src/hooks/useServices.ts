import { useEffect, useRef, useState } from "react";
import { system } from "../api/client";
import type { SystemStatus } from "../api/types";

const REFRESH_MS = 15_000;

export interface ServicesState {
  status: SystemStatus | null;
  unreachable: boolean;
}

export function useServices(): ServicesState {
  const [state, setState] = useState<ServicesState>({ status: null, unreachable: false });
  const badChecks = useRef(0);

  useEffect(() => {
    let timer = 0;
    let stopped = false;
    // While the model is working the CPU is saturated and a check can be slow or time out. A problem is
    // only shown once it has been seen twice in a row (the second look comes sooner than usual).
    const poll = async () => {
      let retrySoon = false;
      try {
        const status = await system.status();
        const problems = problemCount({ status, unreachable: false });
        badChecks.current = problems > 0 ? badChecks.current + 1 : 0;
        if (!stopped && (problems === 0 || badChecks.current >= 2)) setState({ status, unreachable: false });
        retrySoon = problems > 0 && badChecks.current < 2;
      } catch {
        badChecks.current += 1;
        if (!stopped && badChecks.current >= 2) setState((s) => ({ ...s, unreachable: true }));
        retrySoon = badChecks.current < 2;
      }
      if (!stopped) timer = window.setTimeout(poll, retrySoon ? 3_000 : REFRESH_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, []);

  return state;
}

/** How many required services or models are down (0 = all ready). */
export function problemCount(state: ServicesState): number {
  if (state.unreachable || !state.status) return state.unreachable ? 1 : 0;
  const { mongo, qdrant, ollama } = state.status;
  const missingModels = Object.values(ollama.models ?? {}).filter((present) => !present).length;
  return [mongo.up, qdrant.up, ollama.up].filter((up) => !up).length + missingModels;
}
