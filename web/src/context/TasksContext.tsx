import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { tasks as api } from "../api/client";
import type { TaskSummary } from "../api/types";

interface TasksValue {
  tasks: TaskSummary[];
  loaded: boolean;
  refresh: () => Promise<void>;
  remove: (id: string) => Promise<void>;
  clearFinished: () => Promise<number>;
}

const TasksContext = createContext<TasksValue | null>(null);

const IDLE_REFRESH_MS = 30_000;
const ACTIVE_REFRESH_MS = 4_000;

const isActive = (t: TaskSummary) => t.status === "queued" || t.status === "running";

/** The signed-in user's run history, shared by the sidebar and the pages. */
export function TasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const latest = useRef<TaskSummary[]>([]);

  const refresh = useCallback(async () => {
    try {
      const next = await api.list(100);
      latest.current = next;
      setTasks(next);
    } catch {
      /* keep what we have; the status pill shows connectivity problems */
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    let timer = 0;
    let stopped = false;
    const loop = async () => {
      await refresh();
      if (stopped) return;
      const delay = latest.current.some(isActive) ? ACTIVE_REFRESH_MS : IDLE_REFRESH_MS;
      timer = window.setTimeout(loop, delay);
    };
    void loop();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [refresh]);

  const remove = useCallback(
    async (id: string) => {
      await api.remove(id);
      await refresh();
    },
    [refresh],
  );

  const clearFinished = useCallback(async () => {
    const { deleted } = await api.clearFinished();
    await refresh();
    return deleted;
  }, [refresh]);

  const value = useMemo(
    () => ({ tasks, loaded, refresh, remove, clearFinished }),
    [tasks, loaded, refresh, remove, clearFinished],
  );
  return <TasksContext.Provider value={value}>{children}</TasksContext.Provider>;
}

export function useTasks(): TasksValue {
  const value = useContext(TasksContext);
  if (!value) throw new Error("useTasks must be used inside TasksProvider");
  return value;
}
