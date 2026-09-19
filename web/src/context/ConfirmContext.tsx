import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
}

type Ask = (options: ConfirmOptions) => Promise<boolean>;
const ConfirmContext = createContext<Ask | null>(null);

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

/** An in-app confirmation dialog: `const ok = await confirm({ title, message })`. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);

  const ask = useCallback<Ask>(
    (options) => new Promise<boolean>((resolve) => setPending({ ...options, resolve })),
    [],
  );
  const close = useCallback(
    (ok: boolean) => {
      pending?.resolve(ok);
      setPending(null);
    },
    [pending],
  );

  useEffect(() => {
    if (!pending) return;
    confirmButton.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pending, close]);

  const value = useMemo(() => ask, [ask]);
  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {pending && (
        <div className="modal-backdrop" onClick={() => close(false)}>
          <div
            className="modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            aria-describedby="confirm-message"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="confirm-title">{pending.title}</h2>
            <p id="confirm-message">{pending.message}</p>
            <div className="modal-actions">
              <button type="button" className="btn ghost" onClick={() => close(false)}>
                Cancel
              </button>
              <button
                ref={confirmButton}
                type="button"
                className={`btn ${pending.danger ? "danger-solid" : "primary"}`}
                onClick={() => close(true)}
              >
                {pending.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("useConfirm must be used inside ConfirmProvider");
  return ask;
}
