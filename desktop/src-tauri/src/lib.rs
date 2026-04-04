//! Tails `~/.cache/orateur/ui_events.jsonl` (same semantics as Quickshell `tail -n0 -F`)
//! and emits each parsed JSON line to the webview as `orateur:event`.

#[cfg(desktop)]
mod tray;

mod overlay_workspace;

#[cfg(target_os = "macos")]
mod macos_overlay_panel;

use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[cfg(unix)]
use std::os::unix::fs::MetadataExt;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
#[cfg(desktop)]
use tauri::RunEvent;

/// Matches Python `paths.py`: `XDG_CACHE_HOME/orateur`, default `~/.cache/orateur`.
fn orateur_cache_dir(home: &Path) -> PathBuf {
    let base = std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".cache"));
    base.join("orateur")
}

fn default_events_path_for(home: &Path) -> PathBuf {
    orateur_cache_dir(home).join("ui_events.jsonl")
}

fn resolve_home(app: &AppHandle) -> PathBuf {
    match app.path().home_dir() {
        Ok(h) => h,
        Err(_) => std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(".")),
    }
}

fn expand_user_path(path: &Path, home: &Path) -> PathBuf {
    if let Some(s) = path.to_str() {
        if s == "~" {
            return home.to_path_buf();
        }
        if let Some(rest) = s.strip_prefix("~/") {
            return home.join(rest);
        }
    }
    path.to_path_buf()
}

fn resolve_events_path(app: &AppHandle) -> PathBuf {
    let home = resolve_home(app);
    if let Ok(dir) = app.path().app_config_dir() {
        let cfg = dir.join("events-path.txt");
        if let Ok(s) = std::fs::read_to_string(&cfg) {
            let t = s.trim();
            if !t.is_empty() {
                return expand_user_path(Path::new(t), &home);
            }
        }
    }
    default_events_path_for(&home)
}

#[derive(Clone)]
struct PathsState {
    path: PathBuf,
    /// Incremented on every "Apply path & restart tail" so the tail resets even when the path string is unchanged.
    generation: u64,
}

#[derive(Clone, Serialize)]
struct EventsPathInfo {
    path: String,
}

#[tauri::command]
fn get_default_events_path(app: AppHandle) -> String {
    let home = resolve_home(&app);
    default_events_path_for(&home)
        .to_string_lossy()
        .into_owned()
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
    paths: State<'_, Arc<Mutex<PathsState>>>,
    payload: RestartPayload,
) -> Result<(), String> {
    if let Some(ref p) = payload.path {
        write_events_path_config(app.clone(), Some(p.clone()))?;
    } else {
        write_events_path_config(app.clone(), None)?;
    }
    let resolved = resolve_events_path(&app);
    let mut guard = paths.lock().map_err(|e| e.to_string())?;
    guard.path = resolved;
    guard.generation = guard.generation.saturating_add(1);
    let _ = app.emit(
        "orateur:events-path",
        EventsPathInfo {
            path: guard.path.to_string_lossy().into_owned(),
        },
    );
    Ok(())
}

struct TailState {
    read_offset: u64,
    pending: String,
    #[cfg(unix)]
    last_id: Option<(u64, u64)>,
}

impl TailState {
    fn new(skip_history: bool, path: &Path) -> Self {
        let read_offset = if skip_history {
            std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)
        } else {
            0
        };
        let mut s = Self {
            read_offset,
            pending: String::new(),
            #[cfg(unix)]
            last_id: None,
        };
        #[cfg(unix)]
        {
            if let Ok(meta) = std::fs::metadata(path) {
                s.last_id = Some((meta.dev(), meta.ino()));
            }
        }
        s
    }
}

fn ensure_parent(path: &Path) {
    if let Some(p) = path.parent() {
        let _ = std::fs::create_dir_all(p);
    }
}

/// Show the overlay when `orateur run` mirrors activity triggered by global shortcuts.
fn maybe_show_overlay_for_activity(app: &AppHandle, v: &serde_json::Value) {
    let Some(ev) = v.get("event").and_then(|e| e.as_str()) else {
        return;
    };
    if !matches!(
        ev,
        "recording_started" | "tts_estimate" | "tts_playing" | "error"
    ) {
        return;
    }
    let app = app.clone();
    let _ = app.run_on_main_thread({
        let app = app.clone();
        move || {
            if let Some(w) = app.get_webview_window("overlay") {
                overlay_workspace::show_overlay_window(&w);
            }
        }
    });
}

#[tauri::command]
fn hide_overlay(app: AppHandle) -> Result<(), String> {
    let app = app.clone();
    let _ = app.run_on_main_thread({
        let app = app.clone();
        move || {
            #[cfg(target_os = "macos")]
            {
                use tauri_nspanel::ManagerExt;
                if let Ok(panel) = app.get_webview_panel("overlay") {
                    tauri_nspanel::Panel::hide(&*panel);
                    return;
                }
            }
            if let Some(w) = app.get_webview_window("overlay") {
                let _ = w.hide();
            }
        }
    });
    Ok(())
}

fn tail_loop(paths_shared: Arc<Mutex<PathsState>>, app: AppHandle, stop: Arc<AtomicBool>) {
    let mut active_path = PathBuf::new();
    let mut active_generation: u64 = 0;
    let mut state: Option<TailState> = None;

    while !stop.load(Ordering::SeqCst) {
        let snapshot = paths_shared.lock().unwrap().clone();

        if snapshot.path != active_path || snapshot.generation != active_generation {
            active_path = snapshot.path.clone();
            active_generation = snapshot.generation;
            state = Some(TailState::new(true, &snapshot.path));
        }

        let st = match state.as_mut() {
            Some(s) => s,
            None => {
                std::thread::sleep(Duration::from_millis(100));
                continue;
            }
        };

        if !snapshot.path.exists() {
            ensure_parent(&snapshot.path);
            let _ = OpenOptions::new()
                .create(true)
                .write(true)
                .open(&snapshot.path);
            std::thread::sleep(Duration::from_millis(200));
            continue;
        }

        #[cfg(unix)]
        {
            if let Ok(meta) = std::fs::metadata(&snapshot.path) {
                let id = (meta.dev(), meta.ino());
                match st.last_id {
                    Some(prev) if prev != id => {
                        *st = TailState::new(true, &snapshot.path);
                    }
                    None => {
                        st.last_id = Some(id);
                    }
                    _ => {}
                }
            }
        }

        let mut file = match std::fs::File::open(&snapshot.path) {
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

        let new_len = std::fs::metadata(&snapshot.path)
            .map(|m| m.len())
            .unwrap_or(len);
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
                maybe_show_overlay_for_activity(&app, &v);
                let _ = app.emit("orateur:event", v);
            }
        }

        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    #[cfg(target_os = "macos")]
    {
        builder = builder.plugin(tauri_nspanel::init());
    }

    let app = builder
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
            let paths_arc = Arc::new(Mutex::new(PathsState {
                path: initial.clone(),
                generation: 0,
            }));
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

            #[cfg(target_os = "macos")]
            {
                macos_overlay_panel::init_overlay_panel(app.handle())?;
            }

            #[cfg(all(desktop, debug_assertions))]
            {
                let h = app.handle().clone();
                let h2 = h.clone();
                let _ = h.run_on_main_thread(move || {
                    if let Some(w) = h2.get_webview_window("overlay") {
                        overlay_workspace::show_overlay_window(&w);
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_default_events_path,
            get_resolved_events_path,
            read_events_path_config,
            write_events_path_config,
            restart_tail_listener,
            hide_overlay,
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
