import { useEffect, useState } from "react";

/** The current time, refreshed every `intervalMs` while `enabled` (for elapsed timers and "x min ago"). */
export function useNow(enabled: boolean = true, intervalMs: number = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [enabled, intervalMs]);
  return now;
}
