import type {
  AuthConfig,
  ExportResult,
  ModelRouting,
  NetworkStatus,
  NewTaskInput,
  OutputFormat,
  SelfTest,
  Sovereignty,
  SystemStatus,
  Task,
  TaskSummary,
  User,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;

/** Called when a protected request comes back 401 (session expired or signed out elsewhere). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function messageFrom(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "Please check the details you entered.";
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* no JSON body */
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) onUnauthorized?.();
    throw new ApiError(messageFrom(body, response.statusText || "Request failed"), response.status);
  }
  return body as T;
}

const json = (data: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

/* ---------- accounts ---------- */

export const auth = {
  config: () => request<AuthConfig>("/auth/config"),
  me: () => request<User>("/auth/me"),
  login: (email: string, password: string) => request<User>("/auth/login", json({ email, password })),
  signup: (name: string, email: string, password: string) =>
    request<User>("/auth/signup", json({ name, email, password })),
  demo: () => request<User>("/auth/demo", { method: "POST" }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
};

/* ---------- tasks ---------- */

export const tasks = {
  list: (limit = 50) => request<TaskSummary[]>(`/tasks?limit=${limit}`),
  get: (id: string) => request<Task>(`/tasks/${encodeURIComponent(id)}`),
  remove: (id: string) => request<{ deleted: string }>(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  clearFinished: () => request<{ deleted: number }>("/tasks", { method: "DELETE" }),
  fileUrl: (id: string, index: number) => `/tasks/${encodeURIComponent(id)}/files/${index}`,
  /** Save a finished run's answer as a Word file / PDF / image (instant: no model is used). */
  export: (id: string, format: OutputFormat) =>
    request<ExportResult>(`/tasks/${encodeURIComponent(id)}/export`, json({ format })),
  /** Ask more about the same files; starts a new run that knows the earlier question and answer. */
  followUp: (id: string, goal: string) =>
    request<{ id: string; status: string }>(`/tasks/${encodeURIComponent(id)}/followup`, json({ goal })),
};

export const system = {
  status: () => request<SystemStatus>("/system/status"),
  network: (fresh = false) => request<NetworkStatus>(`/system/network${fresh ? "?fresh=1" : ""}`),
  sovereignty: () => request<Sovereignty>("/system/sovereignty"),
  selfTest: () => request<SelfTest>("/system/sovereignty/selftest", { method: "POST" }),
  models: () => request<ModelRouting>("/system/models"),
};

/** ws:// or wss:// address for a WebSocket path on this server. */
export function socketUrl(path: string): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

export interface UploadHandle {
  promise: Promise<{ id: string; status: string }>;
  abort: () => void;
}

/**
 * Create a task. XMLHttpRequest (not fetch) so a large upload can report its progress.
 * Files go in the repeated `files` field; the backend accepts up to 5.
 */
export function createTask(input: NewTaskInput, onProgress?: (fraction: number) => void): UploadHandle {
  const form = new FormData();
  form.append("goal", input.goal);
  if (input.source === "sample") form.append("use_sample", "true");
  if (input.source === "upload") for (const file of input.files) form.append("files", file, file.name);
  if (input.formats.length) form.append("formats", input.formats.join(","));

  const xhr = new XMLHttpRequest();
  const promise = new Promise<{ id: string; status: string }>((resolve, reject) => {
    xhr.open("POST", "/tasks");
    xhr.withCredentials = true;
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      const body: unknown = xhr.response;
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as { id: string; status: string });
      } else {
        if (xhr.status === 401) onUnauthorized?.();
        reject(new ApiError(messageFrom(body, xhr.statusText || "Upload failed"), xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("Could not reach the server.", 0));
    xhr.onabort = () => reject(new ApiError("Upload cancelled.", 0));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}
