import { useMemo, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { Plus, Search, Trash2, X } from "lucide-react";
import type { TaskSummary } from "../api/types";
import { useConfirm } from "../context/ConfirmContext";
import { useTasks } from "../context/TasksContext";
import { useToast } from "../context/ToastContext";
import { useNow } from "../hooks/useNow";
import { formatDuration, relativeTime, dayGroup, durationOf, DAY_GROUPS } from "../utils/time";
import { taskTitle } from "../utils/format";

type Filter = "all" | "success" | "failed" | "active";
const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "success", label: "Done" },
  { value: "failed", label: "Failed" },
  { value: "active", label: "Running" },
];

const isActive = (t: TaskSummary) => t.status === "queued" || t.status === "running";

function summaryOf(task: TaskSummary): string {
  if (task.assessment) {
    const flagged = task.assessment.attention.length;
    return `${task.assessment.table.length} locations · ${flagged ? `${flagged} flagged` : "all OK"}`;
  }
  if (task.status === "success" && task.outputs.length) {
    return `${task.outputs.length} file${task.outputs.length === 1 ? "" : "s"}`;
  }
  if (task.status === "failed") return "failed";
  if (isActive(task)) return task.status;
  return "answered";
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { tasks, loaded, remove, clearFinished } = useTasks();
  const { toast } = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const { id: currentId } = useParams();
  const now = useNow(true, 30_000);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks.filter((t) => {
      if (filter === "success" && t.status !== "success") return false;
      if (filter === "failed" && t.status !== "failed") return false;
      if (filter === "active" && !isActive(t)) return false;
      if (!q) return true;
      return `${t.goal} ${t.files.join(" ")}`.toLowerCase().includes(q);
    });
  }, [tasks, query, filter]);

  const groups = useMemo(() => {
    const map = new Map<string, TaskSummary[]>();
    for (const task of visible) {
      const key = dayGroup(task.created_at);
      map.set(key, [...(map.get(key) ?? []), task]);
    }
    return DAY_GROUPS.filter((g) => map.has(g)).map((g) => ({ name: g, items: map.get(g) ?? [] }));
  }, [visible]);

  const hasFinished = tasks.some((t) => !isActive(t));

  const onDelete = async (task: TaskSummary) => {
    const ok = await confirm({
      title: "Delete this run?",
      message: "The run and the files it created will be permanently deleted.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await remove(task.id);
      if (currentId === task.id) navigate("/");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not delete the run.", "error");
    }
  };

  const onClear = async () => {
    const ok = await confirm({
      title: "Delete all finished runs?",
      message: "Every finished run and the files it created will be permanently deleted. Running tasks are kept.",
      confirmLabel: "Delete all",
      danger: true,
    });
    if (!ok) return;
    try {
      const deleted = await clearFinished();
      toast(`Deleted ${deleted} run${deleted === 1 ? "" : "s"}`, "success");
      if (currentId && !tasks.some((t) => t.id === currentId && isActive(t))) navigate("/");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not delete the runs.", "error");
    }
  };

  return (
    <>
      {open && <div className="sidebar-scrim" onClick={onClose} />}
      <aside className={`sidebar${open ? " open" : ""}`} aria-label="Run history">
        <div className="sidebar-head">
          <NavLink to="/" end className="btn primary new-task" onClick={onClose}>
            <Plus size={16} aria-hidden /> New task
          </NavLink>
          <button type="button" className="icon-btn sidebar-close" aria-label="Close history" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        <label className="search">
          <Search size={15} aria-hidden />
          <input
            type="search"
            placeholder="Search runs…"
            aria-label="Search runs"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>

        <div className="chips" role="group" aria-label="Filter runs">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              className={`chip${filter === f.value ? " active" : ""}`}
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="history-scroll">
          {!loaded && <p className="muted small pad">Loading…</p>}
          {loaded && visible.length === 0 && (
            <p className="muted small pad">{tasks.length ? "No runs match." : "No runs yet. Start one with “New task”."}</p>
          )}
          {groups.map((group) => (
            <section key={group.name}>
              <h3 className="group-title">{group.name}</h3>
              <ul className="history">
                {group.items.map((task) => {
                  const duration = durationOf(task);
                  return (
                    <li key={task.id}>
                      <NavLink to={`/task/${task.id}`} className="history-item" title={task.goal} onClick={onClose}>
                        <span className={`dot ${task.status}`} aria-label={task.status} />
                        <span className="history-text">
                          <span className="history-title">{taskTitle(task.files, task.goal)}</span>
                          <span className="history-sub">
                            {relativeTime(task.created_at, now)}
                            {duration !== null && ` · ${formatDuration(duration)}`} · {summaryOf(task)}
                          </span>
                        </span>
                      </NavLink>
                      {!isActive(task) && (
                        <button
                          type="button"
                          className="icon-btn delete-btn"
                          aria-label={`Delete run ${taskTitle(task.files, task.goal)}`}
                          onClick={() => void onDelete(task)}
                        >
                          <Trash2 size={15} />
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>

        {hasFinished && (
          <button type="button" className="btn ghost small clear-btn" onClick={() => void onClear()}>
            <Trash2 size={14} aria-hidden /> Clear finished
          </button>
        )}
      </aside>
    </>
  );
}
