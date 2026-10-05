/** Mirrors `src/orateur/config.py` defaults for UI fallbacks when keys are missing from `config.json`. */
export const ORATEUR_DEFAULTS: Record<string, unknown> = {
  primary_shortcut: "SUPER+ALT+D",
  secondary_shortcut: "SUPER+ALT+E",
  tts_shortcut: "SUPER+ALT+T",
  sts_shortcut: "SUPER+ALT+S",
  /** Tauri-only: restart `orateur run` (same token style as `primary_shortcut`). */
  restart_daemon_shortcut: "SUPER+ALT+R",
  recording_mode: "toggle",
  grab_keys: false,
  ui_events_mirror: true,
  selected_device_path: null,
  selected_device_name: null,
  audio_device_id: null,
  audio_device_name: null,
  stt_backend: "pywhispercpp",
  stt_model: "base",
  stt_language: null,
  stt_language_secondary: null,
  stt_threads: 4,
  stt_whisper_prompt: "Transcribe with proper capitalization.",
  stt_whisper_prompt_secondary: null,
  stt_whisper_verbose: false,
  tts_backend: "pocket_tts",
  tts_voice: "alba",
  tts_auto_language: true,
  tts_volume: 1.0,
  llm_backend: "ollama",
  llm_model: "llama3.2",
  llm_system_prompt: "You are a helpful assistant. Respond concisely.",
  llm_base_url: "http://localhost:11434",
  mcpServers: {},
  mcp_tools_url: null,
  languagetool_autostart: false,
  languagetool_port: 8081,
  languagetool_jar: null,
  languagetool_max_heap: "256m",
  languagetool_language: "auto",
};

export function mergeOrateurConfig(raw: Record<string, unknown> | null | undefined): Record<string, unknown> {
  return { ...ORATEUR_DEFAULTS, ...(raw ?? {}) };
}
