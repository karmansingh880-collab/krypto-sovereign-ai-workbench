export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function num(value: number | null | undefined, digits: number): string {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** Short label for a run: its file name(s), else the start of the instruction. */
export function taskTitle(files: string[], goal: string): string {
  if (files.length === 1) return files[0];
  if (files.length > 1) return `${files[0]} +${files.length - 1} more`;
  const trimmed = goal.trim().replace(/\s+/g, " ");
  return trimmed.length > 48 ? `${trimmed.slice(0, 47)}…` : trimmed;
}
