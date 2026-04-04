//! Tails `~/.cache/orateur/ui_events.jsonl` (same semantics as Quickshell `tail -n0 -F`)
//! and emits each parsed JSON line to the webview as `orateur:event`.

#[cfg(desktop)]
mod tray;

use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
#[cfg(desktop)]
use tauri::RunEvent;

/// Matches Python `paths.py`: `XDG_CACHE_HOME/orateur`, default `~/.cache/orateur`.
fn orateur_cache_dir() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    let base = std::env::var("XDG_CACHE_HOME").unwrap_or_else(|_| format!("{}/.cache", home));
    PathBuf::from(base).join("orateur")
}

fn default_events_path() -> PathBuf {
    orateur_cache_dir().join("ui_events.jsonl")
}

fn resolve_events_path(app: &AppHandle) -> PathBuf {
    if let Ok(dir) = app.path().app_config_dir() {
        let cfg = dir.join("events-path.txt");
        if let Ok(s) = std::fs::read_to_string(&cfg) {
            let t = s.trim();
            if !t.is_empty() {
                return PathBuf::from(t);
            }
        }
    }
    default_events_path()
}

#[derive(Clone, Serialize)]
struct EventsPathInfo {
    path: String,
}

#[tauri::command]
fn get_default_events_path() -> String {
    default_events_path().to_string_lossy().into_owned()
}

#[tauri::command]
fn get_resolved_events_path(app: AppHandle) -> String {
    resolve_events_path(&app).to_string_lossy().into_owned()
}

#[tauri::command]
fn read_events_path_config(app: AppHandle) -> Result<Option<String>, String> {
    let p = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let cfg = p.join("events-path.txt");
    if !cfg.exists() {
        return Ok(None);
    }
    std::fs::read_to_string(&cfg).map(Some).map_err(|e| e.to_string())
}

#[tauri::command]
fn write_events_path_config(app: AppHandle, path: Option<String>) -> Result<(), String> {
    let p = app.path().app_config_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&p).map_err(|e| e.to_string())?;
    let cfg = p.join("events-path.txt");
    match path {
        None => {
            let _ = std::fs::remove_file(&cfg);
        }
        Some(s) if s.trim().is_empty() => {
            let _ = std::fs::remove_file(&cfg);
        }
        Some(s) => std::fs::write(&cfg, s.trim()).map_err(|e| e.to_string())?,
    }
    Ok(())
}

#[derive(serde::Deserialize)]
pub struct RestartPayload {
    pub path: Option<String>,
}

#[tauri::command]
fn restart_tail_listener(
    app: AppHandle,
    paths: State<'_, Arc<Mutex<PathBuf>>>,
    payload: RestartPayload,
) -> Result<(), String> {
    if let Some(ref p) = payload.path {
        write_events_path_config(app.clone(), Some(p.clone()))?;
    } else {
        write_events_path_config(app.clone(), None)?;
    }
    let path = resolve_events_path(&app);
    *paths.lock().map_err(|e| e.to_string())? = path.clone();
    let _ = app.emit(
        "orateur:events-path",
        EventsPathInfo {
            path: path.to_string_lossy().into_owned(),
        },
    );
    Ok(())
}

struct TailState {
    read_offset: u64,
    pending: String,
}

impl TailState {
    fn new(skip_history: bool, path: &Path) -> Self {
        let read_offset = if skip_history {
            std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)
        } else {
            0
        };
        Self {
            read_offset,
            pending: String::new(),
        }
    }
}

fn ensure_parent(path: &Path) {
    if let Some(p) = path.parent() {
        let _ = std::fs::create_dir_all(p);
    }
}

fn tail_loop(paths_shared: Arc<Mutex<PathBuf>>, app: AppHandle, stop: Arc<AtomicBool>) {
    let mut active_path = PathBuf::new();
    let mut state: Option<TailState> = None;

    while !stop.load(Ordering::SeqCst) {
        let path = paths_shared.lock().unwrap().clone();

        if path != active_path {
            active_path = path.clone();
            state = Some(TailState::new(true, &path));
        }

        let st = match state.as_mut() {
            Some(s) => s,
            None => {
                std::thread::sleep(Duration::from_millis(100));
                continue;
            }
        };

        if !path.exists() {
            ensure_parent(&path);
            let _ = OpenOptions::new()
                .create(true)
                .write(true)
                .open(&path);
            std::thread::sleep(Duration::from_millis(200));
            continue;
        }

        let mut file = match std::fs::File::open(&path) {
            Ok(f) => f,
            Err(_) => {
                std::thread::sleep(Duration::from_millis(200));
                continue;
            }
        };

        let len = file.metadata().ok().map(|m| m.len()).unwrap_or(0);

        if st.read_offset > len {
            st.read_offset = 0;
            st.pending.clear();
        }

        if st.read_offset == len && st.pending.is_empty() {
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }

        if file.seek(SeekFrom::Start(st.read_offset)).is_err() {
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }

        let mut buf = Vec::new();
        if file.read_to_end(&mut buf).is_err() {
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }

        let new_len = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(len);
        st.read_offset = new_len;

        let chunk = String::from_utf8_lossy(&buf);
        st.pending.push_str(&chunk);

        while let Some(nl) = st.pending.find('\n') {
            let line = st.pending[..nl].to_string();
            st.pending.drain(..=nl);
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                let _ = app.emit("orateur:event", v);
            }
        }

        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let label = window.label();
                if label == "overlay" || label == "settings" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            let initial = resolve_events_path(&handle);
            let paths_arc = Arc::new(Mutex::new(initial.clone()));
            let _ = handle.emit(
                "orateur:events-path",
                EventsPathInfo {
                    path: initial.to_string_lossy().into_owned(),
                },
            );

            let stop = Arc::new(AtomicBool::new(false));
            let for_thread = paths_arc.clone();
            let app_handle = handle.clone();
            let stop_bg = stop.clone();
            std::thread::spawn(move || tail_loop(for_thread, app_handle, stop_bg));

            app.manage(paths_arc);

            #[cfg(desktop)]
            tray::create(app.handle())?;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_default_events_path,
            get_resolved_events_path,
            read_events_path_config,
            write_events_path_config,
            restart_tail_listener,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|_app_handle, event| {
        #[cfg(desktop)]
        if let RunEvent::ExitRequested { api, code, .. } = event {
            if code.is_none() {
                api.prevent_exit();
            }
        }
    });
}
