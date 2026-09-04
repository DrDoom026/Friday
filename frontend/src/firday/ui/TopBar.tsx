import { useEffect, useRef } from "react";
import { Menu } from "lucide-react";
import { EntityEngine, EntityState } from "../types";

const ActivityMeter = ({ engine }: { engine: EntityEngine }) => {
  const bar = useRef<HTMLElement>(null);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      if (bar.current) bar.current.style.transform = `scaleX(${engine.activity.toFixed(3)})`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [engine]);
  return (
    <span className="state-meter" aria-hidden>
      <i ref={bar} />
    </span>
  );
};

interface Props {
  state: EntityState;
  engine: EntityEngine;
  onMenu: () => void;
}

export const TopBar = ({ state, engine, onMenu }: Props) => (
  <header className="topbar" data-testid="top-status-strip">
    <div className="topbar-left">
      <span className="wordmark" data-testid="firday-wordmark">FIRDAY</span>
    </div>
    <div className="topbar-right">
      <div className="state-readout" data-testid="entity-state-label" data-state={state}>
        <ActivityMeter engine={engine} />
        <span>{state}</span>
      </div>
      <span className="online" data-testid="online-status-dot">
        <i className="online-dot" />
        <span className="online-text">online</span>
      </span>
      <button className="icon-btn" onClick={onMenu} aria-label="Open control drawer" data-testid="hamburger-menu-button">
        <Menu size={18} strokeWidth={1.5} />
      </button>
    </div>
  </header>
);
