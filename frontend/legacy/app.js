"use strict";

const params = new URLSearchParams(location.search);
const API = (
  params.get("api") ||
  (location.protocol.startsWith("http") ? location.origin : "http://localhost:8010")
).replace(/\/$/, "");

const DEFAULT_GOAL =
  "Read the inspection report, extract wall-thickness readings, calculate " +
  "corrosion rate and remaining life for each location, cite the relevant " +
  "SOP, and generate an approval note.";

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let currentTaskId = null;
let currentTask = null;
let pollTimer = null;
let tickTimer = null;
let traceExpanded = false;

const isActive = (task) => task.status === "queued" || task.status === "running";

/* ---------- helpers ---------- */

function toSignIn() {
  location.href = "login.html?next=" + encodeURIComponent(location.pathname + location.search);
}

async function api(path, options) {
  const res = await fetch(API + path, options);
  if (res.status === 401) {
    toSignIn();
    throw new Error("Please sign in");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

let toastTimer = null;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4000);
}

function relativeTime(iso) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

function durationOf(task) {
  if (!task.finished_at) return null;
  return (new Date(task.finished_at) - new Date(task.created_at)) / 1000;
}

function num(value, digits) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

/* ---------- service status ---------- */

function pill(label, up, title = "") {
  return el("span", { className: "pill " + (up ? "up" : "down"), title }, label);
}

async function refreshServices() {
  try {
    const s = await api("/system/status");
    const pills = [
      pill("MongoDB", s.mongo.up, s.mongo.error || ""),
      pill(
        s.qdrant.up ? `Qdrant · ${s.qdrant.knowledge_base_chunks} chunks` : "Qdrant",
        s.qdrant.up,
        s.qdrant.error || ""
      ),
      pill("Ollama", s.ollama.up, s.ollama.error || ""),
    ];
    for (const [tag, present] of Object.entries(s.ollama.models || {})) {
      pills.push(pill(present ? tag : `${tag} (not pulled)`, present));
    }
    $("services").replaceChildren(...pills);
  } catch (err) {
    $("services").replaceChildren(pill("Backend unreachable", false));
  }
}

/* ---------- new task form ---------- */

const selectedSource = () => document.querySelector('input[name="source"]:checked').value;
const selectedFormats = () =>
  [...document.querySelectorAll('input[name="format"]:checked')].map((box) => box.value);

for (const radio of document.querySelectorAll('input[name="source"]')) {
  radio.addEventListener("change", () => {
    $("file").disabled = selectedSource() !== "upload";
  });
}

$("goal").value = DEFAULT_GOAL;

async function submitTask(goal, source, file, formats = selectedFormats()) {
  const form = new FormData();
  form.append("goal", goal);
  if (source === "sample") form.append("use_sample", "true");
  if (source === "upload") form.append("file", file);
  if (formats.length) form.append("formats", formats.join(","));
  const created = await api("/tasks", { method: "POST", body: form });
  openTask(created.id);
  refreshHistory();
  toast("Task started");
}

$("task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("form-error").textContent = "";

  const source = selectedSource();
  const file = $("file").files[0];
  if (source === "upload" && !file) {
    $("form-error").textContent = "Choose a file to upload.";
    return;
  }

  $("run").disabled = true;
  try {
    await submitTask($("goal").value, source, file);
  } catch (err) {
    $("form-error").textContent = err.message;
  } finally {
    $("run").disabled = false;
  }
});

/* ---------- selecting / polling a task ---------- */

function stopTimers() {
  clearTimeout(pollTimer);
  clearInterval(tickTimer);
  pollTimer = tickTimer = null;
}

function showEmpty() {
  stopTimers();
  currentTaskId = null;
  currentTask = null;
  $("result-card").hidden = true;
  $("empty").hidden = false;
}

function openTask(id) {
  stopTimers();
  currentTaskId = id;
  traceExpanded = false;
  poll();
}

