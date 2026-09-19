import { formatBytes } from "./format";

// These mirror the backend's limits (routes/tasks.py); the server checks them again.
export const MAX_FILES = 5;
export const MAX_FILE_BYTES = 100 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 250 * 1024 * 1024;

const EXTENSIONS = [
  ".pdf", ".docx", ".txt", ".md", ".csv",
  ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
];
export const ACCEPT_ATTR = EXTENSIONS.join(",");

export type FileGroup = "pdf" | "word" | "text" | "image" | "other";

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

export function fileGroup(name: string): FileGroup {
  const ext = extensionOf(name);
  if (ext === ".pdf") return "pdf";
  if (ext === ".docx") return "word";
  if ([".txt", ".md", ".csv"].includes(ext)) return "text";
  if ([".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"].includes(ext)) return "image";
  return "other";
}

export interface Validation {
  accepted: File[];
  errors: string[];
}

/** Add `incoming` files to `existing`, enforcing type, count and size limits. */
export function addFiles(existing: File[], incoming: File[]): Validation {
  const accepted = [...existing];
  const errors: string[] = [];
  let total = existing.reduce((sum, f) => sum + f.size, 0);

  for (const file of incoming) {
    const ext = extensionOf(file.name);
    if (ext === ".doc") {
      errors.push(`“${file.name}”: old .doc files are not supported. Save it as .docx first.`);
    } else if (!EXTENSIONS.includes(ext)) {
      errors.push(`“${file.name}”: ${ext || "this"} files are not supported.`);
    } else if (file.size === 0) {
      errors.push(`“${file.name}” is empty.`);
    } else if (file.size > MAX_FILE_BYTES) {
      errors.push(`“${file.name}” is ${formatBytes(file.size)}; the limit is ${formatBytes(MAX_FILE_BYTES)} per file.`);
    } else if (accepted.length >= MAX_FILES) {
      errors.push(`Only ${MAX_FILES} files can be attached at once; “${file.name}” was skipped.`);
    } else if (total + file.size > MAX_TOTAL_BYTES) {
      errors.push(`“${file.name}” would exceed the ${formatBytes(MAX_TOTAL_BYTES)} total limit.`);
    } else if (accepted.some((f) => f.name === file.name && f.size === file.size)) {
      errors.push(`“${file.name}” is already attached.`);
    } else {
      accepted.push(file);
      total += file.size;
    }
  }
  return { accepted, errors };
}
