import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { auth } from "../api/client";
import type { AuthConfig } from "../api/types";
import { useAuth } from "../context/AuthContext";

type Tab = "login" | "signup";

/** Only ever return to a page of this app, never to an address taken from the query string. */
function safeNext(raw: string | null): string {
  return raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";
}

export function LoginPage() {
  const { state, login, signup, demo } = useAuth();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));

  const [tab, setTab] = useState<Tab>("login");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState("");

  useEffect(() => {
    auth.config().then(setConfig).catch(() => setConfig({ demo_enabled: false }));
  }, []);

  if (state === "authed") return <Navigate to={next} replace />;

  const run = async (action: () => Promise<void>) => {
    setError("");
    setBusy(true);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setBusy(false);
    }
  };

  const submitLogin = (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password) return setError("Enter your email and password.");
    void run(() => login(email.trim(), password));
  };

  const submitSignup = (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return setError("Enter your email address.");
    if (password.length < 8) return setError("Password must be at least 8 characters.");
    if (password !== confirm) return setError("The two passwords do not match.");
    void run(() => signup(name.trim(), email.trim(), password));
  };

  const switchTab = (next: Tab) => {
    setTab(next);
    setError("");
  };

  return (
    <main className="auth-page">
      <div className="auth-wrap">
        <div className="auth-brand">
          <span className="logo big" aria-hidden>K</span>
          <h1>Krypto</h1>
          <p className="muted">Sovereign AI Workbench · runs on local models</p>
        </div>

        <section className="card auth-card">
          <div className="tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "login"} className={tab === "login" ? "active" : ""} onClick={() => switchTab("login")}>
              Sign in
            </button>
            <button type="button" role="tab" aria-selected={tab === "signup"} className={tab === "signup" ? "active" : ""} onClick={() => switchTab("signup")}>
              Create account
            </button>
          </div>

          {tab === "login" ? (
            <form onSubmit={submitLogin} noValidate>
              <label htmlFor="login-email">Email</label>
              <input id="login-email" type="email" autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
              <label htmlFor="login-password">Password</label>
              <input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              <button type="submit" className="btn primary block" disabled={busy}>
                {busy && <Loader2 size={16} className="spin" aria-hidden />} Sign in
              </button>
            </form>
          ) : (
            <form onSubmit={submitSignup} noValidate>
              <label htmlFor="signup-name">Name <span className="muted">(optional)</span></label>
              <input id="signup-name" type="text" autoComplete="name" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
              <label htmlFor="signup-email">Email</label>
              <input id="signup-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
              <label htmlFor="signup-password">Password <span className="muted">(at least 8 characters)</span></label>
              <input id="signup-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              <label htmlFor="signup-confirm">Confirm password</label>
              <input id="signup-confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
              <button type="submit" className="btn primary block" disabled={busy}>
                {busy && <Loader2 size={16} className="spin" aria-hidden />} Create account
              </button>
            </form>
          )}

          {error && <p className="form-error" role="alert">{error}</p>}

          {config?.demo_enabled && (
            <div className="demo">
              <div className="or"><span>or</span></div>
              <button type="button" className="btn ghost block" disabled={busy} onClick={() => void run(demo)}>
                Continue with the demo account
              </button>
              <p className="muted small demo-creds">
                Demo login: <code>{config.demo_email}</code> / <code>{config.demo_password}</code>{" "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => {
                    setTab("login");
                    setEmail(config.demo_email ?? "");
                    setPassword(config.demo_password ?? "");
                  }}
                >
                  fill in
                </button>
              </p>
            </div>
          )}
        </section>
        <p className="muted small center">Your runs and files are private to your account.</p>
      </div>
    </main>
  );
}
