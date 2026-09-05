import { useCallback, useRef } from "react";
import { EngineControls } from "./useEntityEngine";
import * as api from "./api";
import { downsample, floatTo16BitPCM, nextEntityDirective, normalizeActivity, rms } from "./voiceEntity";

// PART 12d/12e: a real /ws/voice session, wired to the same protocol the
// laptop client speaks (app.voice, Parts 12a-12c), driving the existing
// Part 11 entity with the server's own confirmed state - never a frontend
// guess. This is explicit-activation only - the mic button starts one
// utterance, never a hidden always-listening stream. No transcript/response
// is ever fabricated; everything shown/spoken/animated comes from the server
// or from real, currently-flowing audio (see voiceEntity.ts for the pure
// mapping/activity math this hook wires up to I/O).

const TARGET_SAMPLE_RATE = 16000;
const SILENCE_RMS_THRESHOLD = 500;
const SILENCE_DURATION_MS = 1200;
const MAX_UTTERANCE_MS = 30000;
const DEVICE_ID_KEY = "friday.voiceDeviceId";

// Loopback traffic reaches the backend through the Docker bridge, so
// Tailscale peer identity can't be resolved for it and FIRDAY would mark a
// random browser device UNVERIFIED. The boot-time "local" device is already
// registered and trusted via the Pi's own Tailscale self identity, so the
// loopback dashboard reuses it instead of registering a new one.
export function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

// A tool-executing turn (Gmail, etc.) leaves the mic-capture audio graph
// torn down and silent for however long the tool + cloud finalize call
// take, before the response's own audio exists to play - exactly the
// "quiet AudioContext" browsers' power-saving auto-suspend targets. A
// quick tool-free chat reply rarely idles long enough to trigger it, which
// is why only tool-executing responses go silent. Piper still synthesizes
// and the bytes still arrive; the browser just never resumes playback for
// them without this check.
export function shouldResumeAudioContext(state: AudioContextState): boolean {
  return state === "suspended";
}

