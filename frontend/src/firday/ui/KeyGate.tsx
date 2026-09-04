import { FormEvent, useState } from "react";

interface Props {
  open: boolean;
  onSubmit: (key: string) => void;
}

// Shown only when the backend actually rejects a request with 401 - i.e.
// FIRDAY_API_KEYS is configured. Conditionally rendered (unmounted when
// closed), so there is no competing CSS visibility rule to fight with.
export const KeyGate = ({ open, onSubmit }: Props) => {
  const [value, setValue] = useState("");
  if (!open) return null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(value.trim());
  };

  return (
    <div className="key-gate" data-testid="api-key-gate">
      <form className="key-gate-box" onSubmit={submit}>
        <div className="key-gate-title">api key</div>
        <input
          type="password"
          placeholder="x-api-key"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          data-testid="api-key-input"
        />
        <button type="submit" data-testid="api-key-submit">
          connect
        </button>
      </form>
    </div>
  );
};
