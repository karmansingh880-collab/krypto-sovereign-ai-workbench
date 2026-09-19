import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Copy, CornerDownRight, RotateCcw, SquarePen, Trash2 } from "lucide-react";
import type { Task } from "../api/types";
import { useToast } from "../context/ToastContext";
import { useNow } from "../hooks/useNow";
import { isActiveStatus } from "../hooks/useTask";
import { formatDuration, durationOf, relativeTime } from "../utils/time";
import { taskTitle } from "../utils/format";
import { AssessmentView } from "./AssessmentView";
import { Markdown } from "./Markdown";
import { RoutingCard } from "./RoutingCard";
import { NextSteps } from "./NextSteps";
import { OutputsView } from "./OutputsView";
import { StatusBadge } from "./StatusBadge";
import { TraceView } from "./TraceView";

/** The last real answer of a run that ends in text (runs with a corrosion table show that instead). */
export function answerOf(task: Task): string | null {
  if (task.status !== "success" || task.assessment) return null;
  const answered = task.steps.filter((s) => !s.error && s.response && !s.step.startsWith("[SYSTEM]"));
  return answered.length ? (answered[answered.length - 1].response ?? null) : null;
}

interface Props {
  task: Task;
  onRerun?: () => void;
  onReuse: () => void;
  onDelete: () => void;
  /** Called after something changed on the server (e.g. a file was saved) so the run is reloaded. */
  onChanged: () => void;
}

export function ResultView({ task, onRerun, onReuse, onDelete, onChanged }: Props) {
  const { toast } = useToast();
  const active = isActiveStatus(task.status);
  const now = useNow(active);
  const [goalOpen, setGoalOpen] = useState(false);

  const duration = durationOf(task);
  const elapsed = (now - new Date(task.created_at).getTime()) / 1000;
  const answer = answerOf(task);
  const showFailure = task.status === "failed" && task.final_message;
  // Runs that made files (not the corrosion workflow, whose message is just a path) explain what they did.
  const showInfo = task.status === "success" && !task.assessment && task.outputs.length > 0 && task.final_message;
  const longGoal = task.goal.length > 220;

  const copyAnswer = async () => {
    if (!answer) return;
    try {
      await navigator.clipboard.writeText(answer);
      toast("Answer copied", "success");
    } catch {
      toast("Could not copy. Select the text and copy it instead.", "error");
    }
  };

  return (
    <article className="result card" data-testid="result">
      <header className="result-head">
        <div className="result-title">
          <h1>{taskTitle(task.files, task.goal)}</h1>
          {task.parent_id && (
            <Link className="follow-up-link" to={`/task/${task.parent_id}`}>
              <CornerDownRight size={13} aria-hidden /> Follow-up question
            </Link>
          )}
          <p className="muted small" data-testid="meta">
            Started {relativeTime(task.created_at, now)} · {duration === null ? "in progress" : formatDuration(duration)}
            {task.files.length > 0 && ` · ${task.files.join(", ")}`}
          </p>
        </div>
        <div className="result-actions">
          <StatusBadge status={task.status} />
          {!active && onRerun && (
            <button type="button" className="btn ghost small" onClick={onRerun}>
              <RotateCcw size={14} aria-hidden /> Run again
            </button>
          )}
          <button type="button" className="btn ghost small" onClick={onReuse}>
            <SquarePen size={14} aria-hidden /> Reuse instruction
          </button>
          <button type="button" className="btn ghost small danger" onClick={onDelete} disabled={active}>
            <Trash2 size={14} aria-hidden /> Delete
          </button>
        </div>
      </header>

      <p className={`goal${longGoal && !goalOpen ? " clamp" : ""}`}>{task.goal}</p>
      {longGoal && (
        <button type="button" className="link-btn" onClick={() => setGoalOpen((v) => !v)}>
          {goalOpen ? "Show less" : "Show more"}
        </button>
      )}

      {active && (
        <div className="running" role="status">
          <div className="bar"><span /></div>
          <p>
            {task.status === "queued"
              ? "Queued — waiting for the previous run to finish."
              : `Agent is working · ${formatDuration(elapsed)} elapsed. Local models can take a few minutes; long documents take longer.`}
          </p>
        </div>
      )}

      {showFailure && (
        <div className="notice bad" role="alert">
          {task.final_message}
          <div className="hint">
            Tip: the agent works offline with no database or package installs. Attach the document your question is
            about and phrase the goal around what it contains.
          </div>
        </div>
      )}
      {showInfo && <div className="notice info">{task.final_message}</div>}

      {answer && (
        <section className="answer" aria-label="Answer">
          <div className="answer-head">
            <h3>Answer</h3>
            <button type="button" className="btn ghost small" onClick={() => void copyAnswer()}>
              <Copy size={14} aria-hidden /> Copy
            </button>
          </div>
          {task.extras?.code_check && (
            <div
              className={`code-check ${task.extras.code_check.verified ? "ok" : task.extras.code_check.ran ? "warn" : "bad"}`}
              data-testid="code-check"
              data-verified={task.extras.code_check.verified}
            >
              {task.extras.code_check.verified
                ? "Verified: run in the sandbox and passed"
                : task.extras.code_check.ran
                  ? "Ran, but the model's own tests were wrong and were removed"
                  : "Not verified: did not run cleanly"}
              {task.extras.code_check.attempts > 1 && ` · ${task.extras.code_check.attempts} attempts`}
            </div>
          )}
          <Markdown text={answer} />
          {task.extras?.unverified && task.extras.unverified.length > 0 && (
            <div className="notice warn" role="note" data-testid="unverified">
              <AlertTriangle size={15} aria-hidden />
              <span>
                Double-check these against your document: <strong>{task.extras.unverified.join(", ")}</strong>. They
                were not found in its text.
              </span>
            </div>
          )}
          {task.extras?.sources && task.extras.sources.length > 0 && (
            <details className="sources" data-testid="sources">
              <summary>Passages used to answer ({task.extras.sources.length})</summary>
              <ul>
                {task.extras.sources.map((source, index) => (
                  <li key={index}>
                    {source.note ?? (
                      <>
                        <span className="muted small">
                          {source.start_percent !== undefined ? `about ${source.start_percent}% into the document` : "passage"}
                        </span>
                        <q>{source.text}</q>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {task.assessment && <AssessmentView assessment={task.assessment} />}
      <OutputsView task={task} />
      {task.status === "success" && (answer || task.assessment || task.outputs.length > 0) && (
        <NextSteps task={task} onChanged={onChanged} />
      )}
      <RoutingCard steps={task.steps} />
      <TraceView steps={task.steps} active={active} />
    </article>
  );
}