function getOrCreateDeviceId(): string {
  if (isLoopbackHost(location.hostname)) return "local";
  try {
    let id = localStorage.getItem(DEVICE_ID_KEY);
    if (!id) {
      id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `browser-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      localStorage.setItem(DEVICE_ID_KEY, id);
    }
    return id;
  } catch {
    return "browser-session";
  }
}

export function useVoiceSession(
  ctl: EngineControls,
  show: (text: string) => void,
  settle: () => void,
  onEnded?: () => void,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const muteRef = useRef<GainNode | null>(null);
  const responseSampleRate = useRef(22050);
  const silenceMs = useRef(0);
  const utteranceStartedAt = useRef(0);
  const endedUtterance = useRef(false);

  const audioContext = useCallback(() => {
    if (!audioCtxRef.current) audioCtxRef.current = new AudioContext();
    return audioCtxRef.current;
  }, []);

  const stopMic = useCallback(() => {
    processorRef.current?.disconnect();
    muteRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    processorRef.current = null;
    muteRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
  }, []);

  const closeSession = useCallback(() => {
    stopMic();
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
  }, [stopMic]);

  const playResponseChunk = useCallback(
    (buf: ArrayBuffer) => {
      const ctx = audioContext();
      // A tool-executing turn (Gmail, etc.) leaves the mic-capture audio
      // graph torn down and silent for however long the tool + cloud
      // finalize call take, before any response audio exists to play. A
      // quiet AudioContext for that stretch is exactly what browsers'
      // power-saving auto-suspend targets, so a slow tool turn can leave
      // the context suspended by the time Piper's bytes actually arrive -
      // silently: no error, the already-shown text is unaffected. Resuming
      // on every chunk is a cheap no-op once already running.
      if (shouldResumeAudioContext(ctx.state)) void ctx.resume();
      const samples = new Int16Array(buf);
      // Real playback amplitude, same rms() used for the mic - not a fake
      // pulse. Only meaningful while RESPONDING; harmless if applied
      // slightly early/late since setState always resets target on its own.
      ctl.setActivity(normalizeActivity(rms(samples)));

      const audioBuffer = ctx.createBuffer(1, samples.length || 1, responseSampleRate.current);
      const channel = audioBuffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 0x8000;
      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);
      source.start();
    },
    [audioContext, ctl],
  );

  const endUtterance = useCallback(
    (ws: WebSocket) => {
      if (endedUtterance.current) return;
      endedUtterance.current = true;
      stopMic();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "audio.end" }));
        // Sending audio.end unconditionally moves the server session
        // LISTENING -> THINKING (see app/voice/pipeline.py) before STT even
        // runs - so this reflects a guaranteed transition, not a guess.
        ctl.setState("thinking");
      }
    },
    [stopMic, ctl],
  );

  const beginCapture = useCallback(
    async (ws: WebSocket) => {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      streamRef.current = stream;
      const ctx = audioContext();
      const source = ctx.createMediaStreamSource(stream);
      sourceRef.current = source;
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;
      const mute = ctx.createGain();
      mute.gain.value = 0; // capture-only: never route the mic to the speakers
      muteRef.current = mute;

      silenceMs.current = 0;
      utteranceStartedAt.current = performance.now();
      endedUtterance.current = false;

      ws.send(JSON.stringify({ type: "audio.start", format: "pcm16", sample_rate: TARGET_SAMPLE_RATE, channels: 1 }));

      processor.onaudioprocess = (event) => {
        if (ws.readyState !== WebSocket.OPEN || endedUtterance.current) return;
        const down = downsample(event.inputBuffer.getChannelData(0), ctx.sampleRate, TARGET_SAMPLE_RATE);
        const pcm16 = floatTo16BitPCM(down);
        ws.send(pcm16.buffer);

        // Real mic amplitude drives the entity while LISTENING - the same
        // frame already being sent to the Pi, not a second capture path.
        // Jitter/decay is handled by EntityController's existing smoothing,
        // not duplicated here.
        const amplitude = rms(pcm16);
        ctl.setActivity(normalizeActivity(amplitude));

        silenceMs.current = amplitude < SILENCE_RMS_THRESHOLD ? silenceMs.current + (down.length / TARGET_SAMPLE_RATE) * 1000 : 0;
        const elapsedMs = performance.now() - utteranceStartedAt.current;
        if (silenceMs.current >= SILENCE_DURATION_MS || elapsedMs >= MAX_UTTERANCE_MS) {
          endUtterance(ws);
        }
      };

      source.connect(processor);
      processor.connect(mute);
      mute.connect(ctx.destination);
    },
    [audioContext, ctl, endUtterance],
  );

  const fail = useCallback(
    (message: string) => {
      show(message);
      closeSession();
      settle();
      onEnded?.();
    },
    [show, closeSession, settle, onEnded],
  );

  const start = useCallback(async () => {
    const deviceId = getOrCreateDeviceId();
    if (deviceId !== "local") {
      // Reuses the existing Part 5 device trust flow - never a second auth
      // system. Trust is derived from Tailscale identity on this call, not
      // asserted by the browser.
      await api.post("/devices", { name: "browser", device_id: deviceId });
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/voice`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "session.start", device_id: deviceId, client: "browser", protocol_version: "1" }));
    };
    ws.onerror = () => fail("Voice connection failed.");
    // An unexpected drop (network loss, server restart, ...) must return the
    // entity to idle too - not just a clean voice.response.end/error. Safe
    // to call even after a clean end: setState/onEnded are idempotent.
    ws.onclose = () => {
      stopMic();
      ctl.setState("idle");
      onEnded?.();
    };

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        playResponseChunk(event.data);
        return;
      }
      const msg = JSON.parse(event.data as string);

      // session.start's own accepted-session ack isn't a voice.state - it's
      // the cue to request the very first (always-valid) transition.
      if (msg.type === "session.accepted") {
        ws.send(JSON.stringify({ type: "voice.state", state: "wake" }));
        return;
      }

      const directive = nextEntityDirective(msg);
      if (directive?.kind === "state") {
        ctl.setState(directive.state);
        if (directive.state === "wake") {
          ws.send(JSON.stringify({ type: "voice.state", state: "listening" }));
        } else if (directive.state === "listening") {
          void beginCapture(ws);
        } else if (directive.state === "idle") {
          responseSampleRate.current = 22050;
        }
      } else if (directive?.kind === "text") {
        show(directive.text);
      }

      if (msg.type === "voice.response.start") {
        responseSampleRate.current = msg.sample_rate || 22050;
      } else if (msg.type === "error") {
        fail(msg.message || "Voice error.");
      } else if (msg.type === "voice.response.end") {
        settle();
        closeSession();
        onEnded?.();
      }
    };
  }, [ctl, show, settle, fail, closeSession, stopMic, beginCapture, playResponseChunk, onEnded]);

  const stop = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      endUtterance(ws);
      ws.send(JSON.stringify({ type: "session.end" }));
    }
    closeSession();
  }, [endUtterance, closeSession]);

  return { start, stop };
}
