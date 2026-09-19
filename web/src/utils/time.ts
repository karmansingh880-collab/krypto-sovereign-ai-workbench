import type { TaskSummary } from "../api/types";

export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

/** Seconds a finished run took, or null while it is still going. */
export function durationOf(task: Pick<TaskSummary, "created_at" | "finished_at">): number | null {
  if (!task.finished_at) return null;
  return (new Date(task.finished_at).getTime() - new Date(task.created_at).getTime()) / 1000;
}

export type DayGroup = "Today" | "Yesterday" | "This week" | "Older";
export const DAY_GROUPS: DayGroup[] = ["Today", "Yesterday", "This week", "Older"];

export function dayGroup(iso: string, now: Date = new Date()): DayGroup {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const then = new Date(iso).getTime();
  const days = Math.floor((startOfToday - then) / 86_400_000) + 1;
  if (then >= startOfToday) return "Today";
  if (days <= 1) return "Yesterday";
  if (days <= 6) return "This week";
  return "Older";
}
