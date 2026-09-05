import { describe, expect, it } from "vitest";
import { isLoopbackHost, shouldResumeAudioContext } from "./useVoiceSession";

// PART 12 fix: loopback browser traffic can't be Tailscale-identified (it
// arrives via the Docker bridge), so it must reuse the already-trusted
// "local" device instead of registering a new UNVERIFIED one. This is the
// decision this fix hinges on - see useVoiceSession.ts for where it's used
// to pick device_id "local" (no POST /devices) vs. the existing random-UUID
// registration flow.

describe("isLoopbackHost", () => {
  it("treats localhost, 127.0.0.1 and ::1 as loopback -> reuse the trusted local device_id, no POST /devices", () => {
    expect(isLoopbackHost("localhost")).toBe(true);
    expect(isLoopbackHost("127.0.0.1")).toBe(true);
    expect(isLoopbackHost("::1")).toBe(true);
  });

  it("treats any other hostname as non-loopback -> existing random UUID + POST /devices behavior", () => {
    expect(isLoopbackHost("friday.tailnet.ts.net")).toBe(false);
    expect(isLoopbackHost("192.168.1.50")).toBe(false);
  });
});

describe("shouldResumeAudioContext", () => {
  it("reproduces the tool-execution voice bug: a suspended context (the state a slow Gmail + finalize turn can leave it in while final text is already shown) must be resumed before the TTS/audio response plays", () => {
    expect(shouldResumeAudioContext("suspended")).toBe(true);
  });

  it("is a no-op for a context that never needed resuming (the fast, tool-free chat reply path that already worked)", () => {
    expect(shouldResumeAudioContext("running")).toBe(false);
    expect(shouldResumeAudioContext("closed")).toBe(false);
  });
});
