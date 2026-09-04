import { useCallback, useMemo, useRef, useState } from "react";
import { createEngine, EntityEngine, EntityState, STATE_PARAMS } from "./types";

export interface EngineControls {
  engine: EntityEngine;
  setState: (s: EntityState) => void;
  schedule: (fn: () => void, ms: number) => void;
  clearTimers: () => void;
  pulse: (amount: number) => void;
  current: () => EntityState;
}

export function useEntityEngine() {
  const engineRef = useRef<EntityEngine | null>(null);
  if (!engineRef.current) engineRef.current = createEngine();
  const engine = engineRef.current;
  const [state, setStateRaw] = useState<EntityState>("idle");
  const timers = useRef<number[]>([]);

  const setState = useCallback(
    (s: EntityState) => {
      engine.state = s;
      engine.target = STATE_PARAMS[s].activity;
      engine.biasTarget = STATE_PARAMS[s].bias;
      setStateRaw(s);
    },
    [engine],
  );

  const clearTimers = useCallback(() => {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
  }, []);

  const schedule = useCallback((fn: () => void, ms: number) => {
    timers.current.push(window.setTimeout(fn, ms));
  }, []);

  const pulse = useCallback(
    (amount: number) => {
      engine.pulse = Math.min(0.45, engine.pulse + amount);
    },
    [engine],
  );

  const controls = useMemo<EngineControls>(
    () => ({ engine, setState, schedule, clearTimers, pulse, current: () => engine.state }),
    [engine, setState, schedule, clearTimers, pulse],
  );

  return { state, controls };
}
