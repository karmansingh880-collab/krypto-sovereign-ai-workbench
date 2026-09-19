import { useCallback, useRef, useState } from "react";
import { LogOut, Monitor, Moon, Sun } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useTheme, type ThemeChoice } from "../context/ThemeContext";
import { useDismiss } from "../hooks/useDismiss";

const THEMES: { value: ThemeChoice; label: string; icon: typeof Sun }[] = [
  { value: "system", label: "System", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
];

export function UserMenu() {
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  if (!user) return null;

  return (
    <div className="menu-wrap" ref={ref}>
      <button
        type="button"
        className="user-button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="avatar" aria-hidden>{user.name.trim().charAt(0).toUpperCase() || "?"}</span>
        <span className="user-name" data-testid="user-name">{user.name}</span>
      </button>
      {open && (
        <div className="popover right" role="menu">
          <div className="user-card">
            <span className="avatar big" aria-hidden>{user.name.trim().charAt(0).toUpperCase() || "?"}</span>
            <div>
              <strong>{user.name}</strong>
              <div className="muted small">{user.email}</div>
              {user.is_demo && <span className="tag">Demo account</span>}
            </div>
          </div>
          <div className="menu-section">
            <span className="menu-label">Theme</span>
            <div className="segmented" role="radiogroup" aria-label="Theme">
              {THEMES.map(({ value, label, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={theme === value}
                  className={theme === value ? "active" : ""}
                  onClick={() => setTheme(value)}
                >
                  <Icon size={14} aria-hidden /> {label}
                </button>
              ))}
            </div>
          </div>
          <button type="button" className="menu-item" role="menuitem" onClick={() => void logout()}>
            <LogOut size={16} aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
