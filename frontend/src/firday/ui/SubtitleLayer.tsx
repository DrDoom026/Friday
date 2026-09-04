import { useEffect, useState } from "react";
import { EntityState } from "../types";
import { Line } from "../useConversation";

interface Props {
  active: Line | null;
  previous: string | null;
  state: EntityState;
  reducedMotion: boolean;
  onRevealComplete: () => void;
}

export const SubtitleLayer = ({ active, previous, state, reducedMotion, onRevealComplete }: Props) => {
  const [shown, setShown] = useState("");
  const [done, setDone] = useState(true);

  useEffect(() => {
    if (!active) return;
    setDone(false);
    const text = active.text;
    const units = reducedMotion ? text.split(" ") : Array.from(text);
    const joiner = reducedMotion ? " " : "";
    const interval = reducedMotion ? 110 : 26;
    let i = 0;
    setShown("");
    const id = window.setInterval(() => {
      i += 1;
      setShown(units.slice(0, i).join(joiner));
      if (i >= units.length) {
        window.clearInterval(id);
        setDone(true);
        onRevealComplete();
      }
    }, interval);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, reducedMotion]);

  const thinking = state === "thinking";
  const listening = state === "listening";

  return (
    <div className="subtitle-layer" aria-live="polite">
      {thinking && (
        <div className="thinking-dots" data-testid="thinking-indicator" aria-label="FIRDAY is thinking">
          <i /><i /><i />
        </div>
      )}
      {!thinking && listening && !active && (
        <span className="listening-hint" data-testid="listening-hint">listening</span>
      )}
      {!thinking && active && (
        <p className={`subtitle-active${done ? "" : " revealing"}`} data-testid="subtitle-active-line">
          {shown}
          {!done && <span className="subtitle-caret" />}
        </p>
      )}
      {!thinking && previous && (
        <p className="subtitle-prev" data-testid="subtitle-previous-line">{previous}</p>
      )}
    </div>
  );
};
