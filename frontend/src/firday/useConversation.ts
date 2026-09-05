import { useCallback, useRef, useState } from "react";
import { EngineControls } from "./useEntityEngine";
import { EntityState } from "./types";
import * as api from "./api";
import { useVoiceSession } from "./useVoiceSession";

export interface Line {
  id: number;
  text: string;
}

const QUIET: EntityState[] = ["idle", "pause", "wake"];

// Real chat, unlike the Emergent prototype this was ported from: every
// spoken line comes from FIRDAY's own POST /request, never a canned reply.
export function useConversation(ctl: EngineControls) {
  const [active, setActive] = useState<Line | null>(null);
  const [previous, setPrevious] = useState<string | null>(null);
  const [micActive, setMicActive] = useState(false);
  const activeRef = useRef<Line | null>(null);
  const focused = useRef(false);

  const settle = useCallback(() => {
    ctl.setState("pause");
    ctl.schedule(() => ctl.setState(focused.current ? "listening" : "idle"), 2600);
  }, [ctl]);

  const show = useCallback((text: string) => {
    if (activeRef.current) setPrevious(activeRef.current.text);
    const line = { id: Date.now(), text };
    activeRef.current = line;
    setActive(line);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const t = text.trim();
      if (!t) return;
      ctl.clearTimers();
      ctl.setState("thinking");
      const res = await api.post<{ output?: string }>("/request", { input: t });
      const reply =
        res.ok && res.body ? res.body.output || "(no response)" : "unavailable — the request could not be completed.";
      show(reply);
      ctl.setState("responding");
    },
    [ctl, show],
  );

  const onRevealComplete = useCallback(() => {
    if (ctl.current() === "responding") settle();
  }, [ctl, settle]);

  const wake = useCallback(() => {
    if (!QUIET.includes(ctl.current())) return;
    ctl.clearTimers();
    ctl.setState("wake");
    ctl.pulse(0.2);
    ctl.schedule(() => ctl.setState(focused.current ? "listening" : "idle"), 2200);
  }, [ctl]);

  const focus = useCallback(() => {
    focused.current = true;
    if (QUIET.includes(ctl.current())) {
      ctl.clearTimers();
      ctl.setState("listening");
    }
  }, [ctl]);

  const blur = useCallback(() => {
    focused.current = false;
    if (ctl.current() === "listening" && !micActive) {
      ctl.setState("pause");
      ctl.schedule(() => ctl.setState("idle"), 3000);
    }
  }, [ctl, micActive]);

  const typing = useCallback(() => {
    ctl.pulse(0.12);
    if (QUIET.includes(ctl.current())) {
      ctl.clearTimers();
      ctl.setState("listening");
    }
  }, [ctl]);

  // PART 12d: a real /ws/voice session (browser fallback) - explicit
  // activation only, never a hidden always-listening stream. Every line
  // shown comes from the server's own voice.response, same as send().
  const voice = useVoiceSession(ctl, show, settle, () => setMicActive(false));

  const toggleMic = useCallback(() => {
    setMicActive((was) => {
      const next = !was;
      if (next) {
        ctl.clearTimers();
        void voice.start();
      } else {
        voice.stop();
      }
      return next;
    });
  }, [ctl, voice]);

  const overrideState = useCallback(
    (s: EntityState) => {
      ctl.clearTimers();
      ctl.setState(s);
    },
    [ctl],
  );

  return { active, previous, micActive, send, wake, focus, blur, typing, toggleMic, overrideState, onRevealComplete };
}
