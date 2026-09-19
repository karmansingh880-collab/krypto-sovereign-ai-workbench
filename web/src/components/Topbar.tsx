import { Link } from "react-router-dom";
import { Menu, ShieldCheck } from "lucide-react";
import { NetworkBadge } from "./NetworkBadge";
import { ServicesMenu } from "./ServicesMenu";
import { UserMenu } from "./UserMenu";

export function Brand() {
  return (
    <Link to="/" className="brand" aria-label="Krypto home">
      <span className="logo" aria-hidden>K</span>
      <span className="brand-text">
        <strong>Krypto</strong>
        <span className="muted small">Sovereign AI Workbench</span>
      </span>
    </Link>
  );
}

export function Topbar({ onMenu }: { onMenu: () => void }) {
  return (
    <header className="topbar">
      <button type="button" className="icon-btn menu-toggle" aria-label="Open history" onClick={onMenu}>
        <Menu size={20} />
      </button>
      <Brand />
      <div className="topbar-spacer" />
      <Link to="/proof" className="icon-btn" aria-label="Sovereignty proof" title="Sovereignty proof: what is connected to what" data-testid="proof-nav">
        <ShieldCheck size={18} />
      </Link>
      <NetworkBadge />
      <ServicesMenu />
      <UserMenu />
    </header>
  );
}
