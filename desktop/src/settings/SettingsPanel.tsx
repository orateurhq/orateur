import { useCallback, useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  mergeOrateurConfig,
  ORATEUR_DEFAULTS,
} from "./orateurDefaults";
import { ShortcutRecorder } from "./ShortcutRecorder";
import type { OrateurCliReleaseInfo } from "../OrateurInstallGate";

type SettingsTab = "general" | "shortcuts" | "stt" | "tts" | "sts";

function getStr(c: Record<string, unknown>, key: string): string {
  const v = c[key];
  if (v === null || v === undefined) {
    return "";
  }
  return String(v);
}

function getBool(c: Record<string, unknown>, key: string, def: boolean): boolean {
  const v = c[key];
  if (typeof v === "boolean") {
    return v;
  }
  return def;
}

function getNum(c: Record<string, unknown>, key: string, def: number): number {
  const v = c[key];
  if (typeof v === "number" && !Number.isNaN(v)) {
    return v;
  }
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (!Number.isNaN(n)) {
      return n;
    }
  }
  return def;
}

function RestartRunHint({ autoStartDaemon }: { autoStartDaemon: boolean }) {
  return (
    <div className="settings__restart-hint">
      <p className="settings__hint">
        Changes to shortcuts, speech, or LLM settings apply after <code>orateur run</code> restarts and
        reloads <code>config.json</code>.
      </p>
      {autoStartDaemon ? (
        <div className="settings__row">
          <button
            type="button"
            className="settings__btn settings__btn--primary"
            onClick={() => void invoke("restart_orateur_daemon").catch(() => {})}
          >
            Restart speech daemon
          </button>
        </div>
      ) : null}
      <p className="settings__hint settings__hint--systemd">
        If you run Orateur via a systemd user service instead, use{" "}
        <code>systemctl --user restart orateur.service</code> (or <code>orateur systemd restart</code>).
      </p>
    </div>
  );
}

