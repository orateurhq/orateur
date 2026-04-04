/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** @see ./debug.ts */
  readonly VITE_DEBUG_FAKE_RECORDING?: string;
  /** @see ./debug.ts */
  readonly VITE_DEBUG_OVERLAY_NO_AUTO_HIDE?: string;
}
