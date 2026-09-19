import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createTask, type UploadHandle } from "../api/client";
import type { NewTaskInput } from "../api/types";
import { useTasks } from "../context/TasksContext";
import { useToast } from "../context/ToastContext";

/** Start a run, then open it. Returns the upload handle so the caller can show progress or cancel. */
export function useStartTask() {
  const navigate = useNavigate();
  const { refresh } = useTasks();
  const { toast } = useToast();

  return useCallback(
    (input: NewTaskInput, onProgress?: (fraction: number) => void): UploadHandle => {
      const handle = createTask(input, onProgress);
      const promise = handle.promise.then((created) => {
        void refresh();
        toast("Task started", "success");
        navigate(`/task/${created.id}`);
        return created;
      });
      return { promise, abort: handle.abort };
    },
    [navigate, refresh, toast],
  );
}