export function SettingsPanel() {
  const [tab, setTab] = useState<SettingsTab>("general");
  const [config, setConfig] = useState<Record<string, unknown>>(() =>
    mergeOrateurConfig(null),
  );
  const [configPath, setConfigPath] = useState("");
  const [mcpJsonDraft, setMcpJsonDraft] = useState("{}");
  const [mcpJsonError, setMcpJsonError] = useState<string | null>(null);

  const [eventsPathLabel, setEventsPathLabel] = useState("");
  const [pathDraft, setPathDraft] = useState("");
  const [autoStartDaemon, setAutoStartDaemon] = useState(true);
  const [checkCliOnStartup, setCheckCliOnStartup] = useState(false);
  const [cliHint, setCliHint] = useState<{ latest: string } | null>(null);
  const [cliInfo, setCliInfo] = useState<OrateurCliReleaseInfo | null>(null);
  const [cliPhase, setCliPhase] = useState<"idle" | "checking" | "updating">("idle");
  const [cliMessage, setCliMessage] = useState<string | null>(null);
  const [cliLog, setCliLog] = useState<string | null>(null);
  const [updatePhase, setUpdatePhase] = useState<
    "idle" | "checking" | "uptodate" | "downloading" | "error"
  >("idle");
  const [updateMessage, setUpdateMessage] = useState<string | null>(null);

  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const reloadConfig = useCallback(async () => {
    try {
      const raw = await invoke<Record<string, unknown>>("read_orateur_config");
      const merged = mergeOrateurConfig(raw);
      setConfig(merged);
      try {
        setMcpJsonDraft(JSON.stringify(merged.mcpServers ?? {}, null, 2));
      } catch {
        setMcpJsonDraft("{}");
      }
      setMcpJsonError(null);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reloadConfig();
    void invoke<string>("get_orateur_config_path")
      .then(setConfigPath)
      .catch(() => {});
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
    void invoke<boolean>("get_auto_start_daemon")
      .then(setAutoStartDaemon)
      .catch(() => {});
    void invoke<boolean>("get_check_orateur_cli_on_startup")
      .then(setCheckCliOnStartup)
      .catch(() => {});
    try {
      const raw = sessionStorage.getItem("orateur_cli_update_hint");
      if (raw) {
        setCliHint(JSON.parse(raw) as { latest: string });
      }
    } catch {
      /* ignore */
    }
    void invoke<OrateurCliReleaseInfo>("get_orateur_cli_release_info")
      .then((info) => {
        setCliInfo(info);
        setCliMessage(null);
      })
      .catch((e) => {
        setCliMessage(e instanceof Error ? e.message : String(e));
      });
  }, [reloadConfig]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<{ line: string; isStderr: boolean }>("orateur_install_log", (e) => {
      const { line, isStderr } = e.payload;
      setCliLog(
        (prev) =>
          (prev ?? "") + (isStderr ? "[stderr] " : "") + line + "\n",
      );
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      unlisten?.();
    };
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

  const saveGeneral = useCallback(async () => {
    setSaveMsg(null);
    setSaveErr(null);
    try {
      await invoke("write_orateur_config_patch", {
        patch: { ui_events_mirror: getBool(config, "ui_events_mirror", true) },
      });
      await savePath();
      setSaveMsg("General settings saved.");
      void reloadConfig();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, [config, savePath, reloadConfig]);

  const saveShortcuts = useCallback(async () => {
    setSaveMsg(null);
    setSaveErr(null);
    try {
      await invoke("write_orateur_config_patch", {
        patch: {
          primary_shortcut: getStr(config, "primary_shortcut") || ORATEUR_DEFAULTS.primary_shortcut,
          secondary_shortcut: getStr(config, "secondary_shortcut") || ORATEUR_DEFAULTS.secondary_shortcut,
          tts_shortcut: getStr(config, "tts_shortcut") || ORATEUR_DEFAULTS.tts_shortcut,
          sts_shortcut: getStr(config, "sts_shortcut") || ORATEUR_DEFAULTS.sts_shortcut,
          recording_mode: getStr(config, "recording_mode") || "toggle",
          grab_keys: getBool(config, "grab_keys", false),
        },
      });
      setSaveMsg("Shortcuts saved. Restart the speech daemon for them to take effect.");
      void reloadConfig();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, [config, reloadConfig]);

  const saveStt = useCallback(async () => {
    setSaveMsg(null);
    setSaveErr(null);
    try {
      const threads = Math.max(
        1,
        Math.floor(getNum(config, "stt_threads", 4)),
      );
      await invoke("write_orateur_config_patch", {
        patch: {
          stt_backend: getStr(config, "stt_backend") || String(ORATEUR_DEFAULTS.stt_backend),
          stt_model: getStr(config, "stt_model") || String(ORATEUR_DEFAULTS.stt_model),
          stt_language: (() => {
            const s = getStr(config, "stt_language").trim();
            return s.length ? s : null;
          })(),
          stt_language_secondary: (() => {
            const s = getStr(config, "stt_language_secondary").trim();
            return s.length ? s : null;
          })(),
          stt_threads: threads,
          stt_whisper_prompt: getStr(config, "stt_whisper_prompt"),
          stt_whisper_prompt_secondary: (() => {
            const s = getStr(config, "stt_whisper_prompt_secondary").trim();
            return s.length ? s : null;
          })(),
          stt_whisper_verbose: getBool(config, "stt_whisper_verbose", false),
          selected_device_path: (() => {
            const s = getStr(config, "selected_device_path").trim();
            return s.length ? s : null;
          })(),
          selected_device_name: (() => {
            const s = getStr(config, "selected_device_name").trim();
            return s.length ? s : null;
          })(),
          audio_device_id: (() => {
            const s = getStr(config, "audio_device_id").trim();
            return s.length ? s : null;
          })(),
          audio_device_name: (() => {
            const s = getStr(config, "audio_device_name").trim();
            return s.length ? s : null;
          })(),
        },
      });
      setSaveMsg("STT settings saved. Restart the speech daemon for them to take effect.");
      void reloadConfig();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, [config, reloadConfig]);

  const saveTts = useCallback(async () => {
    setSaveMsg(null);
    setSaveErr(null);
    try {
      const vol = getNum(config, "tts_volume", 1);
      await invoke("write_orateur_config_patch", {
        patch: {
          tts_backend: getStr(config, "tts_backend") || String(ORATEUR_DEFAULTS.tts_backend),
          tts_voice: getStr(config, "tts_voice") || String(ORATEUR_DEFAULTS.tts_voice),
          tts_volume: vol,
        },
      });
      setSaveMsg("TTS settings saved. Restart the speech daemon for them to take effect.");
      void reloadConfig();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, [config, reloadConfig]);

  const saveSts = useCallback(async () => {
    setSaveMsg(null);
    setSaveErr(null);
    let mcpServers: Record<string, unknown>;
    try {
      const parsed = JSON.parse(mcpJsonDraft) as unknown;
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setMcpJsonError("mcpServers must be a JSON object.");
        return;
      }
      mcpServers = parsed as Record<string, unknown>;
      setMcpJsonError(null);
    } catch {
      setMcpJsonError("Invalid JSON for mcpServers.");
      return;
    }
    try {
      const toolsUrl = getStr(config, "mcp_tools_url").trim();
      await invoke("write_orateur_config_patch", {
        patch: {
          llm_backend: getStr(config, "llm_backend") || String(ORATEUR_DEFAULTS.llm_backend),
          llm_model: getStr(config, "llm_model") || String(ORATEUR_DEFAULTS.llm_model),
          llm_system_prompt: getStr(config, "llm_system_prompt"),
          llm_base_url: getStr(config, "llm_base_url") || String(ORATEUR_DEFAULTS.llm_base_url),
          mcpServers,
          mcp_tools_url: toolsUrl.length ? toolsUrl : null,
        },
      });
      setSaveMsg("STS / LLM settings saved. Restart the speech daemon for them to take effect.");
      void reloadConfig();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, [config, mcpJsonDraft, reloadConfig]);

  const refreshCliRelease = useCallback(async () => {
    setCliPhase("checking");
    setCliMessage(null);
    try {
      const info = await invoke<OrateurCliReleaseInfo>("get_orateur_cli_release_info");
      setCliInfo(info);
    } catch (e) {
      setCliMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setCliPhase("idle");
    }
  }, []);

  const runCliUpgrade = useCallback(async () => {
    setCliPhase("updating");
    setCliLog("");
    setCliMessage(null);
    try {
      const r = await invoke<{ ok: boolean; stdout: string; stderr: string }>(
        "upgrade_orateur_cli",
      );
      if (r.ok) {
        const info = await invoke<OrateurCliReleaseInfo>("get_orateur_cli_release_info");
        setCliInfo(info);
        sessionStorage.removeItem("orateur_cli_update_hint");
        setCliHint(null);
        setCliMessage("Orateur CLI updated.");
      } else {
        setCliMessage([r.stderr, r.stdout].filter(Boolean).join("\n") || "Update failed.");
      }
    } catch (e) {
      setCliMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setCliPhase("idle");
    }
  }, []);

  const checkAndInstallUpdates = useCallback(async () => {
    if (!(await isTauri())) {
      setUpdateMessage("Updates are only available in the desktop app.");
      setUpdatePhase("error");
      return;
    }
    setUpdatePhase("checking");
    setUpdateMessage(null);
    try {
      const { check } = await import("@tauri-apps/plugin-updater");
      const { relaunch } = await import("@tauri-apps/plugin-process");
      const update = await check();
      if (!update) {
        setUpdatePhase("uptodate");
        setUpdateMessage("You are on the latest version.");
        return;
      }
      setUpdatePhase("downloading");
      await update.downloadAndInstall((event) => {
        if (event.event === "Finished") {
          setUpdateMessage("Installed. Restarting…");
        }
      });
      await relaunch();
    } catch (e) {
      setUpdatePhase("error");
      setUpdateMessage(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const setKey = useCallback((key: string, value: unknown) => {
    setConfig((c) => ({ ...c, [key]: value }));
  }, []);

  return (
    <div className="settings">
      {cliHint ? (
        <p className="settings__cli-banner" role="status">
          A newer Orateur CLI is available (latest: <code>{cliHint.latest}</code>). Update below or dismiss
          this message by updating.
        </p>
      ) : null}
      <header className="settings__header">
        <img
          className="settings__logo"
          src="/logo.png"
          alt=""
          width={40}
          height={40}
          decoding="async"
        />
        <h1 className="settings__title">Settings</h1>
      </header>

      <nav className="settings__tabs" aria-label="Settings sections">
        {(
          [
            ["general", "General"],
            ["shortcuts", "Shortcuts"],
            ["stt", "STT"],
            ["tts", "TTS"],
            ["sts", "STS"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`settings__tab ${tab === id ? "settings__tab--active" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {saveMsg ? <p className="settings__save-msg">{saveMsg}</p> : null}
      {saveErr ? <p className="settings__save-msg settings__save-msg--error">{saveErr}</p> : null}

      {tab === "general" ? (
        <>
          <p className="settings__hint">
            Path to <code>ui_events.jsonl</code> (written by <code>orateur run</code> when mirroring is
            enabled). This app tails the same file for overlay updates. Default matches{" "}
            <code>~/.cache/orateur/ui_events.jsonl</code>.
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
          <p className="settings__path">
            Active: <code>{eventsPathLabel || "…"}</code>
          </p>
          {configPath ? (
            <p className="settings__path">
              Orateur config file: <code>{configPath}</code>
            </p>
          ) : null}
          <label className="settings__label settings__label--checkbox">
            <input
              type="checkbox"
              checked={getBool(config, "ui_events_mirror", true)}
              onChange={(e) => setKey("ui_events_mirror", e.target.checked)}
            />
            Mirror UI events to <code>ui_events.jsonl</code> (lets this app and other tools follow
            recording and TTS state)
          </label>
          <div className="settings__row">
            <button type="button" className="settings__btn settings__btn--primary" onClick={() => void saveGeneral()}>
              Save general
            </button>
          </div>
          <p className="settings__hint settings__hint--sub">
            Saves the mirror option and applies the events file path (restarts the file tail).
          </p>
          <label className="settings__label settings__label--checkbox">
            <input
              type="checkbox"
              checked={autoStartDaemon}
              onChange={(e) => {
                const v = e.target.checked;
                setAutoStartDaemon(v);
                void invoke("set_auto_start_daemon", { enabled: v }).catch(() => {});
              }}
            />
            Start <code>orateur run</code> when this app launches (runs <code>orateur setup</code> first if
            the STT stack is missing; applies on next launch)
          </label>

          <div className="settings__section">
            <p className="settings__hint">Orateur CLI (Python package and launcher from GitHub Releases).</p>
            {cliInfo ? (
              <p className="settings__path">
                Installed:{" "}
                <code>{cliInfo.currentVersion ?? "not detected"}</code> — latest:{" "}
                <code>{cliInfo.latestVersion}</code>
              </p>
            ) : null}
            <label className="settings__label settings__label--checkbox">
              <input
                type="checkbox"
                checked={checkCliOnStartup}
                onChange={(e) => {
                  const v = e.target.checked;
                  setCheckCliOnStartup(v);
                  void invoke("set_check_orateur_cli_on_startup", { enabled: v }).catch(() => {});
                }}
              />
              Check for CLI updates when the app starts (compares to GitHub; no auto-install)
            </label>
            <div className="settings__row">
              <button
                type="button"
                className="settings__btn"
                disabled={cliPhase === "checking" || cliPhase === "updating"}
                onClick={() => void refreshCliRelease()}
              >
                {cliPhase === "checking" ? "Checking…" : "Check CLI updates"}
              </button>
              <button
                type="button"
                className="settings__btn settings__btn--primary"
                disabled={
                  cliPhase === "checking" ||
                  cliPhase === "updating" ||
                  !cliInfo?.updateAvailable
                }
                onClick={() => void runCliUpgrade()}
              >
                {cliPhase === "updating"
                  ? "Updating…"
                  : cliInfo?.currentVersion
                    ? "Update CLI"
                    : "Install CLI"}
              </button>
            </div>
            {cliLog ? <pre className="settings__cli-log">{cliLog}</pre> : null}
            {cliMessage ? (
              <p
                className={
                  cliMessage.startsWith("Orateur CLI updated")
                    ? "settings__update-msg"
                    : "settings__update-msg settings__update-msg--error"
                }
              >
                {cliMessage}
              </p>
            ) : null}
          </div>
          <div className="settings__section">
            <p className="settings__hint">Desktop app updates (signed builds from GitHub Releases).</p>
            <div className="settings__row">
              <button
                type="button"
                className="settings__btn"
                disabled={updatePhase === "checking" || updatePhase === "downloading"}
                onClick={() => void checkAndInstallUpdates()}
              >
                {updatePhase === "checking"
                  ? "Checking…"
                  : updatePhase === "downloading"
                    ? "Downloading…"
                    : "Check for updates"}
              </button>
            </div>
            {updateMessage ? (
              <p
                className={
                  updatePhase === "error" ? "settings__update-msg settings__update-msg--error" : "settings__update-msg"
                }
              >
                {updateMessage}
              </p>
            ) : null}
          </div>
        </>
      ) : null}

      {tab === "shortcuts" ? (
        <>
          <p className="settings__hint">
            Global shortcuts are handled by the Orateur daemon (<code>orateur run</code>), not this window.
            Save, then restart the daemon so changes apply.
          </p>
          <ShortcutRecorder
            label="Primary (main STT / toggle)"
            value={getStr(config, "primary_shortcut")}
            onChange={(v) => setKey("primary_shortcut", v)}
          />
          <ShortcutRecorder
            label="Secondary (alternate language / mode)"
            value={getStr(config, "secondary_shortcut")}
            onChange={(v) => setKey("secondary_shortcut", v)}
          />
          <ShortcutRecorder
            label="TTS (read clipboard or selection)"
            value={getStr(config, "tts_shortcut")}
            onChange={(v) => setKey("tts_shortcut", v)}
          />
          <ShortcutRecorder
            label="STS (speech-to-speech)"
            value={getStr(config, "sts_shortcut")}
            onChange={(v) => setKey("sts_shortcut", v)}
          />
          <label className="settings__label">
            Recording mode
            <input
              className="settings__input"
              value={getStr(config, "recording_mode")}
              onChange={(e) => setKey("recording_mode", e.target.value)}
              placeholder="toggle"
            />
          </label>
          <label className="settings__label settings__label--checkbox">
            <input
              type="checkbox"
              checked={getBool(config, "grab_keys", false)}
              onChange={(e) => setKey("grab_keys", e.target.checked)}
            />
            Grab keys (Linux evdev; may require permissions)
          </label>
          <div className="settings__row">
            <button
              type="button"
              className="settings__btn settings__btn--primary"
              onClick={() => void saveShortcuts()}
            >
              Save shortcuts
            </button>
          </div>
          <RestartRunHint autoStartDaemon={autoStartDaemon} />
        </>
      ) : null}

      {tab === "stt" ? (
        <>
          <label className="settings__label">
            STT backend
            <input
              className="settings__input"
              value={getStr(config, "stt_backend")}
              onChange={(e) => setKey("stt_backend", e.target.value)}
            />
          </label>
          <label className="settings__label">
            Model
            <input
              className="settings__input"
              value={getStr(config, "stt_model")}
              onChange={(e) => setKey("stt_model", e.target.value)}
            />
          </label>
          <label className="settings__label">
            Language (optional)
            <input
              className="settings__input"
              value={getStr(config, "stt_language")}
              onChange={(e) => setKey("stt_language", e.target.value || null)}
              placeholder="e.g. en"
            />
          </label>
          <label className="settings__label">
            Secondary language (optional)
            <input
              className="settings__input"
              value={getStr(config, "stt_language_secondary")}
              onChange={(e) => setKey("stt_language_secondary", e.target.value || null)}
            />
          </label>
          <label className="settings__label">
            Threads
            <input
              className="settings__input"
              type="number"
              min={1}
              value={String(getNum(config, "stt_threads", 4))}
              onChange={(e) => setKey("stt_threads", Number(e.target.value))}
            />
          </label>
          <label className="settings__label">
            Whisper prompt
            <textarea
              className="settings__textarea"
              value={getStr(config, "stt_whisper_prompt")}
              onChange={(e) => setKey("stt_whisper_prompt", e.target.value)}
              rows={3}
            />
          </label>
          <label className="settings__label">
            Whisper prompt (secondary, optional)
            <textarea
              className="settings__textarea"
              value={getStr(config, "stt_whisper_prompt_secondary")}
              onChange={(e) => setKey("stt_whisper_prompt_secondary", e.target.value || null)}
              rows={2}
            />
          </label>
          <label className="settings__label settings__label--checkbox">
            <input
              type="checkbox"
              checked={getBool(config, "stt_whisper_verbose", false)}
              onChange={(e) => setKey("stt_whisper_verbose", e.target.checked)}
            />
            Verbose Whisper logging
          </label>
          <p className="settings__hint">Audio input (optional filters for Linux evdev)</p>
          <label className="settings__label">
            Selected device path
            <input
              className="settings__input"
              value={getStr(config, "selected_device_path")}
              onChange={(e) => setKey("selected_device_path", e.target.value || null)}
            />
          </label>
          <label className="settings__label">
            Selected device name (substring match)
            <input
              className="settings__input"
              value={getStr(config, "selected_device_name")}
              onChange={(e) => setKey("selected_device_name", e.target.value || null)}
            />
          </label>
          <label className="settings__label">
            Audio device ID
            <input
              className="settings__input"
              value={getStr(config, "audio_device_id")}
              onChange={(e) => setKey("audio_device_id", e.target.value || null)}
            />
          </label>
          <label className="settings__label">
            Audio device name
            <input
              className="settings__input"
              value={getStr(config, "audio_device_name")}
              onChange={(e) => setKey("audio_device_name", e.target.value || null)}
            />
          </label>
          <div className="settings__row">
            <button type="button" className="settings__btn settings__btn--primary" onClick={() => void saveStt()}>
              Save STT settings
            </button>
          </div>
          <RestartRunHint autoStartDaemon={autoStartDaemon} />
        </>
      ) : null}

      {tab === "tts" ? (
        <>
          <label className="settings__label">
            TTS backend
            <input
              className="settings__input"
              value={getStr(config, "tts_backend")}
              onChange={(e) => setKey("tts_backend", e.target.value)}
            />
          </label>
          <label className="settings__label">
            Voice
            <input
              className="settings__input"
              value={getStr(config, "tts_voice")}
              onChange={(e) => setKey("tts_voice", e.target.value)}
            />
          </label>
          <label className="settings__label">
            Volume (0–1)
            <input
              className="settings__input"
              type="number"
              step="0.05"
              min={0}
              max={1}
              value={String(getNum(config, "tts_volume", 1))}
              onChange={(e) => setKey("tts_volume", Number(e.target.value))}
            />
          </label>
          <div className="settings__row">
            <button type="button" className="settings__btn settings__btn--primary" onClick={() => void saveTts()}>
              Save TTS settings
            </button>
          </div>
          <RestartRunHint autoStartDaemon={autoStartDaemon} />
        </>
      ) : null}

      {tab === "sts" ? (
        <>
          <p className="settings__hint">
            Speech-to-speech uses the configured LLM (e.g. Ollama). MCP tools are optional.
          </p>
          <label className="settings__label">
            LLM backend
            <input
              className="settings__input"
              value={getStr(config, "llm_backend")}
              onChange={(e) => setKey("llm_backend", e.target.value)}
            />
          </label>
          <label className="settings__label">
            Model
            <input
              className="settings__input"
              value={getStr(config, "llm_model")}
              onChange={(e) => setKey("llm_model", e.target.value)}
            />
          </label>
          <label className="settings__label">
            Base URL
            <input
              className="settings__input"
              value={getStr(config, "llm_base_url")}
              onChange={(e) => setKey("llm_base_url", e.target.value)}
            />
          </label>
          <label className="settings__label">
            System prompt
            <textarea
              className="settings__textarea"
              value={getStr(config, "llm_system_prompt")}
              onChange={(e) => setKey("llm_system_prompt", e.target.value)}
              rows={4}
            />
          </label>
          <label className="settings__label">
            MCP servers (JSON object, Cursor-style)
            <textarea
              className="settings__textarea"
              value={mcpJsonDraft}
              onChange={(e) => {
                setMcpJsonDraft(e.target.value);
                setMcpJsonError(null);
              }}
              rows={10}
              spellCheck={false}
            />
          </label>
          {mcpJsonError ? <p className="settings__save-msg settings__save-msg--error">{mcpJsonError}</p> : null}
          <label className="settings__label">
            MCP tools URL (optional SSE)
            <input
              className="settings__input"
              value={getStr(config, "mcp_tools_url")}
              onChange={(e) => setKey("mcp_tools_url", e.target.value || null)}
              placeholder="http://localhost:8050/sse"
            />
          </label>
          <div className="settings__row">
            <button type="button" className="settings__btn settings__btn--primary" onClick={() => void saveSts()}>
              Save STS settings
            </button>
          </div>
          <RestartRunHint autoStartDaemon={autoStartDaemon} />
        </>
      ) : null}

      <p className="settings__hint settings__hint--footer">
        Close this window when done. Reopen from the tray icon → Settings.
      </p>
    </div>
  );
}
