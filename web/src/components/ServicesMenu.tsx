import { useCallback, useRef, useState } from "react";
import { Activity, Check, X } from "lucide-react";
import { problemCount, useServices } from "../hooks/useServices";
import { useDismiss } from "../hooks/useDismiss";

function Row({ label, up, detail }: { label: string; up: boolean; detail?: string }) {
  return (
    <li className={up ? "up" : "down"}>
      {up ? <Check size={15} /> : <X size={15} />}
      <span className="row-label">{label}</span>
      {detail && <span className="muted small">{detail}</span>}
    </li>
  );
}

/** Compact "all systems ready" indicator that opens the detail of each service and model. */
export function ServicesMenu() {
  const state = useServices();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);

  const checking = state.status === null && !state.unreachable;
  const problems = problemCount(state);
  const tone = checking ? "checking" : problems === 0 ? "ok" : "bad";
  const label = checking ? "Checking…" : problems === 0 ? "All systems ready" : `${problems} issue${problems === 1 ? "" : "s"}`;
  const { status } = state;

  return (
    <div className="menu-wrap" ref={ref}>
      <button
        type="button"
        className={`status-pill ${tone}`}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Activity size={14} aria-hidden />
        <span className="dot" />
        <span>{label}</span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="System status">
          <h3>System status</h3>
          {state.unreachable && <p className="notice-inline bad">The backend is not reachable.</p>}
          {status && (
            <ul className="status-list">
              <Row label="MongoDB" up={status.mongo.up} detail={status.mongo.error} />
              <Row
                label="Qdrant (knowledge base)"
                up={status.qdrant.up}
                detail={status.qdrant.up ? `${status.qdrant.knowledge_base_chunks ?? 0} chunks` : status.qdrant.error}
              />
              <Row label="Ollama" up={status.ollama.up} detail={status.ollama.error} />
              {Object.entries(status.ollama.models ?? {}).map(([model, present]) => (
                <Row key={model} label={model} up={present} detail={present ? "ready" : "not pulled"} />
              ))}
              {Object.entries(status.ollama.optional ?? {}).map(([model, present]) => (
                <Row key={model} label={model} up={present} detail={present ? "ready · specialist" : "optional · not installed"} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
