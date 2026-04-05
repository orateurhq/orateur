import { useCallback, useEffect, useId, useState } from "react";

/** Map `KeyboardEvent.code` to a token matching `config.json` / `shortcuts.py` (joined with `+`). */
function codeToMainToken(code: string): string | null {
  if (code.startsWith("Key") && code.length === 4) {
    return code.slice(3).toUpperCase();
  }
  if (code.startsWith("Digit")) {
    return code.slice(5);
  }
  if (code.startsWith("F") && /^F\d{1,2}$/.test(code)) {
    return code;
  }
  const special: Record<string, string> = {
    Space: "SPACE",
    Minus: "MINUS",
    Equal: "EQUAL",
    BracketLeft: "BRACKETLEFT",
    BracketRight: "BRACKETRIGHT",
    Backslash: "BACKSLASH",
    Semicolon: "SEMICOLON",
    Quote: "QUOTE",
    Comma: "COMMA",
    Period: "PERIOD",
    Slash: "SLASH",
    Backquote: "BACKQUOTE",
    Tab: "TAB",
    Enter: "ENTER",
    Backspace: "BACKSPACE",
    Delete: "DELETE",
    Escape: "ESC",
    ArrowUp: "UP",
    ArrowDown: "DOWN",
    ArrowLeft: "LEFT",
    ArrowRight: "RIGHT",
    Home: "HOME",
    End: "END",
    PageUp: "PAGEUP",
    PageDown: "PAGEDOWN",
    Insert: "INSERT",
    CapsLock: "CAPSLOCK",
    ScrollLock: "SCROLLLOCK",
    NumLock: "NUMLOCK",
    ContextMenu: "COMPOSE",
  };
  if (special[code]) {
    return special[code];
  }
  if (code.startsWith("Numpad")) {
    const rest = code.slice(6);
    return `NUMPAD_${rest}`;
  }
  return null;
}

function keyEventToShortcutString(e: KeyboardEvent): string | null {
  if (e.key === "Escape") {
    return null;
  }
  const parts: string[] = [];
  if (e.ctrlKey) {
    parts.push("CTRL");
  }
  if (e.altKey) {
    parts.push("ALT");
  }
  if (e.shiftKey) {
    parts.push("SHIFT");
  }
  if (e.metaKey) {
    parts.push("SUPER");
  }
  const main = codeToMainToken(e.code);
  if (!main) {
    return null;
  }
  parts.push(main);
  return parts.join("+");
}

type ShortcutRecorderProps = {
  id?: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
};

export function ShortcutRecorder({ id, label, value, onChange }: ShortcutRecorderProps) {
  const autoId = useId();
  const inputId = id ?? `shortcut-${autoId}`;
  const [recording, setRecording] = useState(false);

  useEffect(() => {
    if (!recording) {
      return;
    }
    const onKeyDown = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        setRecording(false);
        return;
      }
      const s = keyEventToShortcutString(e);
      if (s) {
        onChange(s);
        setRecording(false);
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [recording, onChange]);

  const startRecording = useCallback(() => {
    setRecording(true);
  }, []);

  const clear = useCallback(() => {
    onChange("");
  }, [onChange]);

  return (
    <div className="settings__shortcut-row">
      <label className="settings__label" htmlFor={inputId}>
        {label}
        <div className="settings__shortcut-inputRow">
          <input
            id={inputId}
            className="settings__input"
            readOnly
            value={value}
            placeholder="—"
            aria-describedby={recording ? `${inputId}-rec` : undefined}
          />
          <button
            type="button"
            className={`settings__btn ${recording ? "settings__btn--recording" : ""}`}
            onClick={() => (recording ? setRecording(false) : startRecording())}
          >
            {recording ? "Cancel" : "Record shortcut"}
          </button>
          <button type="button" className="settings__btn" onClick={clear} disabled={!value}>
            Clear
          </button>
        </div>
        {recording ? (
          <span id={`${inputId}-rec`} className="settings__shortcut-hint" role="status">
            Press a key combination (Esc to cancel)
          </span>
        ) : null}
      </label>
    </div>
  );
}
