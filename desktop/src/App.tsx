import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Waveform } from "./components/Waveform";
import {
  initialOrateurState,
  reduceOrateurEvent,
  selectDisplayLevels,
  showRecording,
  showTtsChrome,
  type OrateurVisualState,
  type UiEventPayload,
} from "./orateurState";
import "./App.css";

function formatClockSeconds(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

function SettingsPanel() {
  const [eventsPathLabel, setEventsPathLabel] = useState("");
  const [pathDraft, setPathDraft] = useState("");

  useEffect(() => {
    void invoke<string>("get_resolved_events_path")
      .then(setEventsPathLabel)
      .catch(() => {});
    void (async () => {
      try {
        const def = await invoke<string>("get_default_events_path");
        const cfg = await invoke<string | null>("read_events_path_config");
        setPathDraft(cfg?.trim() || def);
      } catch {
        setPathDraft("");
      }
    })();
  }, []);

  const savePath = useCallback(async () => {
    const trimmed = pathDraft.trim();
    const def = await invoke<string>("get_default_events_path");
    const pathOpt =
      trimmed.length === 0 || trimmed === def ? null : trimmed;
    await invoke("restart_tail_listener", {
      payload: { path: pathOpt },
    });
  }, [pathDraft]);

  return (
    <div className="settings">
      <h1 className="settings__title">Settings</h1>
      <p className="settings__hint">
        Path to <code>ui_events.jsonl</code> (same as Quickshell or{" "}
        <code>orateur run</code> with <code>ui_events_mirror</code>). Default
        matches Python <code>~/.cache/orateur/ui_events.jsonl</code>.
      </p>
      <label className="settings__label">
        Events file
        <input
          className="settings__input"
          value={pathDraft}
          onChange={(e) => setPathDraft(e.target.value)}
          placeholder={eventsPathLabel}
        />
      </label>
      <div className="settings__row">
        <button type="button" className="settings__btn settings__btn--primary" onClick={savePath}>
          Apply path &amp; restart tail
        </button>
      </div>
      <p className="settings__path">
        Active: <code>{eventsPathLabel || "…"}</code>
      </p>
      <p className="settings__hint settings__hint--footer">
        Close this window when done. Reopen from the tray icon → Settings.
      </p>
    </div>
  );
}

function OverlayPanel() {
  const [state, setState] = useState<OrateurVisualState>(initialOrateurState);
  const [tick, setTick] = useState(0);
  const [ttsTick, setTtsTick] = useState(0);

  useEffect(() => {
    let unEvent: (() => void) | undefined;
    void (async () => {
      unEvent = await listen<UiEventPayload>("orateur:event", (e) => {
        setState((prev) => reduceOrateurEvent(prev, e.payload));
      });
    })();
    return () => {
      unEvent?.();
    };
  }, []);

  useEffect(() => {
    if (!state.showAfterDone) return;
    const t = window.setTimeout(() => {
      setState((s) => ({ ...s, showAfterDone: false }));
    }, 2500);
    return () => window.clearTimeout(t);
  }, [state.showAfterDone]);

  useEffect(() => {
    if (!state.recording) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, [state.recording]);

  useEffect(() => {
    if (state.ttsPhase !== "play" || state.ttsPlayStartedAt <= 0) return;
    const id = window.setInterval(() => setTtsTick((x) => x + 1), 200);
    return () => window.clearInterval(id);
  }, [state.ttsPhase, state.ttsPlayStartedAt]);

  const displayLevels = useMemo(() => selectDisplayLevels(state), [state]);

  const recordingElapsed =
    state.recording && state.recordingStartTime > 0
      ? Math.floor(Date.now() / 1000 - state.recordingStartTime)
      : 0;
  void tick;

  const ttsRemainingSec = useMemo(() => {
    if (state.ttsPhase !== "play" || state.ttsPlayStartedAt <= 0) return 0;
    void ttsTick;
    const elapsed = Date.now() / 1000 - state.ttsPlayStartedAt;
    const left = Math.ceil(state.ttsDurationSec - elapsed);
    return left < 0 ? 0 : left;
  }, [state, ttsTick]);

  const isActive =
    state.uiState !== "idle" || state.showAfterDone || state.recording;

  const hideToTray = useCallback(async () => {
    if (await isTauri()) {
      await getCurrentWindow().hide();
    }
  }, []);

  return (
    <div className="overlay">
      <div
        className={`overlay__bar ${isActive ? "overlay__bar--active" : ""}`}
        data-tauri-drag-region
      >
        <div className="overlay__chrome">
          <button
            type="button"
            className="overlay__hide"
            title="Hide to tray"
            onClick={hideToTray}
          >
            ×
          </button>
        </div>
        <div className="app__barInner">
          <div className="app__slot app__slot--left">
            {showRecording(state) && <span className="app__pulse" aria-hidden />}
            {showTtsChrome(state) && (
              <span
                className={`app__ttsDot ${
                  state.ttsPhase === "synthesize" ? "app__ttsDot--syn" : "app__ttsDot--play"
                }`}
                aria-hidden
              />
            )}
          </div>

          <div className="app__waveWrap">
            <Waveform levels={displayLevels} />
          </div>

          <div className="app__slot app__slot--right">
            {(showRecording(state) || showTtsChrome(state)) && (
              <span className="app__timer">
                {showRecording(state)
                  ? formatClockSeconds(recordingElapsed)
                  : state.ttsPhase === "synthesize"
                    ? "--:--"
                    : formatClockSeconds(ttsRemainingSec)}
              </span>
            )}
          </div>
        </div>
        <p className="app__status">{state.statusText}</p>
        {state.transcribedText && state.uiState === "idle" && (
          <p className="app__transcript">{state.transcribedText}</p>
        )}
      </div>
      <p className="overlay__trayHint">Tray icon: Settings · Show status bar</p>
    </div>
  );
}

export default function App() {
  const [mode, setMode] = useState<"loading" | "overlay" | "settings" | "browser">(
    "loading"
  );

  useEffect(() => {
    void (async () => {
      if (await isTauri()) {
        const label = await getCurrentWindow().label;
        document.body.classList.add(
          label === "settings" ? "app--settings" : "app--overlay"
        );
        setMode(label === "settings" ? "settings" : "overlay");
      } else {
        document.body.classList.add("app--browser");
        setMode("browser");
      }
    })();
  }, []);

  if (mode === "loading") {
    return null;
  }

  if (mode === "settings") {
    return <SettingsPanel />;
  }

  if (mode === "browser") {
    return (
      <div className="browserDev">
        <p className="browserDev__note">
          Browser preview: overlay layout below. Run <code>npm run tauri dev</code>{" "}
          for the real borderless window + tray.
        </p>
        <OverlayPanel />
      </div>
    );
  }

  return <OverlayPanel />;
}
