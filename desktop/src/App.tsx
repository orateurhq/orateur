import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Waveform } from "./components/Waveform";
import { debug } from "./debug";
import {
  OrateurInstallGate,
  type OrateurCliReleaseInfo,
} from "./OrateurInstallGate";
import {
  initialOrateurState,
  overlayVisualState,
  reduceOrateurEvent,
  selectDisplayLevels,
  showPulse,
  showRecording,
  showTtsChrome,
  type OrateurVisualState,
  type UiEventPayload,
} from "./orateurState";
import "./App.css";
import { SettingsPanel } from "./settings/SettingsPanel";

/** Rounded clip for frameless transparent overlay (see App.css). Modal portal: `#modal-portal-root` in index.html. */
function OverlayAppShell({ children }: { children: React.ReactNode }) {
  return <div className="overlay-shell">{children}</div>;
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

  useEffect(() => {
    if (debug.overlayNoAutoHide) {
      return;
    }
    if (isActive) {
      if (hideTimerRef.current !== null) {
        window.clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
      return;
    }
    hideTimerRef.current = window.setTimeout(() => {
      hideTimerRef.current = null;
      void (async () => {
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
            {showPulse(visualState) && (
              <span
                className={`app__pulse ${visualState.recording ? "" : "app__pulse--stt"}`}
                aria-hidden
              />
            )}
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

  useEffect(() => {
    if (mode !== "overlay") return;
    void (async () => {
      if (!(await isTauri())) return;
      const on = await invoke<boolean>("get_check_orateur_cli_on_startup").catch(() => false);
      if (!on) return;
      const info = await invoke<OrateurCliReleaseInfo>("get_orateur_cli_release_info").catch(
        () => null,
      );
      if (info?.updateAvailable) {
        sessionStorage.setItem(
          "orateur_cli_update_hint",
          JSON.stringify({ latest: info.latestVersion }),
        );
      } else {
        sessionStorage.removeItem("orateur_cli_update_hint");
      }
    })();
  }, [mode]);

  if (mode === "loading") {
    return null;
  }

  if (mode === "settings") {
    return (
      <OrateurInstallGate>
        <SettingsPanel />
      </OrateurInstallGate>
    );
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
    <OrateurInstallGate>
      <OverlayAppShell>
        <OverlayPanel />
      </OverlayAppShell>
    </OrateurInstallGate>
  );
}
