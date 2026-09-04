import { FormEvent, useState } from "react";
import { ArrowUp, Mic } from "lucide-react";

// Same four prompts the previous static dashboard exposed as quick actions -
// each one is sent through the real /request endpoint, not a canned reply.
const CHIPS = [
  { id: "status", label: "system status", text: "what's my system status?" },
  { id: "containers", label: "containers", text: "list running containers" },
  { id: "automations", label: "automations", text: "show pending automations" },
  { id: "email", label: "check email", text: "check for new email" },
];

interface Props {
  micActive: boolean;
  onSend: (text: string) => void;
  onFocus: () => void;
  onBlur: () => void;
  onTyping: () => void;
  onMic: () => void;
}

export const BottomBar = ({ micActive, onSend, onFocus, onBlur, onTyping, onMic }: Props) => {
  const [value, setValue] = useState("");

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!value.trim()) return;
    onSend(value);
    setValue("");
  };

  return (
    <div className="bottombar" data-testid="bottom-control-strip">
      <div className="chips" role="list">
        {CHIPS.map((c) => (
          <button key={c.id} className="chip" role="listitem" data-testid={`suggestion-chip-${c.id}`} onClick={() => onSend(c.text)}>
            {c.label}
          </button>
        ))}
      </div>
      <form className="input-shell" onSubmit={submit}>
        <input
          data-testid="firday-message-input"
          value={value}
          placeholder="Speak to FIRDAY"
          autoComplete="off"
          onFocus={onFocus}
          onBlur={onBlur}
          onChange={(e) => {
            setValue(e.target.value);
            onTyping();
          }}
        />
        <button
          type="button"
          className={`ctrl-btn mic${micActive ? " active" : ""}`}
          onClick={onMic}
          aria-pressed={micActive}
          aria-label="Toggle listening"
          data-testid="microphone-button"
        >
          <Mic size={16} strokeWidth={1.6} />
        </button>
        <button type="submit" className="ctrl-btn send" aria-label="Send" data-testid="send-message-button" disabled={!value.trim()}>
          <ArrowUp size={16} strokeWidth={1.8} />
        </button>
      </form>
    </div>
  );
};
