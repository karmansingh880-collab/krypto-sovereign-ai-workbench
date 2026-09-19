import { useState } from "react";
import type { Step } from "../api/types";

function pretty(text: string | null | undefined): string {
  const raw = text ?? "";
  let out = raw;
  try {
    out = JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    /* not JSON: show as written */
  }
  return out.length > 4000 ? `${out.slice(0, 4000)}\n… (truncated)` : out;
}

function toolsOf(step: Step): string[] {
  if (Array.isArray(step.tool_used)) return step.tool_used;
  return step.tool_used ? [step.tool_used] : [];
}

function StepItem({ step, index, open }: { step: Step; index: number; open: boolean }) {
  const auto = step.step.startsWith("[SYSTEM]");
  const title = step.step.replace(/^\[SYSTEM\]\s*/, "");
  const tools = toolsOf(step);
  const args = step.tool_args ? pretty(JSON.stringify(step.tool_args)) : null;

  return (
    <li>
      <details open={open}>
        <summary>
          <span className="step-n">{index + 1}</span>
          <span className="step-title">{title}</span>
          {tools.map((tool) => (
            <span key={tool} className="chip-static tool">{tool}</span>
          ))}
          {tools.length === 0 && !step.error && <span className="chip-static">model reply</span>}
          {step.route && (
            <span className="chip-static route" title={`${step.route.reason}. The design calls for ${step.route.designed_model}.`}>
              {step.route.task_type} · {step.route.model}
            </span>
          )}
          {auto && <span className="chip-static auto" title="Run by the system in code, not by the model">auto</span>}
          {step.error && <span className="chip-static err">failed</span>}
        </summary>
        <div className="step-body">
          {args && (
            <>
              <span className="cap">Arguments</span>
              <pre>{args}</pre>
            </>
          )}
          {step.error ? (
            <>
              <span className="cap">Error</span>
              <pre className="bad">{step.error}</pre>
            </>
          ) : (
            step.response && (
              <>
                <span className="cap">Result</span>
                <pre>{pretty(step.response)}</pre>
              </>
            )
          )}
        </div>
      </details>
    </li>
  );
}

/** The step-by-step audit trail of a run. */
export function TraceView({ steps, active }: { steps: Step[]; active: boolean }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <section className="trace" aria-label="Agent trace">
      <div className="trace-head">
        <h3 className="section-title">
          Agent trace {steps.length > 0 && <span className="muted">({steps.length})</span>}
        </h3>
        {steps.length > 0 && (
          <button type="button" className="btn ghost small" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Collapse all" : "Expand all"}
          </button>
        )}
      </div>
      {active && <p className="muted small">The full step trace appears when the run finishes.</p>}
      {!active && steps.length === 0 && <p className="muted small">No steps were recorded.</p>}
      <ol className="steps">
        {steps.map((step, index) => (
          <StepItem key={`${index}-${expanded}`} step={step} index={index} open={expanded} />
        ))}
      </ol>
    </section>
  );
}
