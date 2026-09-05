import { EntityState } from "./types";

// PART 12e: pure voice-state -> entity-state mapping and activity-level
// math. Deliberately free of DOM/WebSocket/AudioContext - the real voice
// session (useVoiceSession.ts) is the only thing that touches those; this
// module is what makes the mapping/activity math independently testable.

const VOICE_TO_ENTITY: Record<string, EntityState> = {
  idle: "idle",
  wake: "wake",
  listening: "listening",
  paused: "pause",
  thinking: "thinking",
  responding: "responding",
};

// Maps a real, server-confirmed voice state to the existing Part 11 entity
// states. Falls back to "idle" for anything unrecognized - never invents a
// visual state that isn't one of Part 11's own.
export function mapVoiceStateToEntityState(voiceState: string): EntityState {
  return VOICE_TO_ENTITY[voiceState] ?? "idle";
}

export type EntityDirective =
  | { kind: "state"; state: EntityState }
  | { kind: "text"; text: string }
  | null;

interface VoiceMessage {
  type?: string;
  state?: string;
  text?: string;
  [key: string]: unknown;
}

// Translates one already-decoded /ws/voice JSON message into what the
// entity should do next - a real, server-confirmed state change, a line of
// text to show, or nothing. Pure and stateless: every call is independent,
// so no session's messages can ever leak into another's entity state.
export function nextEntityDirective(message: VoiceMessage): EntityDirective {
  switch (message.type) {
    case "voice.state.accepted":
      return { kind: "state", state: mapVoiceStateToEntityState(message.state ?? "") };
    case "voice.response.start":
      return { kind: "state", state: "responding" };
    case "voice.response.end":
    case "error":
      return { kind: "state", state: "idle" };
    case "voice.response":
      return { kind: "text", text: message.text ?? "" };
    default:
      return null; // voice.transcript, session.accepted, session.ended, ...
  }
}

// Reference loudness (PCM16 RMS) treated as "loud speech" -> activity 1.0.
// Silence (rms 0) -> activity 0. Deterministic: the same amplitude always
// yields the same activity, no randomness, no synthetic pulsing.
const LOUD_REFERENCE_RMS = 6000;

export function normalizeActivity(amplitudeRms: number, referenceRms: number = LOUD_REFERENCE_RMS): number {
  if (!Number.isFinite(amplitudeRms) || amplitudeRms <= 0) return 0;
  return Math.max(0, Math.min(1, amplitudeRms / referenceRms));
}

export function rms(samples: Int16Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

export function floatTo16BitPCM(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function downsample(input: Float32Array, inputRate: number, targetRate: number): Float32Array {
  if (targetRate >= inputRate) return input;
  const ratio = inputRate / targetRate;
  const out = new Float32Array(Math.round(input.length / ratio));
  for (let i = 0; i < out.length; i++) out[i] = input[Math.floor(i * ratio)];
  return out;
}
