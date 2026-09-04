export type EntityState =
  | "idle"
  | "wake"
  | "listening"
  | "pause"
  | "thinking"
  | "responding";

export const ENTITY_STATES: EntityState[] = [
  "idle",
  "wake",
  "listening",
  "pause",
  "thinking",
  "responding",
];

// activity: target activityLevel 0..1, bias: -1 cool .. +1 warm
export const STATE_PARAMS: Record<EntityState, { activity: number; bias: number }> = {
  idle: { activity: 0.18, bias: 0.0 },
  wake: { activity: 0.4, bias: 0.45 },
  listening: { activity: 0.6, bias: -0.65 },
  pause: { activity: 0.2, bias: -0.2 },
  thinking: { activity: 0.85, bias: -0.35 },
  responding: { activity: 1.0, bias: 0.75 },
};

export interface EntityEngine {
  state: EntityState;
  target: number;
  level: number;
  pulse: number;
  bias: number;
  biasTarget: number;
  activity: number;
  time: number;
  rot: number;
}

export const createEngine = (): EntityEngine => ({
  state: "idle",
  target: STATE_PARAMS.idle.activity,
  level: 0.05,
  pulse: 0,
  bias: 0,
  biasTarget: 0,
  activity: 0.05,
  time: 0,
  rot: 0,
});

export type PerformanceTier = "high" | "mid" | "low";
