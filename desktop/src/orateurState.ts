/**
 * UI state derived from ~/.cache/orateur/ui_events.jsonl — mirrors
 * quickshell/orateur/OrateurWidget.qml `parseEvent`.
 */

import { debug } from "./debug";

export type UiState = "idle" | "record" | "stt" | "tts" | "sts";

export interface OrateurVisualState {
  uiState: UiState;
  recordKind: string;
  ttsPhase: "idle" | "synthesize" | "play";
  recording: boolean;
  recordingStartTime: number;
  waveformLevels: number[];
  ttsLevels: number[];
  ttsDurationSec: number;
  ttsPlayStartedAt: number;
  stsPipelineActive: boolean;
  transcribedText: string;
  statusText: string;
  showAfterDone: boolean;
}

export const initialOrateurState: OrateurVisualState = {
  uiState: "idle",
  recordKind: "",
  ttsPhase: "idle",
  recording: false,
  recordingStartTime: 0,
  waveformLevels: [],
  ttsLevels: [],
  ttsDurationSec: 0,
  ttsPlayStartedAt: 0,
  stsPipelineActive: false,
  transcribedText: "",
  statusText: "Idle",
  showAfterDone: false,
};

export interface UiEventPayload {
  event?: string;
  level?: number;
  levels?: number[];
  text?: string;
  duration_sec?: number;
  message?: string;
  mode?: string;
  success?: boolean;
}

export function reduceOrateurEvent(
  prev: OrateurVisualState,
  raw: UiEventPayload
): OrateurVisualState {
  const ev = raw.event;
  if (!ev) return prev;

  switch (ev) {
    case "recording_started":
      return {
        ...prev,
        stsPipelineActive: false,
        ttsPhase: "idle",
        ttsPlayStartedAt: 0,
        recording: true,
        recordKind: raw.mode || "stt",
        uiState: "record",
        recordingStartTime: Date.now() / 1000,
        waveformLevels: [],
        statusText: "Recording...",
      };
    case "recording": {
      if (raw.level === undefined) return prev;
      const arr = [...prev.waveformLevels, raw.level];
      while (arr.length > 60) arr.shift();
      return { ...prev, waveformLevels: arr };
    }
    case "recording_stopped": {
      const isSts = prev.recordKind === "sts";
      const levels =
        raw.levels && raw.levels.length > 0 ? raw.levels : prev.waveformLevels;
      return {
        ...prev,
        recording: false,
        stsPipelineActive: isSts,
        uiState: isSts ? "sts" : "stt",
        waveformLevels: levels,
        statusText: "Processing...",
      };
    }
    case "transcribing":
      return { ...prev, statusText: "Transcribing..." };
    case "transcribed": {
      const text = raw.text || "";
      if (prev.stsPipelineActive) {
        return {
          ...prev,
          transcribedText: text,
          statusText: "Processing...",
        };
      }
      return {
        ...prev,
        transcribedText: text,
        statusText: text ? "Done" : "Idle",
        uiState: "idle",
        recordKind: "",
        showAfterDone: true,
      };
    }
    case "tts_estimate":
      return {
        ...prev,
        ttsPhase: "synthesize",
        ttsPlayStartedAt: 0,
        ttsDurationSec: raw.duration_sec || 0,
        ttsLevels: [],
        statusText: "Synthesizing...",
        uiState: prev.stsPipelineActive ? prev.uiState : "tts",
      };
    case "tts_playing":
      return {
        ...prev,
        ttsPhase: "play",
        ttsPlayStartedAt: Date.now() / 1000,
        statusText: "Playing...",
        uiState: prev.stsPipelineActive ? "sts" : "tts",
      };
    case "tts_level": {
      const ttsArr = [...prev.ttsLevels, raw.level || 0];
      return { ...prev, ttsLevels: ttsArr };
    }
    case "tts_done":
      return {
        ...prev,
        ttsPhase: "idle",
        ttsPlayStartedAt: 0,
        ttsDurationSec: 0,
        ttsLevels: [],
        statusText: "Idle",
        stsPipelineActive: false,
        recordKind: "",
        uiState: "idle",
        showAfterDone: true,
      };
    case "error":
      return {
        ...prev,
        ttsPhase: "idle",
        ttsPlayStartedAt: 0,
        statusText: `Error: ${raw.message || "unknown"}`,
        recording: false,
        stsPipelineActive: false,
        recordKind: "",
        uiState: "idle",
        showAfterDone: true,
      };
    default:
      return prev;
  }
}

/** Display levels: while recording use waveform; during TTS playback prefer ttsLevels. */
export function selectDisplayLevels(s: OrateurVisualState): number[] {
  if (s.recording) return s.waveformLevels;
  if (s.ttsLevels.length > 0) return s.ttsLevels;
  return s.waveformLevels;
}

export const showRecording = (s: OrateurVisualState) => s.recording;
export const showTtsChrome = (s: OrateurVisualState) => s.ttsPhase !== "idle";

const FAKE_WAVEFORM_LEVELS: number[] = Array.from({ length: 48 }, (_, i) =>
  Math.min(1, 0.12 + 0.55 * (0.5 + 0.5 * Math.sin(i * 0.35)))
);

let fakeRecordingStartSec = 0;

/** Visual state for the overlay bar; fake recording when `debug.fakeRecording` and not really recording. */
export function overlayVisualState(s: OrateurVisualState): OrateurVisualState {
  if (!debug.fakeRecording) return s;
  if (s.recording) {
    fakeRecordingStartSec = 0;
    return s;
  }
  if (fakeRecordingStartSec <= 0) fakeRecordingStartSec = Date.now() / 1000;
  return {
    ...s,
    recording: true,
    uiState: "record",
    recordKind: s.recordKind || "stt",
    recordingStartTime: fakeRecordingStartSec,
    waveformLevels:
      s.waveformLevels.length > 0 ? s.waveformLevels : FAKE_WAVEFORM_LEVELS,
  };
}
