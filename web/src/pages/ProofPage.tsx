import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Cable, Check, Loader2, Network, RefreshCw, ShieldCheck, X } from "lucide-react";
import { system } from "../api/client";
import type { ModelRouting, SelfTest, Sovereignty } from "../api/types";
import { useNetwork } from "../hooks/useNetwork";

const REFRESH_MS = 5000;

function duration(seconds: number): string {
  if (seconds < 90) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 90) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function Tile({ label, value, tone }: { label: string; value: string | number; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className={`proof-tile ${tone ?? ""}`}>
      <span className="proof-value">{value}</span>
      <span className="muted small">{label}</span>
    </div>
  );
}

const SCOPE_TAG: Record<string, string> = {
  "this computer": "ok",
  "local network": "warn",
  internet: "bad",
  "reachable from the network": "warn",
};

/** Evidence that nothing leaves this computer: what the operating system says is connected, and a live test. */
export function ProofPage() {
  const [data, setData] = useState<Sovereignty | null>(null);
  const [routing, setRouting] = useState<ModelRouting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<SelfTest | null>(null);
  const [testing, setTesting] = useState(false);
  const { status: network } = useNetwork();

  const load = useCallback(async () => {
    try {
      setData(await system.sovereignty());
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not read the connection list.");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    system.models().then(setRouting).catch(() => undefined);
  }, []);

  const runTest = async () => {
    setTesting(true);
    try {
      setTest(await system.selfTest());
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The test could not run.");
    } finally {
      setTesting(false);
    }
  };

  const verdictTone = !data ? "" : data.verdict === "internet" ? "bad" : data.verdict === "local-only" ? "ok" : "warn";
  const link = network?.link;

  return (
    <div className="proof" data-testid="proof-page">
      <header className="proof-head">
        <ShieldCheck size={26} aria-hidden />
        <div>
          <h2>Sovereignty proof</h2>
          <p className="muted">
            Krypto's claim is that nothing leaves this computer. This page shows the evidence: the operating system's own
            list of connections, and a test you can run.
          </p>
        </div>
      </header>

      {error && (
        <div className="notice bad" role="alert">
          {error}
        </div>
      )}
      {!data && !error && (
        <p className="muted">
          <Loader2 size={15} className="spin" aria-hidden /> Reading the connection list…
        </p>
      )}

      {data && (
        <>
          <div className={`proof-verdict ${verdictTone}`} role="status" data-testid="proof-verdict" data-verdict={data.verdict}>
            {data.verdict === "internet" || data.verdict === "helpers" ? <AlertTriangle size={20} aria-hidden /> : <Check size={20} aria-hidden />}
            <strong>{data.headline}</strong>
          </div>
          {data.helper_advice.length > 0 && (
            <ul className="helper-advice" data-testid="helper-advice">
              {data.helper_advice.map((h) => (
                <li key={h.role}>
                  <strong>{h.role}:</strong> {h.advice}
                </li>
              ))}
            </ul>
          )}

          <div className="proof-tiles">
            <Tile label="outgoing internet connections now" value={data.counts_now.internet ?? 0} tone={data.counts_now.internet ? "bad" : "ok"} />
            <Tile label="made by Krypto or the AI server since start" value={data.ever_external_core} tone={data.ever_external_core ? "bad" : "ok"} />
            <Tile label="made by helper programs since start" value={data.ever_external - data.ever_external_core} tone={data.ever_external - data.ever_external_core ? "warn" : "ok"} />
            <Tile label="attempts blocked by offline mode" value={data.strict ? data.blocked_attempts : "n/a"} tone={data.blocked_attempts ? "warn" : "ok"} />
            <Tile label="connections on this computer" value={data.counts_now["this computer"] ?? 0} />
            <Tile label="watching for" value={duration(data.watching_seconds)} />
          </div>

          <section className="card">
            <h3 className="section-title">Mode</h3>
            <ul className="status-list">
              <li className={data.strict ? "up" : "unknown"}>
                {data.strict ? <Check size={15} /> : <span className="dash">–</span>}
                <span className="row-label">Strict offline mode</span>
                <span className="muted small">
                  {data.strict
                    ? "on: any attempt to reach another computer is refused and counted"
                    : "off: start with  .\\run_demo.ps1 -Offline  to enforce it"}
                </span>
              </li>
              <li className={data.lan_allowed ? "unknown" : "up"}>
                {data.lan_allowed ? <span className="dash">–</span> : <Check size={15} />}
                <span className="row-label">Local network</span>
                <span className="muted small">
                  {data.lan_allowed ? "allowed (private addresses only): two-computer mode" : "not used: this computer only"}
                </span>
              </li>
              <li className={data.cloud_libraries_installed.length ? "down" : "up"}>
                {data.cloud_libraries_installed.length ? <X size={15} /> : <Check size={15} />}
                <span className="row-label">Cloud AI libraries</span>
                <span className="muted small">
                  {data.cloud_libraries_installed.length
                    ? `installed: ${data.cloud_libraries_installed.join(", ")}`
                    : "none installed (no OpenAI, Anthropic, Google, AWS clients)"}
                </span>
              </li>
            </ul>
          </section>

          <section className="card">
            <div className="proof-row-head">
              <h3 className="section-title">Try to reach the internet</h3>
              <button type="button" className="btn primary small" onClick={() => void runTest()} disabled={testing} data-testid="selftest">
                {testing ? <Loader2 size={14} className="spin" aria-hidden /> : <ShieldCheck size={14} aria-hidden />} Run the test
              </button>
            </div>
            <p className="muted small">
              Krypto tries to open a connection to a public internet address, a public DNS server and a website name. With
              strict offline mode on, all three must fail.
            </p>
            {test && (
              <div data-testid="selftest-result">
                <p className={test.ran ? (test.all_refused ? "ok-text" : "bad-text") : "muted"}>
                  <strong>{test.message}</strong>
                </p>
                {test.attempts.length > 0 && (
                  <ul className="status-list">
                    {test.attempts.map((a) => (
                      <li key={a.target} className={a.result.startsWith("refused") ? "up" : "down"}>
                        {a.result.startsWith("refused") ? <Check size={15} /> : <X size={15} />}
                        <span className="row-label">
                          {a.what} <span className="muted small">({a.target})</span>
                        </span>
                        <span className="muted small">{a.result}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          <section className="card">
            <h3 className="section-title">What the operating system says is connected</h3>
            <p className="muted small">
              Read from the operating system for these processes: {data.processes.join(" · ") || "none found"}. It refreshes
              every few seconds. <code>netstat -ano</code> shows the same table.
            </p>
            <div className="table-wrap">
              <table className="proof-table" data-testid="connections">
                <thead>
                  <tr>
                    <th>Program</th>
                    <th>Connected to</th>
                    <th>Where that is</th>
                    <th>Direction / state</th>
                  </tr>
                </thead>
                <tbody>
                  {data.connections_now.length === 0 && (
                    <tr>
                      <td colSpan={4} className="muted">
                        No open connections right now.
                      </td>
                    </tr>
                  )}
                  {data.connections_now.map((c, i) => (
                    <tr key={`${c.pid}-${c.local}-${c.remote}-${i}`}>
                      <td>{c.role}</td>
                      <td>
                        <code>{c.remote}</code>
                      </td>
                      <td>
                        <span className={`scope-tag ${SCOPE_TAG[c.scope] ?? ""}`}>{c.scope}</span>
                      </td>
                      <td className="muted small">
                        {c.direction === "inbound" ? "a browser connected to Krypto" : "started by this program"} · {c.state}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {data.ever_seen.length > 0 && (
            <section className="card">
              <h3 className="section-title">Every destination seen since the server started</h3>
              <div className="table-wrap">
                <table className="proof-table">
                  <thead>
                    <tr>
                      <th>Program</th>
                      <th>Address</th>
                      <th>Where that is</th>
                      <th>First / last seen</th>
                      <th>Connected?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.ever_seen.map((e) => (
                      <tr key={`${e.role}-${e.host}`}>
                        <td>
                          {e.role} <span className={`scope-tag ${e.party === "core" ? "" : "warn"}`}>{e.party === "core" ? "Krypto" : "helper"}</span>
                        </td>
                        <td>
                          <code>{e.host}</code>
                          <div className="muted small">{e.direction === "inbound" ? "incoming" : "outgoing"}</div>
                        </td>
                        <td>
                          <span className={`scope-tag ${e.direction === "inbound" && e.scope !== "internet" ? "ok" : SCOPE_TAG[e.scope] ?? ""}`}>{e.scope}</span>
                        </td>
                        <td className="muted small">
                          {e.first_seen} / {e.last_seen}
                        </td>
                        <td className="muted small">{e.connected ? "yes" : "attempt only"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="card">
            <h3 className="section-title">
              <Cable size={15} aria-hidden /> Network and two-computer access
            </h3>
            <ul className="status-list">
              {(link?.adapters ?? [])
                .filter((a) => a.kind !== "other" || a.up)
                .map((a) => (
                  <li key={a.name} className={a.up ? "up" : "unknown"}>
                    {a.up ? <Check size={15} /> : <span className="dash">–</span>}
                    <span className="row-label">
                      {a.kind === "wifi" ? "Wi-Fi" : a.kind === "ethernet" ? "Ethernet" : "Network"} · {a.name}
                    </span>
                    <span className="muted small">
                      {a.up ? `${a.ipv4.join(", ")}${a.speed_mbps ? ` · ${a.speed_mbps} Mbps` : ""}` : "not connected"}
                    </span>
                  </li>
                ))}
            </ul>
            {data.lan_allowed && link && link.lan_urls.length > 0 ? (
              <>
                <p className="muted small">Open this on the other computer (same cable, switch or network):</p>
                <ul className="lan-urls" data-testid="lan-urls">
                  {link.lan_urls.map((url) => (
                    <li key={url}>
                      <code>{url}</code>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="muted small">
                To use a second computer, start with <code>.\run_demo.ps1 -Lan -Offline</code>. The other computer only needs
                a web browser.
              </p>
            )}
            {data.lan_connections.length > 0 && (
              <>
                <p className="muted small">Connections this server made to other computers on the local network:</p>
                <ul className="lan-urls">
                  {data.lan_connections.map((c) => (
                    <li key={`${c.host}:${c.port}`}>
                      <code>
                        {c.host}:{c.port}
                      </code>{" "}
                      <span className="muted small">× {c.count}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          {routing && (
            <section className="card" data-testid="model-table">
              <h3 className="section-title">Which model handles which task</h3>
              <div className="table-wrap">
                <table className="proof-table">
                  <thead>
                    <tr>
                      <th>Kind of task</th>
                      <th>Model the design calls for</th>
                      <th>Model running here</th>
                    </tr>
                  </thead>
                  <tbody>
                    {routing.models.map((m) => (
                      <tr key={m.task_type}>
                        <td>
                          <strong>{m.task_type}</strong> <span className="muted small">{m.roles.slice(0, 3).join(", ")}</span>
                        </td>
                        <td>
                          <code>{m.designed_model}</code>
                        </td>
                        <td>
                          <code>{m.running_model}</code>{" "}
                          <span className={`scope-tag ${m.installed ? "ok" : "bad"}`}>{m.installed ? "installed" : "missing"}</span>
                          {m.stand_in && <span className="muted small"> · stand-in for this computer</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="card">
            <div className="proof-row-head">
              <h3 className="section-title">Audit log</h3>
              <button type="button" className="btn ghost small" onClick={() => void load()}>
                <RefreshCw size={13} aria-hidden /> Refresh
              </button>
            </div>
            <p className="muted small">
              Everything above is also written to <code>{data.audit_log}</code>. Last lines:
            </p>
            <pre className="audit-log" data-testid="audit-log">
              {data.audit_tail.length ? data.audit_tail.join("\n") : "(nothing to report: no refused or outside connections)"}
            </pre>
          </section>

          <section className="card">
            <h3 className="section-title">
              <Network size={15} aria-hidden /> Check it yourself
            </h3>
            <ol className="how-to">
              {data.how_to_check.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
          </section>
        </>
      )}
    </div>
  );
}
