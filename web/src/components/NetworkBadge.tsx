import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Cable, Check, Loader2, RefreshCw, ShieldCheck, Wifi, WifiOff, X } from "lucide-react";
import { useDismiss } from "../hooks/useDismiss";
import { useNetwork } from "../hooks/useNetwork";
import { useNow } from "../hooks/useNow";
import { relativeTime } from "../utils/time";

function Row({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) {
  return (
    <li className={ok === null ? "unknown" : ok ? "up" : "down"}>
      {ok === null ? <span className="dash">–</span> : ok ? <Check size={15} /> : <X size={15} />}
      <span className="row-label">{label}</span>
      {detail && <span className="muted small">{detail}</span>}
    </li>
  );
}

/** Shows whether this computer is online. Krypto never needs the internet: this only reports the state. */
export function NetworkBadge() {
  const { status, live, browserOnline, refresh } = useNetwork();
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const now = useNow(open, 5000);

  const check = async () => {
    setChecking(true);
    await refresh();
    setChecking(false);
  };

  let label = "Checking network…";
  let tone = "checking";
  let Icon = Wifi;
  if (status?.strict) {
    label = "Offline mode";
    tone = "strict";
    Icon = ShieldCheck;
  } else if (status && status.online && browserOnline) {
    label = "Online";
    tone = "online";
  } else if (status) {
    label = "Offline · running locally";
    tone = "offline";
    Icon = WifiOff;
  }

  return (
    <div className="menu-wrap" ref={ref}>
      <button
        type="button"
        className={`net-pill ${tone}`}
        aria-haspopup="true"
        aria-expanded={open}
        data-testid="network-badge"
        title="Network status"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon size={14} aria-hidden />
        <span className="net-label">{label}</span>
      </button>

      {open && (
        <div className="popover right net-popover" role="dialog" aria-label="Network status">
          <h3>Network</h3>
          {status ? (
            <>
              <ul className="status-list">
                {status.strict ? (
                  <Row ok={false} label="Internet" detail="blocked by offline mode" />
                ) : (
                  <Row
                    ok={status.online}
                    label="Internet"
                    detail={status.online ? `reachable · ${status.latency_ms} ms` : "not reachable"}
                  />
                )}
                {status.wifi ? (
                  <Row
                    ok={status.wifi.connected}
                    label="Wi-Fi"
                    detail={
                      status.wifi.connected
                        ? `${status.wifi.ssid ?? "connected"}${status.wifi.signal ? ` · ${status.wifi.signal}` : ""}`
                        : "not connected"
                    }
                  />
                ) : (
                  <Row ok={null} label="Wi-Fi" detail="not reported" />
                )}
                {status.link && status.link.kind !== "unknown" && (
                  <Row
                    ok={status.link.adapters.some((a) => a.kind === "ethernet" && a.up)}
                    label="Ethernet"
                    detail={(() => {
                      const eth = status.link.adapters.find((a) => a.kind === "ethernet" && a.up);
                      return eth ? `${eth.ipv4[0]}${eth.speed_mbps ? ` · ${eth.speed_mbps} Mbps` : ""}` : "no cable connected";
                    })()}
                  />
                )}
                <Row ok={browserOnline} label="This browser" detail={browserOnline ? "online" : "offline"} />
              </ul>

              <p className="net-note">
                <strong>Krypto works fully offline.</strong> The AI models, text recognition, database and search all
                run on this computer, so no internet is needed for anything you do here.
              </p>

              {status.lan_allowed && status.link && status.link.lan_urls.length > 0 && (
                <p className="net-note" data-testid="lan-hint">
                  <Cable size={13} aria-hidden /> Two-computer mode. On the other computer open{" "}
                  <strong>{status.link.lan_urls[0]}</strong>
                </p>
              )}

              {status.strict && (
                <div className="net-strict">
                  <p>
                    <ShieldCheck size={14} aria-hidden /> Offline mode is enforced: connections to other computers are
                    refused. Blocked so far: <strong data-testid="blocked-count">{status.blocked_attempts}</strong>
                  </p>
                  {status.recent_blocked.length > 0 && (
                    <ul className="blocked-list">
                      {status.recent_blocked.slice(-4).map((b, i) => (
                        <li key={`${b.time}-${i}`}>
                          {b.time} · {b.host}
                          {b.port ? `:${b.port}` : ""}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <Link to="/proof" className="net-proof-link" onClick={close} data-testid="proof-link">
                <ShieldCheck size={14} aria-hidden /> See the proof: what is connected to what
              </Link>

              <div className="net-foot">
                <span className="muted small">
                  Checked {relativeTime(status.checked_at, now)} · {live ? "live updates" : "checking every 10 s"}
                </span>
                <button type="button" className="btn ghost small" onClick={() => void check()} disabled={checking}>
                  {checking ? <Loader2 size={13} className="spin" aria-hidden /> : <RefreshCw size={13} aria-hidden />}
                  Check now
                </button>
              </div>
            </>
          ) : (
            <p className="muted small">Checking…</p>
          )}
        </div>
      )}
    </div>
  );
}
