import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, tasks as api } from "../api/client";
import type { Task } from "../api/types";
import { useTasks } from "../context/TasksContext";

const POLL_MS = 2_000;
const RETRY_MS = 4_000;

export const isActiveStatus = (status: Task["status"]) => status === "queued" || status === "running";

interface TaskState {
  task: Task | null;
  error: string | null;
  notFound: boolean;
  loading: boolean;
}

/** One run's full details; polls while it is queued or running. */
export function useTask(id: string): TaskState & { reload: () => void } {
  const { refresh } = useTasks();
  const [state, setState] = useState<TaskState>({ task: null, error: null, notFound: false, loading: true });
  const wasActive = useRef(false);
  const generation = useRef(0);
  const timer = useRef(0);

  const load = useCallback(async () => {
    const mine = generation.current;
    window.clearTimeout(timer.current);
    try {
      const task = await api.get(id);
      if (mine !== generation.current) return;
      setState({ task, error: null, notFound: false, loading: false });
      const active = isActiveStatus(task.status);
      if (active) {
        timer.current = window.setTimeout(load, POLL_MS);
      } else if (wasActive.current) {
        void refresh(); // it just finished: update the history list too
      }
      wasActive.current = active;
    } catch (err) {
      if (mine !== generation.current) return;
      if (err instanceof ApiError && err.status === 404) {
        setState({ task: null, error: null, notFound: true, loading: false });
        return;
      }
      setState((s) => ({ ...s, error: err instanceof Error ? err.message : "Could not load this run.", loading: false }));
      timer.current = window.setTimeout(load, RETRY_MS);
    }
  }, [id, refresh]);

  useEffect(() => {
    generation.current += 1;
    wasActive.current = false;
    setState({ task: null, error: null, notFound: false, loading: true });
    void load();
    return () => {
      generation.current += 1;
      window.clearTimeout(timer.current);
    };
  }, [id, load]);

  return { ...state, reload: () => void load() };
}
