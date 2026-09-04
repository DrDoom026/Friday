import { useEffect, useState } from "react";
import "./firday.css";
import { useEntityEngine } from "./useEntityEngine";
import { useConversation } from "./useConversation";
import { useReducedMotion } from "./hooks/useReducedMotion";
import { usePerformanceTier } from "./hooks/usePerformanceTier";
import { useViewportHeight } from "./hooks/useViewportHeight";
import { FirdayScene } from "./scene/FirdayScene";
import { TopBar } from "./ui/TopBar";
import { BottomBar } from "./ui/BottomBar";
import { SubtitleLayer } from "./ui/SubtitleLayer";
import { ControlDrawer } from "./ui/ControlDrawer";
import { KeyGate } from "./ui/KeyGate";
import * as api from "./api";

export default function AppShell() {
  const { state, controls } = useEntityEngine();
  const convo = useConversation(controls);
  const reducedMotion = useReducedMotion();
  const { tier, downgrade } = usePerformanceTier();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [keyGateOpen, setKeyGateOpen] = useState(false);
  useViewportHeight();

  useEffect(() => {
    api.onUnauthorized(() => setKeyGateOpen(true));
  }, []);

  return (
    <div className="firday-root" data-testid="firday-app" data-tier={tier} data-state={state}>
      <FirdayScene engine={controls.engine} tier={tier} reducedMotion={reducedMotion} onDowngrade={downgrade} onWake={convo.wake} />
      <TopBar state={state} engine={controls.engine} onMenu={() => setDrawerOpen(true)} />
      <SubtitleLayer active={convo.active} previous={convo.previous} state={state} reducedMotion={reducedMotion} onRevealComplete={convo.onRevealComplete} />
      <BottomBar micActive={convo.micActive} onSend={convo.send} onFocus={convo.focus} onBlur={convo.blur} onTyping={convo.typing} onMic={convo.toggleMic} />
      <ControlDrawer open={drawerOpen} state={state} reducedMotion={reducedMotion} onClose={() => setDrawerOpen(false)} onSelectState={convo.overrideState} />
      <KeyGate
        open={keyGateOpen}
        onSubmit={(key) => {
          api.setApiKey(key);
          setKeyGateOpen(false);
        }}
      />
    </div>
  );
}
