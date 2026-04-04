/**
 * Debug-only toggles for local development. All flags are read from `VITE_DEBUG_*`
 * env vars (see `desktop/.env.example`). Import `debug` from here — do not scatter
 * `import.meta.env` checks for these behaviors.
 *
 * Vite inlines these at compile time: restart `vite` / `tauri dev` after changing `.env*`.
 */

function parseBool(raw: string | undefined, defaultValue: boolean): boolean {
  if (raw === undefined || raw.trim() === "") return defaultValue;
  const v = raw.trim().toLowerCase();
  if (v === "0" || v === "false" || v === "no" || v === "off") return false;
  return v === "1" || v === "true" || v === "yes" || v === "on";
}

export const debug = {
  /** Show recording UI / waveform without real `recording_*` events (layout). */
  fakeRecording: parseBool(import.meta.env.VITE_DEBUG_FAKE_RECORDING, false),

  /** Skip auto-hiding the borderless overlay when idle (layout / window chrome). */
  overlayNoAutoHide: parseBool(import.meta.env.VITE_DEBUG_OVERLAY_NO_AUTO_HIDE, false),
} as const;
