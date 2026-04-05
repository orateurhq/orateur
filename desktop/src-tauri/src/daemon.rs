//! Spawn `orateur setup` (when needed) and `orateur run` so the overlay receives JSONL events.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{AppHandle, Manager};

use crate::env_check::{self, run_cmd_timeout, SETUP_TIMEOUT};

/// Serialized `orateur run` lifecycle: one spawn at a time, coordinated with [`kill_daemon`].
#[derive(Debug)]
pub struct DaemonHolderInner {
    pub child: Mutex<Option<Child>>,
    pub spawn_serial: Mutex<()>,
}

#[derive(Clone)]
pub struct DaemonHolder(pub Arc<DaemonHolderInner>);

fn child_still_running(child: &mut Child) -> bool {
    match child.try_wait() {
        Ok(None) => true,
        Ok(Some(_)) | Err(_) => false,
    }
}

fn orateur_data_dir(home: &Path) -> PathBuf {
    std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".local").join("share"))
        .join("orateur")
}

fn venv_python(home: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        orateur_data_dir(home).join("venv").join("Scripts").join("python.exe")
    }
    #[cfg(not(windows))]
    {
        orateur_data_dir(home).join("venv").join("bin").join("python")
    }
}

fn pywhispercpp_import_ok(home: &Path) -> bool {
    let py = venv_python(home);
    if !py.is_file() {
        return false;
    }
    let mut cmd = Command::new(&py);
    cmd.arg("-c");
    cmd.arg("import pywhispercpp");
    cmd.env(
        "PATH",
        env_check::extended_path_for_orateur_home(home),
    );
    match run_cmd_timeout(
        cmd,
        Duration::from_secs(20),
        "venv import pywhispercpp",
    ) {
        Ok(o) => o.status.success(),
        Err(_) => false,
    }
}

fn append_daemon_log(path: &Path, line: &str) {
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{line}");
    }
}

fn maybe_run_setup(app: &AppHandle, home: &Path, log_path: &Path) {
    if pywhispercpp_import_ok(home) {
        return;
    }
    let Some(mut cmd) = env_check::orateur_cli_command(app) else {
        append_daemon_log(
            log_path,
            "daemon: skip setup — orateur CLI not available",
        );
        return;
    };
    cmd.arg("setup");
    cmd.stdin(Stdio::null());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    append_daemon_log(log_path, "daemon: running `orateur setup` (this may take a while)…");
    match run_cmd_timeout(cmd, SETUP_TIMEOUT, "orateur setup") {
        Ok(o) => {
            let ok = o.status.success();
            let stdout = String::from_utf8_lossy(&o.stdout);
            let stderr = String::from_utf8_lossy(&o.stderr);
            append_daemon_log(
                log_path,
                &format!(
                    "daemon: `orateur setup` finished success={ok}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
                ),
            );
        }
        Err(e) => append_daemon_log(log_path, &format!("daemon: `orateur setup` error: {e}")),
    }
}

fn daemon_log_path(app: &AppHandle) -> Option<PathBuf> {
    let home = app.path().home_dir().ok()?;
    let cache = std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".cache"));
    Some(cache.join("orateur").join("desktop-daemon.log"))
}

pub fn is_auto_start_enabled(app: &AppHandle) -> bool {
    let Ok(dir) = app.path().app_config_dir() else {
        return true;
    };
    let p = dir.join("auto-start-daemon");
    match std::fs::read_to_string(&p) {
        Ok(s) => {
            let t = s.trim().to_lowercase();
            !matches!(t.as_str(), "0" | "false" | "no" | "off")
        }
        Err(_) => true,
    }
}

#[tauri::command]
pub fn get_auto_start_daemon(app: AppHandle) -> bool {
    is_auto_start_enabled(&app)
}

#[tauri::command]
pub fn set_auto_start_daemon(app: AppHandle, enabled: bool) -> Result<(), String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let p = dir.join("auto-start-daemon");
    let v = if enabled { "1\n" } else { "0\n" };
    std::fs::write(&p, v).map_err(|e| e.to_string())
}

