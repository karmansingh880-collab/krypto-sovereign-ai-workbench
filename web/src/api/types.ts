export type TaskStatus = "queued" | "running" | "success" | "failed";
export type OutputKind = "docx" | "pdf" | "image" | "file";
export type OutputFormat = "docx" | "pdf" | "image";

export interface User {
  id: string;
  email: string;
  name: string;
  is_demo: boolean;
}

export interface AuthConfig {
  demo_enabled: boolean;
  demo_email?: string;
  demo_password?: string;
}

export interface OutputFile {
  index: number;
  name: string;
  kind: OutputKind;
}

export interface CorrosionRow {
  location: string;
  previous_thickness?: number | null;
  current_thickness?: number | null;
  corrosion_rate?: number | null;
  remaining_life?: number | null;
  status?: string;
}

export interface Assessment {
  required_thickness: number | null;
  table: CorrosionRow[];
  attention: string[];
}

/** Which model the router picked for a step, and which one actually ran on this computer. */
export interface RouteInfo {
  task_type: string;
  designed_model: string;
  model: string;
  reason: string;
  substituted: boolean;
}

export interface Step {
  step: string;
  tool_used: string | string[] | null;
  tool_args: unknown;
  response: string | null;
  error: string | null;
  route?: RouteInfo | null;
}

export interface TaskSummary {
  id: string;
  goal: string;
  files: string[];
  formats: OutputFormat[];
  assessment: Assessment | null;
  status: TaskStatus;
  final_message: string | null;
  outputs: OutputFile[];
  created_at: string;
  finished_at: string | null;
  parent_id: string | null;
}

export interface TimelineStage {
  id: string;
  title: string;
  status: string;
  detail: string | null;
  duration: number | null;
  error: string | null;
}

export interface AnswerSource {
  start_percent?: number;
  text?: string;
  note?: string;
}

/** Checks made on the answer: which passages it used, and facts the document does not contain. */
export interface AnswerExtras {
  sources?: AnswerSource[];
  unverified?: string[];
  /** For code answers: was the code run in the sandbox, and did it pass? */
  code_check?: { verified: boolean; ran: boolean; checks_removed: boolean; attempts: number; isolation: string | null };
}

export interface Task extends TaskSummary {
  steps: Step[];
  timeline: TimelineStage[];
  extras: AnswerExtras;
}

export interface ServiceStatus {
  up: boolean;
  error?: string;
}

export interface SystemStatus {
  mongo: ServiceStatus;
  qdrant: ServiceStatus & { knowledge_base_chunks?: number };
  ollama: ServiceStatus & { models: Record<string, boolean>; optional?: Record<string, boolean> };
}

export type SourceKind = "upload" | "sample" | "none";

export interface NewTaskInput {
  goal: string;
  source: SourceKind;
  files: File[];
  formats: OutputFormat[];
}

/* ---------- live progress (WebSocket) ---------- */

export type LiveStatus = "pending" | "running" | "done" | "error" | "skipped";

export interface LiveStage {
  id: string;
  title: string;
  status: LiveStatus;
  detail: string;
  current: number | null;
  total: number | null;
  started?: number | null;
  duration: number | null;
  error?: string | null;
}

export interface LiveSnapshot {
  task_id: string;
  status: TaskStatus;
  message: string;
  elapsed: number;
  stages: LiveStage[];
  text: string;
}

export type LiveEvent =
  | { type: "snapshot"; run: LiveSnapshot }
  | { type: "plan"; stages: LiveStage[] }
  | { type: "stage"; stage: LiveStage }
  | { type: "token"; text: string }
  | { type: "text_reset" }
  | { type: "status"; status: TaskStatus; message: string }
  | { type: "ping" };

/* ---------- network / egress ---------- */

export interface NetworkStatus {
  online: boolean;
  latency_ms: number | null;
  strict: boolean;
  blocked_attempts: number;
  recent_blocked: { time: string; host: string; port: number | string | null; caller: string }[];
  wifi: { connected: boolean; ssid: string | null; signal: string | null } | null;
  /** Private-network addresses are allowed (two computers on one cable/switch). */
  lan_allowed?: boolean;
  link?: {
    kind: "ethernet" | "wifi" | "both" | "none" | "unknown";
    adapters: { name: string; kind: string; up: boolean; speed_mbps: number | null; ipv4: string[] }[];
    /** Addresses another computer on the same network can open. */
    lan_urls: string[];
  };
  message: string;
  checked_at: string;
}

/* ---------- sovereignty proof ---------- */

export interface ConnectionRow {
  role: string;
  /** core = Krypto's own components; helper = third-party programs beside it (Docker Desktop, Ollama's tray updater). */
  party: "core" | "helper";
  direction: "inbound" | "outbound" | "listening";
  process: string;
  pid: number;
  local: string;
  remote: string;
  state: string;
  scope: string;
  kind: "connection" | "listening";
}

export interface Sovereignty {
  verdict: "local-only" | "lan-only" | "helpers" | "internet";
  headline: string;
  strict: boolean;
  lan_allowed: boolean;
  blocked_attempts: number;
  recent_blocked: { time: string; host: string; port: number | string | null; caller: string }[];
  lan_connections: { host: string; port: number | string | null; count: number; caller: string }[];
  connections_now: ConnectionRow[];
  listening: ConnectionRow[];
  counts_now: Record<string, number>;
  ever_seen: {
    role: string;
    party: "core" | "helper";
    process: string;
    direction: "inbound" | "outbound";
    host: string;
    scope: string;
    first_seen: string;
    last_seen: string;
    count: number;
    example: string;
    connected: boolean;
  }[];
  ever_external: number;
  ever_external_core: number;
  helper_advice: { role: string; advice: string }[];
  samples: number;
  watching_seconds: number;
  processes: string[];
  cloud_libraries_installed: string[];
  audit_log: string;
  audit_tail: string[];
  host: string;
  how_to_check: string[];
}

export interface SelfTest {
  ran: boolean;
  all_refused?: boolean;
  message: string;
  attempts: { target: string; what: string; result: string }[];
}

export interface ModelRouting {
  demo_mode: boolean;
  models: { task_type: string; roles: string[]; designed_model: string; running_model: string; installed: boolean; stand_in: boolean }[];
}

export interface ExportResult {
  output: OutputFile;
  created: boolean;
}