async function poll() {
  const id = currentTaskId;
  if (!id) return;
  try {
    const task = await api(`/tasks/${id}`);
    if (id !== currentTaskId) return;
    currentTask = task;
    renderTask(task);
    if (isActive(task)) {
      pollTimer = setTimeout(poll, 2000);
    } else {
      stopTimers();
      refreshHistory();
    }
  } catch (err) {
    if (id !== currentTaskId) return;
    toast("Could not load task: " + err.message);
    pollTimer = setTimeout(poll, 4000);
  }
}

/* ---------- result panel ---------- */

function statusClass(text) {
  const s = String(text || "").toLowerCase();
  if (s.startsWith("replace") || s.startsWith("high")) return "bad";
  if (s.startsWith("moderate")) return "warn";
  return "ok";
}

function renderAssessment(assessment) {
  const box = $("assessment");
  if (!assessment || !assessment.table || !assessment.table.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  const attention = assessment.attention || [];
  $("stats").replaceChildren(
    el("div", { className: "stat" },
      el("div", { className: "label" }, "Locations assessed"),
      el("div", { className: "value" }, assessment.table.length)),
    el("div", { className: "stat attn" + (attention.length ? "" : " none") },
      el("div", { className: "label" }, "Need attention"),
      el("div", { className: "value" }, attention.length),
      el("div", { className: "sub" }, attention.length ? attention.join(", ") : "All within limits")),
    el("div", { className: "stat" },
      el("div", { className: "label" }, "Minimum allowable thickness"),
      el("div", { className: "value" }, assessment.required_thickness == null ? "—" : `${num(assessment.required_thickness, 2)} mm`),
      el("div", { className: "sub" }, "from the SOP"))
  );

  $("table").tBodies[0].replaceChildren(
    ...assessment.table.map((row) => {
      const [head, ...rest] = String(row.status || "").split(":");
      const cls = statusClass(row.status);
      return el("tr", { className: cls === "bad" ? "attn-row" : "" },
        el("td", {}, row.location),
        el("td", { className: "num" }, num(row.previous_thickness, 2)),
        el("td", { className: "num" }, num(row.current_thickness, 2)),
        el("td", { className: "num" }, num(row.corrosion_rate, 3)),
        el("td", { className: "num" }, num(row.remaining_life, 1)),
        el("td", { className: "status-cell", title: row.status || "" },
          row.status
            ? el("span", { className: "status-tag " + cls }, head)
            : "—",
          rest.length ? el("div", { className: "muted" }, rest.join(":").trim()) : null)
      );
    })
  );
}

function renderSteps(task) {
  const steps = task.steps || [];
  $("trace-count").textContent = steps.length ? `(${steps.length})` : "";
  $("toggle-trace").hidden = steps.length === 0;
  $("toggle-trace").textContent = traceExpanded ? "Collapse all" : "Expand all";
  $("trace-hint").textContent = isActive(task)
    ? "The full step trace appears when the run finishes."
    : steps.length ? "" : "No steps were recorded.";
  $("steps").replaceChildren(...steps.map(renderStep));
}

function prettyResponse(text) {
  const raw = String(text ?? "");
  let out = raw;
  try { out = JSON.stringify(JSON.parse(raw), null, 2); } catch (_) { /* not JSON */ }
  return out.length > 4000 ? out.slice(0, 4000) + "\n… (truncated)" : out;
}

function renderStep(step, index) {
  const auto = step.step.startsWith("[SYSTEM]");
  const title = step.step.replace(/^\[SYSTEM\]\s*/, "");
  const tools = Array.isArray(step.tool_used) ? step.tool_used : step.tool_used ? [step.tool_used] : [];

  const summary = el("summary", {},
    el("span", { className: "n" }, index + 1),
    el("span", {}, title),
    ...tools.map((t) => el("span", { className: "chip tool" }, t)),
    !tools.length && !step.error ? el("span", { className: "chip" }, "model reply") : null,
    auto ? el("span", { className: "chip auto", title: "Run by the system in code, not by the model" }, "auto") : null,
    step.error ? el("span", { className: "chip err" }, "failed") : null
  );

  const body = el("div", { className: "body" });
  if (step.tool_args) {
    body.append(el("div", { className: "cap" }, "Arguments"), el("pre", {}, prettyResponse(JSON.stringify(step.tool_args))));
  }
  if (step.error) {
    body.append(el("div", { className: "cap" }, "Error"), el("pre", { className: "bad" }, step.error));
  } else if (step.response) {
    body.append(el("div", { className: "cap" }, "Result"), el("pre", {}, prettyResponse(step.response)));
  }

  return el("li", {}, el("details", { open: traceExpanded }, summary, body));
}

function updateRunningText(task) {
  const elapsed = (Date.now() - new Date(task.created_at).getTime()) / 1000;
  $("running-text").textContent =
    task.status === "queued"
      ? "Queued — waiting for the previous run to finish."
      : `Agent is working · ${formatDuration(elapsed)} elapsed. Local models can take a few minutes.`;
}

/* For runs that end in text (no approval note / table), show the last real answer. */
function answerOf(task) {
  if (task.status !== "success" || task.assessment) return null;
  const answered = (task.steps || []).filter(
    (s) => !s.error && s.response && !s.step.startsWith("[SYSTEM]")
  );
  return answered.length ? answered[answered.length - 1].response : null;
}

const KIND_LABEL = { docx: "Word", pdf: "PDF", image: "Image", file: "File" };

/* Download buttons for every produced file, plus a preview gallery for images. */
function renderOutputs(task) {
  const fileUrl = (file) => `${API}/tasks/${task.id}/files/${file.index}`;
  const isNote = (file) => file.kind === "docx" && file.name.startsWith("approval_note_");

  // Images get a preview card below; documents (and the corrosion note) get a download button.
  const buttons = task.outputs.filter((f) => f.kind !== "image").map((file) =>
    el("a", { href: fileUrl(file), download: file.name, className: "kind-" + file.kind },
      ...(isNote(file)
        ? ["Download approval note (.docx)"]
        : [el("span", { className: "tag" }, KIND_LABEL[file.kind] || "File"), ` ${file.name}`])
    )
  );
  $("downloads").replaceChildren(...buttons);

  const images = task.outputs.filter((f) => f.kind === "image");
  $("gallery").hidden = images.length === 0;
  $("gallery").replaceChildren(
    ...images.map((file) => {
      const url = fileUrl(file);
      return el("figure", {},
        el("a", { className: "thumb", href: url, target: "_blank", rel: "noopener" },
          el("img", { src: url, alt: file.name, loading: "lazy" })),
        el("figcaption", {},
          el("span", { className: "name" }, file.name),
          el("a", { href: url, download: file.name }, "Download image")));
    })
  );
}

function renderTask(task) {
  $("empty").hidden = true;
  $("result-card").hidden = false;

  const badge = $("status");
  badge.textContent = task.status;
  badge.className = "badge " + task.status;

  const duration = durationOf(task);
  const inputs = task.files.length ? task.files.join(", ") : "no file";
  $("meta").textContent =
    `Started ${relativeTime(task.created_at)} · ${duration === null ? "in progress" : formatDuration(duration)} · ${inputs}`;
  $("result-goal").textContent = task.goal;

  const active = isActive(task);
  $("running").hidden = !active;
  clearInterval(tickTimer);
  if (active) {
    updateRunningText(task);
    tickTimer = setInterval(() => updateRunningText(task), 1000);
  }

  const notice = $("final-message");
  const showNotice = task.status === "failed" && task.final_message;
  // Runs that produced files (not the corrosion workflow, whose message is just a path) explain what they made.
  const showInfo = task.status === "success" && !task.assessment && task.outputs.length && task.final_message;
  notice.hidden = !(showNotice || showInfo);
  notice.className = "notice " + (showNotice ? "bad" : "info");
  notice.replaceChildren();
  if (showNotice) {
    notice.append(
      task.final_message,
      el("div", { className: "hint" },
        "Tip: the agent works offline with no database or package installs. Attach the document your " +
        "question is about and phrase the goal around what it contains.")
    );
  } else if (showInfo) {
    notice.textContent = task.final_message;
  }

  const answer = answerOf(task);
  $("answer").hidden = answer === null;
  $("answer-text").textContent = answer ?? "";

  renderAssessment(task.assessment);

  renderOutputs(task);

  const canRerun = !active && (task.files.length === 0 || task.files[0].endsWith("(sample)"));
  $("rerun").hidden = !canRerun;
  $("delete").disabled = active;

  renderSteps(task);
}

$("toggle-trace").addEventListener("click", () => {
  traceExpanded = !traceExpanded;
  for (const details of document.querySelectorAll("#steps details")) details.open = traceExpanded;
  $("toggle-trace").textContent = traceExpanded ? "Collapse all" : "Expand all";
});

$("rerun").addEventListener("click", async () => {
  if (!currentTask) return;
  const source = currentTask.files.length ? "sample" : "none";
  try {
    await submitTask(currentTask.goal, source, undefined, currentTask.formats || []);
  } catch (err) {
    toast(err.message);
  }
});

async function deleteTask(id) {
  if (!confirm("Delete this run and its generated files?")) return;
  try {
    await api(`/tasks/${id}`, { method: "DELETE" });
    if (id === currentTaskId) showEmpty();
    refreshHistory();
  } catch (err) {
    toast(err.message);
  }
}

$("delete").addEventListener("click", () => currentTaskId && deleteTask(currentTaskId));

$("clear").addEventListener("click", async () => {
  if (!confirm("Delete all finished runs and their generated files?")) return;
  try {
    const { deleted } = await api("/tasks", { method: "DELETE" });
    toast(`Deleted ${deleted} run${deleted === 1 ? "" : "s"}`);
    refreshHistory();
  } catch (err) {
    toast(err.message);
  }
});

/* ---------- history ---------- */

function historyItem(task) {
  const summary = [relativeTime(task.created_at)];
  const duration = durationOf(task);
  if (duration !== null) summary.push(formatDuration(duration));

  const sub = el("div", { className: "h-sub" }, summary.join(" · "));
  if (task.assessment) {
    const attn = (task.assessment.attention || []).length;
    sub.append(` · ${task.assessment.table.length} locations · `);
    sub.append(attn ? el("span", { className: "attn" }, `${attn} flagged`) : "all OK");
  } else if (task.outputs.length && task.status === "success") {
    sub.append(` · ${task.outputs.length} file${task.outputs.length === 1 ? "" : "s"}`);
  } else if (task.status === "failed") {
    sub.append(" · failed");
  } else if (isActive(task)) {
    sub.append(` · ${task.status}`);
  }

  const item = el("li", {
    className: task.id === currentTaskId ? "active" : "",
    title: task.goal,
    tabIndex: 0,
  },
    el("span", { className: "dot " + task.status }),
    el("div", { className: "h-main" },
      el("div", { className: "h-title" }, task.files[0] || "No file"),
      sub),
    isActive(task) ? el("span") : el("button", { className: "h-del", type: "button", title: "Delete run", ariaLabel: "Delete run" }, "×")
  );

  const open = () => openTask(task.id);
  item.addEventListener("click", open);
  item.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  const del = item.querySelector(".h-del");
  if (del) del.addEventListener("click", (e) => { e.stopPropagation(); deleteTask(task.id); });
  return item;
}

async function refreshHistory() {
  try {
    const tasks = await api("/tasks?limit=30");
    $("clear").hidden = !tasks.some((t) => !isActive(t));
    $("history").replaceChildren(
      ...(tasks.length ? tasks.map(historyItem) : [el("li", { className: "empty-row" }, "No runs yet.")])
    );
    if (currentTaskId && !tasks.some((t) => t.id === currentTaskId)) showEmpty();
  } catch (_) { /* backend unreachable — the status pills already show it */ }
}

$("logout").addEventListener("click", async () => {
  try { await fetch(API + "/auth/logout", { method: "POST" }); } catch (_) { /* leaving anyway */ }
  location.href = "login.html";
});

(async function boot() {
  try {
    const me = await api("/auth/me");  // a 401 here sends the visitor to the sign-in page
    $("user-name").replaceChildren("Signed in as ", el("strong", {}, me.name));
  } catch (_) {
    return;
  }
  if (params.get("task")) openTask(params.get("task"));
  refreshServices();
  refreshHistory();
  setInterval(refreshServices, 15000);
  setInterval(refreshHistory, 30000);
})();