pub fn is_check_orateur_cli_on_startup_enabled(app: &AppHandle) -> bool {
    let Ok(dir) = app.path().app_config_dir() else {
        return false;
    };
    let p = dir.join("check-orateur-cli-on-startup");
    match std::fs::read_to_string(&p) {
        Ok(s) => {
            let t = s.trim().to_lowercase();
            matches!(t.as_str(), "1" | "true" | "yes" | "on")
        }
        Err(_) => false,
    }
}

pub fn set_check_orateur_cli_on_startup(app: &AppHandle, enabled: bool) -> Result<(), String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let p = dir.join("check-orateur-cli-on-startup");
    let v = if enabled { "1\n" } else { "0\n" };
    std::fs::write(&p, v).map_err(|e| e.to_string())
}

pub fn spawn_orateur_daemon_if_needed(app: &AppHandle, holder: &Arc<DaemonHolderInner>) {
    if !is_auto_start_enabled(app) {
        return;
    }

    let _spawn_guard = holder
        .spawn_serial
        .lock()
        .expect("daemon spawn_serial mutex poisoned");

    {
        let mut g = holder.child.lock().expect("daemon mutex poisoned");
        if let Some(ref mut ch) = *g {
            if child_still_running(ch) {
                return;
            }
            *g = None;
        }
    }

    let Some(home) = app.path().home_dir().ok() else {
        return;
    };

    let log_path = match daemon_log_path(app) {
        Some(p) => p,
        None => return,
    };
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    maybe_run_setup(app, &home, &log_path);

    let mut run_cmd = match env_check::orateur_cli_command(app) {
        Some(c) => c,
        None => {
            append_daemon_log(
                &log_path,
                "daemon: cannot spawn — `orateur` CLI not found (same resolution as install gate).",
            );
            return;
        }
    };
    run_cmd.arg("run");
    run_cmd.stdin(Stdio::null());
    let log = match OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        Ok(f) => f,
        Err(e) => {
            append_daemon_log(&log_path, &format!("daemon: cannot open log: {e}"));
            return;
        }
    };
    let log2 = match log.try_clone() {
        Ok(f) => f,
        Err(e) => {
            append_daemon_log(&log_path, &format!("daemon: cannot dup log fd: {e}"));
            return;
        }
    };
    run_cmd.stdout(Stdio::from(log));
    run_cmd.stderr(Stdio::from(log2));

    append_daemon_log(&log_path, "daemon: spawning `orateur run`…");
    match run_cmd.spawn() {
        Ok(child) => {
            let mut g = holder.child.lock().expect("daemon mutex poisoned");
            *g = Some(child);
        }
        Err(e) => append_daemon_log(&log_path, &format!("daemon: spawn failed: {e}")),
    }
}

pub fn kill_daemon(holder: &Arc<DaemonHolderInner>) {
    let _guard = holder
        .spawn_serial
        .lock()
        .expect("daemon spawn_serial mutex poisoned");
    let mut g = holder.child.lock().expect("daemon mutex poisoned");
    if let Some(mut c) = g.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
}

/// Call after a successful in-app install so the daemon can start without restarting the desktop app.
#[tauri::command]
pub fn trigger_orateur_daemon(app: AppHandle, holder: tauri::State<DaemonHolder>) -> Result<(), String> {
    let inner = holder.0.clone();
    let app2 = app.clone();
    std::thread::spawn(move || {
        spawn_orateur_daemon_if_needed(&app2, &inner);
    });
    Ok(())
}

/// Kill the spawned `orateur run` child (if any) and spawn again when auto-start is enabled.
#[tauri::command]
pub fn restart_orateur_daemon(app: AppHandle, holder: tauri::State<DaemonHolder>) -> Result<(), String> {
    let inner = holder.0.clone();
    let app2 = app.clone();
    std::thread::spawn(move || {
        kill_daemon(&inner);
        spawn_orateur_daemon_if_needed(&app2, &inner);
    });
    Ok(())
}
