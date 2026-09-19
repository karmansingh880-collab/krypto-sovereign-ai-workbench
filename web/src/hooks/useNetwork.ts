import { useCallback, useEffect, useState } from "react";
import { socketUrl, system } from "../api/client";
import type { NetworkStatus } from "../api/types";

const POLL_MS = 10_000;

export interface NetworkState {
  status: NetworkStatus | null;
  /** Updates are arriving over the WebSocket (otherwise the page polls every few seconds). */
  live: boolean;
  /** What the browser itself reports (it also fires an event the moment the connection drops). */
  browserOnline: boolean;
  refresh: () => Promise<void>;
}

/** Is this computer online? Pushed over a WebSocket when it changes, with polling as a fallback. */
export function useNetwork(): NetworkState {
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [live, setLive] = useState(false);
  const [browserOnline, setBrowserOnline] = useState(() => navigator.onLine);

  const refresh = useCallback(async () => {
    try {
      setStatus(await system.network(true));
    } catch {
      /* the backend itself is unreachable: the services indicator says so */
    }
  }, []);

  // WebSocket, reconnecting with a short backoff.
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let timer = 0;
    let attempt = 0;

    const connect = () => {
      socket = new WebSocket(socketUrl("/ws/system"));
      socket.onopen = () => {
        attempt = 0;
        setLive(true);
      };
      socket.onmessage = (message) => {
        try {
          const data = JSON.parse(message.data as string) as { type: string; network?: NetworkStatus };
          if (data.type === "network" && data.network) setStatus(data.network);
        } catch {
          /* ignore malformed messages */
        }
      };
      socket.onclose = () => {
        setLive(false);
        if (!disposed) {
          attempt += 1;
          timer = window.setTimeout(connect, Math.min(15_000, 1000 * attempt));
        }
      };
    };
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, []);

  // Polling fallback while the WebSocket is not connected (and the first value before it connects).
  useEffect(() => {
    if (live) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await system.network();
        if (!stopped) setStatus(next);
      } catch {
        /* try again later */
      }
      if (!stopped) timer = window.setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [live]);

  // The browser knows immediately when the connection drops or returns.
  useEffect(() => {
    const online = () => {
      setBrowserOnline(true);
      void refresh();
    };
    const offline = () => {
      setBrowserOnline(false);
      void refresh();
    };
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    return () => {
      window.removeEventListener("online", online);
      window.removeEventListener("offline", offline);
    };
  }, [refresh]);

  return { status, live, browserOnline, refresh };
}
