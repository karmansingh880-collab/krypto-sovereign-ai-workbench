import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import type { TaskStatus } from "../api/types";

const LABEL: Record<TaskStatus, string> = {
  queued: "Queued",
  running: "Running",
  success: "Success",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={`badge ${status}`} data-testid="status-badge">
      {status === "success" && <CheckCircle2 size={13} aria-hidden />}
      {status === "failed" && <XCircle size={13} aria-hidden />}
      {status === "running" && <Loader2 size={13} className="spin" aria-hidden />}
      {status === "queued" && <Clock size={13} aria-hidden />}
      {LABEL[status]}
    </span>
  );
}
