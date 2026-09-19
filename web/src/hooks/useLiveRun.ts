import { useEffect, useState } from "react";
import { socketUrl } from "../api/client";
import type { LiveEvent, LiveSnapshot } from "../api/types";

/** Apply one message from the server to the local picture of the run. */
export function applyEvent(run: LiveSnapshot | null, event: LiveEvent): LiveSnapshot | null {
  if (event.type === "snapshot") return event.run;
  if (!run) return run;
  switch (event.type) {
    case "plan":
      return { ...run, stages: event.stages };
    case "stage": {
      const exists = run.stages.some((s) => s.id === event.stage.id);
      const stages = exists
        ? run.stages.map((s) => (s.id === event.stage.id ? event.stage : s))
        : [...run.stages, event.stage];
      return { ...run, stages };
    }
    case "token":
      return { ...run, text: run.text + event.text };
    case "text_reset":
      return { ...run, text: "" };
    case "status":
      return { ...run, status: event.status, message: event.message };
    default:
      return run;
  }
}

export type LiveConnection = "idle" | "connecting" | "live" | "closed" | "failed";

const MAX_RETRIES = 5;
// A connection that keeps opening and dropping straight away must not make the browser reconnect forever.
const MAX_RECONNECTS = 30;

/**
 * Live progress of a run over a WebSocket: the step being worked on, what comes next, and the answer as
 * it is written. Reconnects if the connection drops. If it cannot connect at all, `connection` becomes
 * "failed" and the page keeps working from its normal polling.
 */
export function useLiveRun(taskId: string, enabled: boolean): { run: LiveSnapshot | null; connection: LiveConnection } {
  const [run, setRun] = useState<LiveSnapshot | null>(null);
  const [connection, setConnection] = useState<LiveConnection>("idle");

  useEffect(() => {
    setRun(null);
    if (!enabled) {
      setConnection("idle");
      return;
    }
    let disposed = false;
    let finished = false;
    let retries = 0;
    let reconnects = 0;
    let timer = 0;
    let socket: WebSocket | null = null;

    const connect = () => {
      setConnection("connecting");
      socket = new WebSocket(socketUrl(`/ws/tasks/${encodeURIComponent(taskId)}`));
      socket.onopen = () => {
        setConnection("live");
      };
      socket.onmessage = (message) => {
        let event: LiveEvent;
        try {
          event = JSON.parse(message.data as string) as LiveEvent;
        } catch {
          return;
        }
        retries = 0; // it is really talking to us, not just opening and dropping
        if (event.type === "ping") return;
        if (event.type === "status" && (event.status === "success" || event.status === "failed")) finished = true;
        setRun((current) => applyEvent(current, event));
      };
      socket.onclose = () => {
        if (disposed || finished) {
          if (!disposed) setConnection("closed");
          return;
        }
        if (retries < MAX_RETRIES && reconnects < MAX_RECONNECTS) {
          retries += 1;
          reconnects += 1;
          timer = window.setTimeout(connect, Math.min(4000, 600 * retries));
        } else {
          setConnection("failed");
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, [taskId, enabled]);

  return { run, connection };
}
