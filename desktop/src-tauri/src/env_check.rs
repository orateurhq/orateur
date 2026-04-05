//! Detect Python / `orateur` availability and run consented install for the desktop app.

use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};

use wait_timeout::ChildExt;

use crate::release_info::{fetch_latest_release, parse_cli_version, semver_cmp};

const CHECK_TIMEOUT: Duration = Duration::from_secs(25);
const INSTALL_TIMEOUT: Duration = Duration::from_secs(600);
/// GPU/source `orateur setup` can take many minutes.
pub(crate) const SETUP_TIMEOUT: Duration = Duration::from_secs(1200);

const UNIX_LATEST_INSTALL: &str = "curl -fsSL https://github.com/orateurhq/orateur/releases/latest/download/install.sh | bash";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrateurEnvCheck {
    pub orateur_installed: bool,
    pub python_ok: bool,
    pub python_version: Option<String>,
    pub python_executable: Option<String>,
    pub orateur_cli_works: bool,
    pub detail: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallPreview {
    pub uses_bundled_wheel: bool,
    /// `bundled` = offline wheel in app resources; `network` = latest release from GitHub.
    pub install_source: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrateurInstallResult {
    pub ok: bool,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrateurInstallLogEvent {
    pub line: String,
    pub is_stderr: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrateurCliReleaseInfo {
    pub current_version: Option<String>,
    pub latest_version: String,
    pub update_available: bool,
}

fn bundled_wheel_path(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().resource_dir().ok()?;
    let p = dir.join("orateur-bundle.whl");
    p.is_file().then_some(p)
}

/// Public: neutral install preview (no shell commands).
#[tauri::command]
pub fn get_orateur_install_preview(app: AppHandle) -> Result<InstallPreview, String> {
    let uses_bundled_wheel = bundled_wheel_path(&app).is_some();
    let install_source = if uses_bundled_wheel {
        "bundled"
    } else {
        "network"
    };
    Ok(InstallPreview {
        uses_bundled_wheel,
        install_source: install_source.to_string(),
    })
}

fn output_string_lossy(o: &std::process::Output) -> (String, String) {
    (
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

pub(crate) fn run_cmd_timeout(
    mut cmd: Command,
    timeout: Duration,
    label: &str,
) -> Result<std::process::Output, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{label}: failed to spawn: {e}"))?;

    let mut stdout_h = child.stdout.take().expect("stdout piped");
    let mut stderr_h = child.stderr.take().expect("stderr piped");

    let t_out = thread::spawn(move || {
        let mut v = Vec::new();
        let _ = stdout_h.read_to_end(&mut v);
        v
    });
    let t_err = thread::spawn(move || {
        let mut v = Vec::new();
        let _ = stderr_h.read_to_end(&mut v);
        v
    });

    let wait_result = child
        .wait_timeout(timeout)
        .map_err(|e| format!("{label}: wait: {e}"))?;

    let status = match wait_result {
        Some(s) => s,
        None => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = t_out.join();
            let _ = t_err.join();
            return Err(format!("{label}: timed out after {}s", timeout.as_secs()));
        }
    };

    let stdout = t_out
        .join()
        .map_err(|_| format!("{label}: stdout reader panicked"))?;
    let stderr = t_err
        .join()
        .map_err(|_| format!("{label}: stderr reader panicked"))?;

    Ok(std::process::Output {
        status,
        stdout,
        stderr,
    })
}

fn run_cmd_streaming(
    app: &AppHandle,
    mut cmd: Command,
    timeout: Duration,
    label: &str,
) -> Result<std::process::Output, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{label}: failed to spawn: {e}"))?;

    let stdout_h = child.stdout.take().expect("stdout piped");
    let stderr_h = child.stderr.take().expect("stderr piped");

    let stdout_acc = Arc::new(Mutex::new(Vec::<u8>::new()));
    let stderr_acc = Arc::new(Mutex::new(Vec::<u8>::new()));

    let stdout_acc_out = stdout_acc.clone();
    let app_out = app.clone();
    let t_out = thread::spawn(move || {
        let mut reader = BufReader::new(stdout_h);
        let mut line_buf = String::new();
        loop {
            line_buf.clear();
            match reader.read_line(&mut line_buf) {
                Ok(0) => break,
                Ok(_) => {
                    let trimmed = line_buf.trim_end_matches(&['\r', '\n'][..]);
                    if let Ok(mut g) = stdout_acc_out.lock() {
                        g.extend_from_slice(trimmed.as_bytes());
                        g.push(b'\n');
                    }
                    if !trimmed.is_empty() {
                        let _ = app_out.emit(
                            "orateur_install_log",
                            OrateurInstallLogEvent {
                                line: trimmed.to_string(),
                                is_stderr: false,
                            },
                        );
                    }
                }
                Err(_) => break,
            }
        }
    });

    let stderr_acc_err = stderr_acc.clone();
    let app_err = app.clone();
    let t_err = thread::spawn(move || {
        let mut reader = BufReader::new(stderr_h);
        let mut line_buf = String::new();
        loop {
            line_buf.clear();
            match reader.read_line(&mut line_buf) {
                Ok(0) => break,
                Ok(_) => {
                    let trimmed = line_buf.trim_end_matches(&['\r', '\n'][..]);
                    if let Ok(mut g) = stderr_acc_err.lock() {
                        g.extend_from_slice(trimmed.as_bytes());
                        g.push(b'\n');
                    }
                    if !trimmed.is_empty() {
                        let _ = app_err.emit(
                            "orateur_install_log",
                            OrateurInstallLogEvent {
                                line: trimmed.to_string(),
                                is_stderr: true,
                            },
                        );
                    }
                }
                Err(_) => break,
            }
        }
    });

    let wait_result = child
        .wait_timeout(timeout)
        .map_err(|e| format!("{label}: wait: {e}"))?;

    let status = match wait_result {
        Some(s) => s,
        None => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = t_out.join();
            let _ = t_err.join();
            return Err(format!("{label}: timed out after {}s", timeout.as_secs()));
        }
    };

    let _ = t_out.join();
    let _ = t_err.join();

    let stdout = stdout_acc.lock().map_err(|_| format!("{label}: stdout lock"))?.clone();
    let stderr = stderr_acc.lock().map_err(|_| format!("{label}: stderr lock"))?.clone();

    Ok(std::process::Output {
        status,
        stdout,
        stderr,
    })
}

fn python_candidates() -> Vec<Vec<String>> {
    #[cfg(windows)]
    {
        vec![
            vec!["py".to_string(), "-3".to_string()],
            vec!["python".to_string()],
            vec!["python3".to_string()],
        ]
    }
    #[cfg(not(windows))]
    {
        vec![vec!["python3".to_string()], vec!["python".to_string()]]
    }
}

/// Returns (path to executable for display, full argv prefix for Command).
fn resolve_python_invocation() -> Option<(String, Vec<String>)> {
    for args in python_candidates() {
        let mut cmd = Command::new(&args[0]);
        for a in args.iter().skip(1) {
            cmd.arg(a);
        }
        cmd.arg("-c");
        cmd.arg("import sys; print('%d.%d.%d' % sys.version_info[:3])");
        let out = run_cmd_timeout(cmd, CHECK_TIMEOUT, "python version").ok()?;
        if !out.status.success() {
            continue;
        }
        let ver = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if version_at_least_310(&ver) {
            let display = if args.len() > 1 {
                format!("{} {}", args[0], args[1..].join(" "))
            } else {
                args[0].clone()
            };
            return Some((display, args));
        }
    }
    None
}

fn version_at_least_310(s: &str) -> bool {
    let parts: Vec<u32> = s.split('.').filter_map(|p| p.parse().ok()).collect();
    let major = parts.first().copied().unwrap_or(0);
    let minor = parts.get(1).copied().unwrap_or(0);
    major > 3 || (major == 3 && minor >= 10)
}

/// Prepends typical user install locations so GUI apps find `~/.local/bin/orateur`.
pub fn extended_path_for_orateur_home(home: &std::path::Path) -> String {
    let sep = if cfg!(windows) { ";" } else { ":" };
    let parts: Vec<std::path::PathBuf> = {
        let base = vec![home.join(".local").join("bin")];
        #[cfg(target_os = "macos")]
        {
            let mut v = base;
            v.push("/opt/homebrew/bin".into());
            v.push("/usr/local/bin".into());
            v
        }
        #[cfg(not(target_os = "macos"))]
        {
            base
        }
    };
    let prefix = parts
        .iter()
        .map(|p| p.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(sep);
    match std::env::var("PATH") {
        Ok(existing) if !existing.is_empty() => format!("{prefix}{sep}{existing}"),
        _ => prefix,
    }
}

fn orateur_cli_version_on_path() -> Option<String> {
    let mut cmd = Command::new("orateur");
    cmd.arg("--version");
    let out = run_cmd_timeout(cmd, CHECK_TIMEOUT, "orateur --version").ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn orateur_cli_version_with_path(home: &std::path::Path) -> Option<String> {
    let mut cmd = Command::new("orateur");
    cmd.arg("--version");
    cmd.env("PATH", extended_path_for_orateur_home(home));
    let out = run_cmd_timeout(cmd, CHECK_TIMEOUT, "orateur --version").ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

pub fn current_orateur_cli_semver(app: &AppHandle) -> Option<String> {
    let line = app
        .path()
        .home_dir()
        .ok()
        .and_then(|h| orateur_cli_version_with_path(&h))
        .or_else(orateur_cli_version_on_path)?;
    parse_cli_version(&line)
}

/// `orateur` on PATH (including `~/.local/bin`), ready for subcommands like `run` / `setup`.
/// Uses the same resolution as [`check_orateur_environment`] so the daemon can spawn whenever the gate passes.
pub fn orateur_cli_command(app: &AppHandle) -> Option<Command> {
    let home = app.path().home_dir().ok()?;
    let path_env = extended_path_for_orateur_home(&home);
    let cli_ok = orateur_cli_version_with_path(&home).is_some()
        || orateur_cli_version_on_path().is_some();
    if !cli_ok {
        return None;
    }
    let mut c = Command::new("orateur");
    c.env("PATH", &path_env);
    Some(c)
}

#[tauri::command]
pub fn check_orateur_environment(app: AppHandle) -> Result<OrateurEnvCheck, String> {
    let mut detail = String::new();
    let cli_ver = app
        .path()
        .home_dir()
        .ok()
        .and_then(|h| orateur_cli_version_with_path(&h))
        .or_else(orateur_cli_version_on_path);
    if let Some(v) = &cli_ver {
        detail.push_str(&format!("orateur on PATH reports: {v}\n"));
        return Ok(OrateurEnvCheck {
            orateur_installed: true,
            python_ok: true,
            python_version: None,
            python_executable: None,
            orateur_cli_works: true,
            detail: detail.trim().to_string(),
        });
    }

    let Some((py_display, py_args)) = resolve_python_invocation() else {
        return Ok(OrateurEnvCheck {
            orateur_installed: false,
            python_ok: false,
            python_version: None,
            python_executable: None,
            orateur_cli_works: false,
            detail: "No Python 3.10+ interpreter found (tried python3, python). Install Python 3.10+ first — the GitHub release installer needs it."
                .to_string(),
        });
    };

    let ver_out = {
        let mut cmd = Command::new(&py_args[0]);
        for a in py_args.iter().skip(1) {
            cmd.arg(a);
        }
        cmd.arg("-c");
        cmd.arg("import sys; print('%d.%d.%d' % sys.version_info[:3])");
        run_cmd_timeout(cmd, CHECK_TIMEOUT, "python version")?
    };
    let py_ver = String::from_utf8_lossy(&ver_out.stdout).trim().to_string();

    let detail_fail = if cfg!(unix) {
        format!(
            "The `orateur` command was not found (PATH includes ~/.local/bin when possible). \
Python {py_ver} ({py_display}) is available — required for the official installer. \
Use the Install button to download the latest release from GitHub: the app runs curl to fetch install.sh, which creates the venv and installs the `orateur` command (same flow as the project README)."
        )
    } else {
        format!(
            "The `orateur` command was not found. Python {py_ver} is OK. \
Use the Install button to download and install the latest Orateur wheel from GitHub (pip)."
        )
    };

    Ok(OrateurEnvCheck {
        orateur_installed: false,
        python_ok: true,
        python_version: Some(py_ver),
        python_executable: Some(py_display),
        orateur_cli_works: false,
        detail: detail_fail,
    })
}

fn install_orateur_inner(app: AppHandle) -> Result<OrateurInstallResult, String> {
    #[cfg(unix)]
    {
        // Offline: only when both bundled wheel and bundled install.sh exist.
        if let (Some(wheel), Ok(res_dir)) = (bundled_wheel_path(&app), app.path().resource_dir()) {
            let install_sh = res_dir.join("install.sh");
            if install_sh.is_file() {
                let mut cmd = Command::new("bash");
                cmd.arg(&install_sh);
                cmd.env("ORATEUR_WHEEL", &wheel);
                let launcher = res_dir.join("orateur-launcher");
                if launcher.is_file() {
                    cmd.env("ORATEUR_LAUNCHER", launcher);
                }
                return match run_cmd_streaming(&app, cmd, INSTALL_TIMEOUT, "install.sh (bundled)") {
                    Ok(o) => {
                        let ok = o.status.success();
                        let (stdout, stderr) = output_string_lossy(&o);
                        Ok(OrateurInstallResult { ok, stdout, stderr })
                    }
                    Err(e) => Ok(OrateurInstallResult {
                        ok: false,
                        stdout: String::new(),
                        stderr: e,
                    }),
                };
            }
        }

        // macOS / Linux: latest release install.sh from GitHub (embedded version in asset).
        let mut cmd = Command::new("bash");
        cmd.arg("-c");
        cmd.arg(UNIX_LATEST_INSTALL);
        return match run_cmd_streaming(&app, cmd, INSTALL_TIMEOUT, "install.sh (latest)") {
            Ok(o) => {
                let ok = o.status.success();
                let (stdout, stderr) = output_string_lossy(&o);
                Ok(OrateurInstallResult { ok, stdout, stderr })
            }
            Err(e) => Ok(OrateurInstallResult {
                ok: false,
                stdout: String::new(),
                stderr: e,
            }),
        };
    }

    #[cfg(not(unix))]
    {
        let Some((_, py_args)) = resolve_python_invocation() else {
            return Ok(OrateurInstallResult {
                ok: false,
                stdout: String::new(),
                stderr: "No Python 3.10+ found. Install Python before installing Orateur.".to_string(),
            });
        };

        let latest = match fetch_latest_release() {
            Ok(l) => l,
            Err(e) => {
                return Ok(OrateurInstallResult {
                    ok: false,
                    stdout: String::new(),
                    stderr: format!("Could not resolve latest release: {e}"),
                });
            }
        };
        let wheel_url = crate::release_info::wheel_url_for_version(&latest.semver);

        let mut cmd = Command::new(&py_args[0]);
        for a in py_args.iter().skip(1) {
            cmd.arg(a);
        }
        cmd.arg("-m");
        cmd.arg("pip");
        cmd.arg("install");
        cmd.arg("--user");
        cmd.arg(&wheel_url);

        match run_cmd_streaming(&app, cmd, INSTALL_TIMEOUT, "pip install") {
            Ok(o) => {
                let ok = o.status.success();
                let (stdout, stderr) = output_string_lossy(&o);
                Ok(OrateurInstallResult { ok, stdout, stderr })
            }
            Err(e) => Ok(OrateurInstallResult {
                ok: false,
                stdout: String::new(),
                stderr: e,
            }),
        }
    }
}

#[tauri::command]
pub fn install_orateur_from_desktop(app: AppHandle) -> Result<OrateurInstallResult, String> {
    install_orateur_inner(app)
}

#[tauri::command]
pub fn upgrade_orateur_cli(app: AppHandle) -> Result<OrateurInstallResult, String> {
    install_orateur_inner(app)
}

#[tauri::command]
pub fn get_orateur_cli_release_info(app: AppHandle) -> Result<OrateurCliReleaseInfo, String> {
    let latest = fetch_latest_release()?;
    let current = current_orateur_cli_semver(&app);
    let update_available = match &current {
        None => true,
        Some(cur) => semver_cmp(cur, &latest.semver) == std::cmp::Ordering::Less,
    };
    Ok(OrateurCliReleaseInfo {
        current_version: current,
        latest_version: latest.semver,
        update_available,
    })
}

