import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { ENTITY_STATES, EntityState } from "../types";
import { DrawerRow, useDrawerData } from "../hooks/useDrawerData";

const Row = ({ row }: { row: DrawerRow }) => (
  <div className="drow">
    <span className="drow-name">{row.name}</span>
    <span className={`drow-status tone-${row.tone}`}>{row.status}</span>
    <span className="drow-metric">{row.metric}</span>
    {row.action && (
      <button className={`drow-action${row.action.amber ? " amber" : ""}`} disabled={row.action.disabled} onClick={row.action.onClick}>
        {row.action.label}
      </button>
    )}
  </div>
);

interface Props {
  open: boolean;
  state: EntityState;
  reducedMotion: boolean;
  onClose: () => void;
  onSelectState: (s: EntityState) => void;
}

export const ControlDrawer = ({ open, state, reducedMotion, onClose, onSelectState }: Props) => {
  const [toast, setToast] = useState<{ text: string; amber?: boolean } | null>(null);
  const toastTimer = useRef<number | null>(null);

  const notify = useCallback((text: string, amber?: boolean) => {
    setToast({ text, amber });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3200);
  }, []);

  const sections = useDrawerData(open, notify);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <div className="drawer-scrim" onClick={onClose} data-testid="drawer-scrim" />
          <motion.aside
            className="drawer"
            data-testid="left-control-drawer"
            initial={{ x: reducedMotion ? 0 : "-100%", opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: reducedMotion ? 0 : "-100%", opacity: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 32, opacity: { duration: 0.25 } }}
          >
            <div className="drawer-head">
              <span className="drawer-title">instrumentation</span>
              <button className="icon-btn" onClick={onClose} aria-label="Close drawer" data-testid="drawer-close-button">
                <X size={16} strokeWidth={1.5} />
              </button>
            </div>
            <div className="drawer-body">
              <section className="drawer-section" data-testid="drawer-section-entity">
                <h3>Entity state</h3>
                <div className="state-grid">
                  {ENTITY_STATES.map((s) => (
                    <button
                      key={s}
                      className={`state-btn${s === state ? " active" : ""}`}
                      onClick={() => onSelectState(s)}
                      data-testid={`state-button-${s}`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </section>
              {sections.map((sec) => (
                <section key={sec.id} className="drawer-section" data-testid={`drawer-section-${sec.id}`}>
                  <h3>{sec.title}</h3>
                  {sec.rows.length === 0 && <div className="drow-empty">unavailable</div>}
                  {sec.rows.map((r) => (
                    <Row key={r.name} row={r} />
                  ))}
                </section>
              ))}
            </div>
            {toast && <div className={`drawer-toast${toast.amber ? " amber" : ""}`}>{toast.text}</div>}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
};
