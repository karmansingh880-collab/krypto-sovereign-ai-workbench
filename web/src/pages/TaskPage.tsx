import { useNavigate, useParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { AlertTriangle, Loader2 } from "lucide-react";
import { LivePanel } from "../components/LivePanel";
import { ResultView } from "../components/ResultView";
import type { ComposerPreset } from "../components/Composer";
import { useConfirm } from "../context/ConfirmContext";
import { useTasks } from "../context/TasksContext";
import { useToast } from "../context/ToastContext";
import { useStartTask } from "../hooks/useStartTask";
import { useLiveRun } from "../hooks/useLiveRun";
import { isActiveStatus, useTask } from "../hooks/useTask";

export function TaskPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { task, error, notFound, loading, reload } = useTask(id);
  const { remove } = useTasks();
  const { toast } = useToast();
  const confirm = useConfirm();
  const start = useStartTask();
  // Watch the run live while it is queued or running (finished runs show the timeline saved with them).
  const watching = task !== null && isActiveStatus(task.status);
  const { run, connection } = useLiveRun(id, watching);

  if (notFound) {
    return (
      <div className="card empty" role="alert">
        <AlertTriangle size={28} aria-hidden />
        <h2>Run not found</h2>
        <p className="muted">It may have been deleted, or it belongs to another account.</p>
        <Link className="btn primary" to="/">Start a new task</Link>
      </div>
    );
  }
  if (!task) {
    return (
      <div className="card empty" aria-busy={loading}>
        {error ? (
          <>
            <AlertTriangle size={28} aria-hidden />
            <p role="alert">{error}</p>
          </>
        ) : (
          <>
            <Loader2 size={26} className="spin" aria-hidden />
            <p className="muted">Loading run…</p>
          </>
        )}
      </div>
    );
  }

  // "Run again" resubmits instantly, which only makes sense when no uploaded file is involved.
  const canRerun = task.files.length === 0 || task.files[0].endsWith("(sample)");
  const onRerun = canRerun
    ? () => {
        const handle = start({
          goal: task.goal,
          source: task.files.length ? "sample" : "none",
          files: [],
          formats: task.formats,
        });
        handle.promise.catch((err: unknown) => toast(err instanceof Error ? err.message : "Could not start the task.", "error"));
      }
    : undefined;

  const onReuse = () => {
    const preset: ComposerPreset = {
      goal: task.goal,
      formats: task.formats,
      source: task.files.length === 0 ? "none" : task.files[0].endsWith("(sample)") ? "sample" : "upload",
    };
    navigate("/", { state: preset });
  };

  const onDelete = async () => {
    const ok = await confirm({
      title: "Delete this run?",
      message: "The run and the files it created will be permanently deleted.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await remove(task.id);
      navigate("/");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not delete the run.", "error");
    }
  };

  return (
    <div className="task-layout">
      <ResultView
        task={task}
        onRerun={onRerun}
        onReuse={onReuse}
        onDelete={() => void onDelete()}
        onChanged={reload}
      />
      <LivePanel task={task} run={run} connection={connection} />
    </div>
  );
}
