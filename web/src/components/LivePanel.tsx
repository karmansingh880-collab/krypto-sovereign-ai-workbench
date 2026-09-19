import { useEffect, useRef } from "react";
import { Activity, CheckCircle2, Circle, Loader2, MinusCircle, XCircle } from "lucide-react";
import type { LiveSnapshot, LiveStage, Task } from "../api/types";
import type { LiveConnection } from "../hooks/useLiveRun";
import { isActiveStatus } from "../hooks/useTask";
import { useNow } from "../hooks/useNow";
import { formatDuration, durationOf } from "../utils/time";

interface Props {
  task: Task;
  run: LiveSnapshot | null;
  connection: LiveConnection;
}

/** The stages of a finished run, as saved with it. */
function savedStages(task: Task): LiveStage[] {
  return task.timeline.map((t) => ({
    id: t.id,
    title: t.title,
    status: t.status === "error" ? "error" : "done",
    detail: t.detail ?? "",
    current: null,
    total: null,
    duration: t.duration,
    error: t.error,
  }));
}

function Icon({ status }: { status: LiveStage["status"] }) {
  if (status === "done") return <CheckCircle2 size={18} aria-hidden />;
  if (status === "running") return <Loader2 size={18} className="spin" aria-hidden />;
  if (status === "error") return <XCircle size={18} aria-hidden />;
  if (status === "skipped") return <MinusCircle size={18} aria-hidden />;
  return <Circle size={18} aria-hidden />;
}

/**
 * Right-hand "what is the AI doing" panel: the step in progress, the steps still to come,
 * and the answer as it is being written. For a finished run it shows how the time was spent.
 */
export function LivePanel({ task, run, connection }: Props) {
  const active = isActiveStatus(run?.status ?? task.status);
  const now = useNow(active, 1000);
  const streamRef = useRef<HTMLPreElement>(null);

  const stages: LiveStage[] = run ? run.stages : task.status === "success" || task.status === "failed" ? savedStages(task) : [];
  const status = run?.status ?? task.status;
  const text = run?.text ?? "";
  const runningIndex = stages.findIndex((s) => s.status === "running");
  const nextPending = stages.findIndex((s, i) => s.status === "pending" && i > runningIndex);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text]);

  let summary: string;
  if (status === "queued") summary = "Waiting for the previous run to finish…";
  else if (status === "running") {
    summary =
      runningIndex >= 0
        ? `Working on step ${runningIndex + 1} of ${stages.length}: ${stages[runningIndex].title}`
        : "Starting…";
  } else if (status === "success") {
    const seconds = durationOf(task);
    summary = seconds === null ? "Finished." : `Finished in ${formatDuration(seconds)}.`;
  } else {
    summary = run?.message || task.final_message || "The run stopped.";
  }

  const total = stages.reduce((sum, s) => sum + (s.duration ?? 0), 0);

  return (
    <aside className="live-panel" aria-label="Live activity" data-testid="live-panel">
      <header className="live-head">
        <h2>
          <Activity size={16} aria-hidden /> Live activity
        </h2>
        <span
          className={`live-conn ${connection}`}
          title={
            connection === "live"
              ? "Connected: updates arrive instantly"
              : connection === "failed"
                ? "Live connection unavailable: this page refreshes itself every few seconds instead"
                : "Connecting…"
          }
        >
          <span className="dot" />
          {connection === "live" ? "live" : connection === "failed" ? "polling" : connection === "connecting" ? "connecting" : ""}
        </span>
      </header>

      <p className="live-summary" data-testid="live-summary" role="status">
        {summary}
      </p>

      {stages.length > 0 ? (
        <ol className="live-steps" data-testid="live-steps">
          {stages.map((stage, index) => {
            const elapsed =
              stage.status === "running" && stage.started ? Math.max(0, now / 1000 - stage.started) : stage.duration;
            const percent =
              stage.total && stage.current !== null ? Math.round((stage.current / stage.total) * 100) : null;
            return (
              <li key={stage.id} className={`live-step ${stage.status}`} data-status={stage.status}>
                <span className="live-icon">
                  <Icon status={stage.status} />
                </span>
                <div className="live-body">
                  <div className="live-title">
                    <span>{stage.title}</span>
                    {index === nextPending && status === "running" && <span className="next-tag">next</span>}
                  </div>
                  {stage.status === "error" && stage.error ? (
                    <div className="live-detail err">{stage.error}</div>
                  ) : (
                    stage.detail && stage.status !== "pending" && <div className="live-detail">{stage.detail}</div>
                  )}
                  {percent !== null && stage.status === "running" && (
                    <div className="live-progress" aria-hidden>
                      <span style={{ width: `${percent}%` }} />
                    </div>
                  )}
                </div>
                <span className="live-time">
                  {elapsed !== null && elapsed !== undefined && stage.status !== "pending" && stage.status !== "skipped"
                    ? formatDuration(elapsed)
                    : ""}
                </span>
              </li>
            );
          })}
        </ol>
      ) : (
        active && (
          <p className="muted small">
            {connection === "failed" ? "Live steps are unavailable; the page will update when the run finishes." : "Preparing…"}
          </p>
        )
      )}

      {text && (
        <div className="live-text">
          <span className="cap">Answer as it is written</span>
          <pre ref={streamRef} data-testid="live-text">
            {text}
            {active && <span className="caret" aria-hidden />}
          </pre>
        </div>
      )}

      {!active && stages.length > 0 && (
        <p className="live-foot muted small">
          {status === "success"
            ? `Steps took ${formatDuration(total)} in total. Choose what to do next below.`
            : "Some steps did not complete. See the trace for details."}
        </p>
      )}
    </aside>
  );
}
