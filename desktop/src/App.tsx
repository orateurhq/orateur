import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Waveform } from "./components/Waveform";
import { debug } from "./debug";
import {
  initialOrateurState,
  overlayVisualState,
  reduceOrateurEvent,
  selectDisplayLevels,
  showRecording,
  showTtsChrome,
  type OrateurVisualState,
  type UiEventPayload,
} from "./orateurState";
import "./App.css";

/** Rounded clip + portal root for frameless transparent overlay (see App.css). */
function OverlayAppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="overlay-shell">
      {children}
      <div id="modal-portal-root" className="overlay-shell__portal" aria-hidden />
    </div>
  );
}

function formatClockSeconds(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

/** Frameless window: native edges + full-window drag conflict; use explicit resize strips. */
const OVERLAY_RESIZE_DIRS = [
  "NorthWest",
  "North",
  "NorthEast",
  "West",
  "East",
  "SouthWest",
  "South",
  "SouthEast",
] as const;

type OverlayResizeDir = (typeof OVERLAY_RESIZE_DIRS)[number];

function OverlayResizeEdges() {
  const onMouseDown = useCallback((e: React.MouseEvent, dir: OverlayResizeDir) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isTauri()) return;
    void getCurrentWindow().startResizeDragging(dir);
  }, []);

  return (
    <>
      {OVERLAY_RESIZE_DIRS.map((dir) => (
        <div
          key={dir}
          className={`overlay__resize overlay__resize--${dir}`}
          onMouseDown={(e) => onMouseDown(e, dir)}
          aria-hidden
        />
      ))}
    </>
  );
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

  const visualState = useMemo(() => overlayVisualState(state), [state]);

  useEffect(() => {
    if (!visualState.recording) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, [visualState.recording]);

  useEffect(() => {
    if (state.ttsPhase !== "play" || state.ttsPlayStartedAt <= 0) return;
    const id = window.setInterval(() => setTtsTick((x) => x + 1), 200);
    return () => window.clearInterval(id);
  }, [state.ttsPhase, state.ttsPlayStartedAt]);

  const displayLevels = useMemo(() => selectDisplayLevels(visualState), [visualState]);

  const recordingElapsed =
    visualState.recording && visualState.recordingStartTime > 0
      ? Math.floor(Date.now() / 1000 - visualState.recordingStartTime)
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
    visualState.uiState !== "idle" || state.showAfterDone || visualState.recording;

  const hideTimerRef = useRef<number | null>(null);

  // #region agent log
  useEffect(() => {
    fetch("http://127.0.0.1:7320/ingest/a346a257-a263-4c19-ba30-f18a99f44a82", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "15b347" },
      body: JSON.stringify({
        sessionId: "15b347",
        hypothesisId: "C",
        location: "App.tsx:OverlayPanel:activeSnapshot",
        message: "isActive inputs",
        data: {
          uiState: state.uiState,
          showAfterDone: state.showAfterDone,
          stateRecording: state.recording,
          visualRecording: visualState.recording,
          fakeRecording: debug.fakeRecording,
          overlayNoAutoHide: debug.overlayNoAutoHide,
          rawFake: import.meta.env.VITE_DEBUG_FAKE_RECORDING,
          rawNoAutoHide: import.meta.env.VITE_DEBUG_OVERLAY_NO_AUTO_HIDE,
          isActive,
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  }, [
    state.uiState,
    state.showAfterDone,
    state.recording,
    visualState.recording,
    isActive,
  ]);
  // #endregion

  useEffect(() => {
    if (debug.overlayNoAutoHide) {
      // #region agent log
      fetch("http://127.0.0.1:7320/ingest/a346a257-a263-4c19-ba30-f18a99f44a82", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "15b347" },
        body: JSON.stringify({
          sessionId: "15b347",
          hypothesisId: "B",
          location: "App.tsx:OverlayPanel:hideEffect",
          message: "branch overlayNoAutoHide skip",
          data: { overlayNoAutoHide: true },
          timestamp: Date.now(),
        }),
      }).catch(() => {});
      // #endregion
      return;
    }
    if (isActive) {
      if (hideTimerRef.current !== null) {
        window.clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
      // #region agent log
      fetch("http://127.0.0.1:7320/ingest/a346a257-a263-4c19-ba30-f18a99f44a82", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "15b347" },
        body: JSON.stringify({
          sessionId: "15b347",
          runId: "post-fix",
          hypothesisId: "A",
          location: "App.tsx:OverlayPanel:hideEffect",
          message: "branch isActive — cancel hide timer",
          data: { isActive: true },
          timestamp: Date.now(),
        }),
      }).catch(() => {});
      // #endregion
      return;
    }
    // #region agent log
    fetch("http://127.0.0.1:7320/ingest/a346a257-a263-4c19-ba30-f18a99f44a82", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "15b347" },
      body: JSON.stringify({
        sessionId: "15b347",
        runId: "post-fix",
        hypothesisId: "A",
        location: "App.tsx:OverlayPanel:hideEffect",
        message: "schedule hide timer (idle)",
        data: { isActive: false },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
    // #endregion
    hideTimerRef.current = window.setTimeout(() => {
      hideTimerRef.current = null;
      void (async () => {
        // #region agent log
        fetch("http://127.0.0.1:7320/ingest/a346a257-a263-4c19-ba30-f18a99f44a82", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "15b347" },
          body: JSON.stringify({
            sessionId: "15b347",
            runId: "post-fix",
            hypothesisId: "D",
            location: "App.tsx:OverlayPanel:hideTimer",
            message: "invoke hide_overlay",
            data: { isTauri: await isTauri() },
            timestamp: Date.now(),
          }),
        }).catch(() => {});
        // #endregion
        if (await isTauri()) {
          await invoke("hide_overlay").catch(() => {});
        }
      })();
    }, 800);
    return () => {
      if (hideTimerRef.current !== null) {
        window.clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
    };
  }, [isActive, debug.overlayNoAutoHide]);

  return (
    <div className="overlay">
      <div
        className={`overlay__bar ${isActive ? "overlay__bar--active" : ""}`}
        data-tauri-drag-region
      >
        <div className="app__barInner" data-tauri-drag-region>
          <div className="app__slot app__slot--left" data-tauri-drag-region>
            {showRecording(visualState) && <span className="app__pulse" aria-hidden />}
            {showTtsChrome(visualState) && (
              <span
                className={`app__ttsDot ${
                  visualState.ttsPhase === "synthesize" ? "app__ttsDot--syn" : "app__ttsDot--play"
                }`}
                aria-hidden
              />
            )}
          </div>

          <div className="app__waveWrap" data-tauri-drag-region>
            <Waveform levels={displayLevels} />
          </div>

          <div className="app__slot app__slot--right" data-tauri-drag-region>
            {(showRecording(visualState) || showTtsChrome(visualState)) && (
              <span className="app__timer">
                {showRecording(visualState)
                  ? formatClockSeconds(recordingElapsed)
                  : visualState.ttsPhase === "synthesize"
                    ? "--:--"
                    : formatClockSeconds(ttsRemainingSec)}
              </span>
            )}
          </div>
        </div>
      </div>
      <OverlayResizeEdges />
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
        const modeClass =
          label === "settings" ? "app--settings" : "app--overlay";
        // Apply mode on both `html` and `body` so `:root` / layout rules stay consistent.
        document.documentElement.classList.add(modeClass);
        document.body.classList.add(modeClass);
        setMode(label === "settings" ? "settings" : "overlay");
      } else {
        document.documentElement.classList.add("app--browser");
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
        <OverlayAppShell>
          <OverlayPanel />
        </OverlayAppShell>
      </div>
    );
  }

  return (
    <OverlayAppShell>
      <OverlayPanel />
    </OverlayAppShell>
  );
}
