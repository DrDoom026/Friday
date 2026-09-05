import { describe, expect, it } from "vitest";
import {
  downsample,
  mapVoiceStateToEntityState,
  nextEntityDirective,
  normalizeActivity,
  rms,
} from "./voiceEntity";

// PART 12e: the real /ws/voice session drives the Part 11 entity through
// these pure functions. No WebSocket/AudioContext/mic is needed to verify
// the mapping and activity math - see useVoiceSession.ts for the I/O that
// wires them up.

describe("mapVoiceStateToEntityState", () => {
  it("maps every real voice state to its existing entity state", () => {
    expect(mapVoiceStateToEntityState("idle")).toBe("idle");
    expect(mapVoiceStateToEntityState("wake")).toBe("wake");
    expect(mapVoiceStateToEntityState("listening")).toBe("listening");
    expect(mapVoiceStateToEntityState("paused")).toBe("pause");
    expect(mapVoiceStateToEntityState("thinking")).toBe("thinking");
    expect(mapVoiceStateToEntityState("responding")).toBe("responding");
  });

  it("falls back to idle for anything unrecognized, never invents a state", () => {
    expect(mapVoiceStateToEntityState("some-future-state")).toBe("idle");
    expect(mapVoiceStateToEntityState("")).toBe("idle");
  });
});

describe("nextEntityDirective", () => {
  it("turns a voice.state.accepted ack into a confirmed state directive", () => {
    expect(nextEntityDirective({ type: "voice.state.accepted", state: "listening" })).toEqual({
      kind: "state",
      state: "listening",
    });
    expect(nextEntityDirective({ type: "voice.state.accepted", state: "paused" })).toEqual({
      kind: "state",
      state: "pause",
    });
  });

  it("maps voice.response.start to responding", () => {
    expect(nextEntityDirective({ type: "voice.response.start", session_id: "s1" })).toEqual({
      kind: "state",
      state: "responding",
    });
  });

  it("returns the session to idle on voice.response.end", () => {
    expect(nextEntityDirective({ type: "voice.response.end", status: "ok" })).toEqual({
      kind: "state",
      state: "idle",
    });
  });

  it("returns the session to idle on a server error, without executing anything", () => {
    expect(nextEntityDirective({ type: "error", code: "STT_FAILED", message: "boom" })).toEqual({
      kind: "state",
      state: "idle",
    });
  });

  it("surfaces voice.response as text, not a state change", () => {
    expect(nextEntityDirective({ type: "voice.response", text: "hi there" })).toEqual({
      kind: "text",
      text: "hi there",
    });
  });

  it("ignores messages with no entity-relevant state (e.g. a transcript)", () => {
    expect(nextEntityDirective({ type: "voice.transcript", text: "turn on the lights" })).toBeNull();
    expect(nextEntityDirective({ type: "session.accepted", session_id: "s1" })).toBeNull();
    expect(nextEntityDirective({ type: "session.ended" })).toBeNull();
  });

  it("is a pure function: identical input always yields identical output (no session leakage)", () => {
    const message = { type: "voice.state.accepted", state: "thinking" };
    const first = nextEntityDirective({ ...message });
    const second = nextEntityDirective({ ...message });
    expect(first).toEqual(second);
  });
});

describe("normalizeActivity", () => {
  it("normalizes to the 0..1 range", () => {
    expect(normalizeActivity(0)).toBe(0);
    expect(normalizeActivity(6000)).toBe(1);
    expect(normalizeActivity(3000)).toBeCloseTo(0.5);
  });

  it("clamps above the loud reference instead of exceeding 1", () => {
    expect(normalizeActivity(60000)).toBe(1);
  });

  it("decays to exactly zero for silence", () => {
    expect(normalizeActivity(0)).toBe(0);
    expect(normalizeActivity(-5)).toBe(0);
  });

  it("distinguishes quiet from loud speech", () => {
    const quiet = normalizeActivity(1000);
    const loud = normalizeActivity(5000);
    expect(loud).toBeGreaterThan(quiet);
  });

  it("is deterministic - never random", () => {
    const a = normalizeActivity(2345);
    const b = normalizeActivity(2345);
    expect(a).toBe(b);
  });
});

describe("rms", () => {
  it("is zero for silence", () => {
    expect(rms(new Int16Array(320))).toBe(0);
  });

  it("is higher for louder samples", () => {
    const quiet = new Int16Array(10).fill(500);
    const loud = new Int16Array(10).fill(5000);
    expect(rms(loud)).toBeGreaterThan(rms(quiet));
  });
});

describe("downsample", () => {
  it("reduces sample count in proportion to the rate ratio", () => {
    const input = new Float32Array(480); // 10ms @ 48000Hz
    const out = downsample(input, 48000, 16000);
    expect(out.length).toBe(160); // 10ms @ 16000Hz
  });

  it("is a no-op when the target rate is not lower", () => {
    const input = new Float32Array([1, 2, 3]);
    expect(downsample(input, 16000, 16000)).toBe(input);
  });
});
