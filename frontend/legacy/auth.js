"use strict";

const params = new URLSearchParams(location.search);
const API = (
  params.get("api") ||
  (location.protocol.startsWith("http") ? location.origin : "http://localhost:8010")
).replace(/\/$/, "");

const $ = (id) => document.getElementById(id);

/* Only ever go back to a page of this app (never an arbitrary URL from the query string). */
function nextUrl() {
  const next = params.get("next") || "";
  return next.startsWith("/ui/") && !next.startsWith("//") ? next : "index.html";
}

async function post(path, body) {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail) ? "Please check the details you entered." : res.statusText;
    throw new Error(detail);
  }
  return data;
}

function setError(message) {
  $("auth-error").textContent = message || "";
}

function setBusy(busy) {
  for (const id of ["login-submit", "signup-submit", "demo-btn"]) $(id).disabled = busy;
}

async function attempt(action) {
  setError("");
  setBusy(true);
  try {
    await action();
    location.href = nextUrl();
  } catch (err) {
    setError(err.message);
    setBusy(false);
  }
}

/* ---------- tabs ---------- */

function showTab(which) {
  const login = which === "login";
  $("login-form").hidden = !login;
  $("signup-form").hidden = login;
  $("tab-login").classList.toggle("active", login);
  $("tab-signup").classList.toggle("active", !login);
  $("tab-login").setAttribute("aria-selected", String(login));
  $("tab-signup").setAttribute("aria-selected", String(!login));
  setError("");
  (login ? $("login-email") : $("signup-name")).focus();
}

$("tab-login").addEventListener("click", () => showTab("login"));
$("tab-signup").addEventListener("click", () => showTab("signup"));

/* ---------- forms ---------- */

$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const email = $("login-email").value.trim();
  const password = $("login-password").value;
  if (!email || !password) return setError("Enter your email and password.");
  attempt(() => post("/auth/login", { email, password }));
});

$("signup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = $("signup-name").value.trim();
  const email = $("signup-email").value.trim();
  const password = $("signup-password").value;
  if (!email) return setError("Enter your email address.");
  if (password.length < 8) return setError("Password must be at least 8 characters.");
  if (password !== $("signup-confirm").value) return setError("The two passwords do not match.");
  attempt(() => post("/auth/signup", { name, email, password }));
});

/* ---------- demo account ---------- */

$("demo-btn").addEventListener("click", () => attempt(() => post("/auth/demo")));

$("demo-fill").addEventListener("click", () => {
  showTab("login");
  $("login-email").value = $("demo-email").textContent;
  $("login-password").value = $("demo-pass").textContent;
  $("login-submit").focus();
});

async function init() {
  // Already signed in? Skip the form.
  try {
    const res = await fetch(API + "/auth/me");
    if (res.ok) { location.replace(nextUrl()); return; }
  } catch (_) { /* server not reachable: show the form anyway */ }

  try {
    const config = await (await fetch(API + "/auth/config")).json();
    if (config.demo_enabled) {
      $("demo-email").textContent = config.demo_email;
      $("demo-pass").textContent = config.demo_password;
      $("demo-box").hidden = false;
    }
  } catch (_) { /* no demo box */ }
  $("login-email").focus();
}

init();
