//! Read/write `~/.config/orateur/config.json` (same path as Python `paths.CONFIG_FILE`).

use std::path::{Path, PathBuf};

use serde_json::Value;
use tauri::{AppHandle, Manager};

/// Matches Python `paths.py`: `XDG_CONFIG_HOME/orateur`, default `~/.config/orateur`.
pub fn orateur_config_dir(home: &Path) -> PathBuf {
    std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".config"))
        .join("orateur")
}

fn config_json_path(app: &AppHandle) -> Result<PathBuf, String> {
    let home = app.path().home_dir().map_err(|e| e.to_string())?;
    Ok(orateur_config_dir(&home).join("config.json"))
}

#[tauri::command]
pub fn get_orateur_config_path(app: AppHandle) -> Result<String, String> {
    let path = config_json_path(&app)?;
    Ok(path.to_string_lossy().into_owned())
}

const FORBIDDEN_KEYS: &[&str] = &["quickshell_autostart"];

fn strip_forbidden_patch(patch: &mut Value) {
    let Some(obj) = patch.as_object_mut() else {
        return;
    };
    for k in FORBIDDEN_KEYS {
        obj.remove(*k);
    }
}

/// Returns JSON object from disk, or `{}` if missing. Strips `$schema` from the root object.
#[tauri::command]
pub fn read_orateur_config(app: AppHandle) -> Result<Value, String> {
    let path = config_json_path(&app)?;
    if !path.exists() {
        return Ok(Value::Object(serde_json::Map::new()));
    }
    let s = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut v: Value = serde_json::from_str(&s).map_err(|e| e.to_string())?;
    if let Some(obj) = v.as_object_mut() {
        obj.remove("$schema");
    }
    Ok(v)
}

/// Shallow-merge top-level keys from `patch` into existing config (or empty). Writes pretty JSON.
#[tauri::command]
pub fn write_orateur_config_patch(app: AppHandle, mut patch: Value) -> Result<(), String> {
    strip_forbidden_patch(&mut patch);
    let path = config_json_path(&app)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }

    let mut base = if path.exists() {
        let s = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
        serde_json::from_str::<Value>(&s).unwrap_or_else(|_| Value::Object(serde_json::Map::new()))
    } else {
        Value::Object(serde_json::Map::new())
    };

    if !base.is_object() {
        base = Value::Object(serde_json::Map::new());
    }
    if let Some(base_obj) = base.as_object_mut() {
        if let Some(patch_obj) = patch.as_object() {
            for (k, v) in patch_obj {
                base_obj.insert(k.clone(), v.clone());
            }
        }
    }

    if let Some(obj) = base.as_object_mut() {
        obj.remove("$schema");
    }

    let out = serde_json::to_string_pretty(&base).map_err(|e| e.to_string())?;
    std::fs::write(&path, out).map_err(|e| e.to_string())?;
    Ok(())
}
