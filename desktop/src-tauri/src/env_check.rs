//! Detect Python / `orateur` availability and run consented `pip install` for the desktop app.

use serde::Serialize;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Manager};

use wait_timeout::ChildExt;

/// Default when `resources/orateur-pip-spec.txt` is missing (e.g. some dev runs).
const DEFAULT_PIP_SPEC: &str = "orateur==0.1.1";

const CHECK_TIMEOUT: Duration = Duration::from_secs(25);
const INSTALL_TIMEOUT: Duration = Duration::from_secs(600);

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
    pub pip_spec: String,
    pub command_display: String,
    pub uses_bundled_wheel: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrateurInstallResult {
    pub ok: bool,
    pub stdout: String,
    pub stderr: String,
}

fn read_pip_spec(app: &AppHandle) -> String {
    if let Ok(dir) = app.path().resource_dir() {
        let p = dir.join("orateur-pip-spec.txt");
        if let Ok(s) = std::fs::read_to_string(&p) {
            let t = s.trim();
            if !t.is_empty() {
                return t.to_string();
            }
        }
    }
    DEFAULT_PIP_SPEC.to_string()
}

fn bundled_wheel_path(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().resource_dir().ok()?;
    let p = dir.join("orateur-bundle.whl");
    p.is_file().then_some(p)
}

fn version_from_wheel_filename(path: &Path) -> Option<String> {
    let name = path.file_name()?.to_str()?;
    let rest = name.strip_prefix("orateur-")?;
    let ver = rest.split('-').next()?;
    Some(ver.to_string())
}

fn orateur_version_from_pip_spec(spec: &str) -> Option<String> {
    let s = spec.trim();
    s.strip_prefix("orateur==")
        .map(|v| v.trim().to_string())
}

fn install_orateur_version(app: &AppHandle) -> String {
    if let Some(p) = bundled_wheel_path(app) {
        if let Some(v) = version_from_wheel_filename(&p) {
            return v;
        }
    }
    if let Some(v) = orateur_version_from_pip_spec(&read_pip_spec(app)) {
        return v;
    }
    orateur_version_from_pip_spec(DEFAULT_PIP_SPEC).unwrap_or_else(|| "0.1.1".to_string())
}

/// Public: what the Install button will run.
#[tauri::command]
pub fn get_orateur_install_preview(app: AppHandle) -> Result<InstallPreview, String> {
    let pip_spec = if let Some(wheel) = bundled_wheel_path(&app) {
        wheel.to_string_lossy().into_owned()
    } else {
        read_pip_spec(&app)
    };
    let uses_bundled_wheel = bundled_wheel_path(&app).is_some();
    let py = pick_python_executable(&app).unwrap_or_else(|| "python3".to_string());
    let command_display = if cfg!(unix) {
        if let Ok(res) = app.path().resource_dir() {
            if res.join("install.sh").is_file() {
                "bash resources/install.sh  →  ~/.local/share/orateur/venv  +  ~/.local/bin/orateur"
                    .to_string()
            } else {
                format!("{py} -m pip install --user {pip_spec}")
            }
        } else {
            format!("{py} -m pip install --user {pip_spec}")
        }
    } else {
        format!("{py} -m pip install --user {pip_spec}")
    };
    Ok(InstallPreview {
        pip_spec,
        command_display,
        uses_bundled_wheel,
    })
}

fn output_string_lossy(o: &std::process::Output) -> (String, String) {
    (
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

fn run_cmd_timeout(
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

fn pick_python_executable(_app: &AppHandle) -> Option<String> {
    resolve_python_invocation().map(|(display, _)| display)
}

fn python_cmd_import_orateur(args: &[String]) -> Result<std::process::Output, String> {
    let mut cmd = Command::new(&args[0]);
    for a in args.iter().skip(1) {
        cmd.arg(a);
    }
    cmd.arg("-c");
    cmd.arg(
        r#"import sys
if sys.version_info < (3, 10):
    sys.exit(2)
try:
    import orateur
    print(getattr(orateur, "__version__", "?"))
except ImportError:
    sys.exit(1)
sys.exit(0)
"#,
    );
    run_cmd_timeout(cmd, CHECK_TIMEOUT, "import orateur")
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

fn orateur_module_version(py_args: &[String]) -> bool {
    let mut cmd = Command::new(&py_args[0]);
    for a in py_args.iter().skip(1) {
        cmd.arg(a);
    }
    cmd.arg("-m");
    cmd.arg("orateur.cli");
    cmd.arg("--version");
    let out = match run_cmd_timeout(cmd, CHECK_TIMEOUT, "python -m orateur.cli --version") {
        Ok(o) => o,
        Err(_) => return false,
    };
    out.status.success()
}

#[tauri::command]
pub fn check_orateur_environment(_app: AppHandle) -> Result<OrateurEnvCheck, String> {
    let mut detail = String::new();
    let cli_ver = orateur_cli_version_on_path();
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
            detail: "No Python 3.10+ interpreter found (tried python3, python). Install Python 3.10 or newer."
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

    match python_cmd_import_orateur(&py_args) {
        Ok(out) if out.status.success() => {
            let v = String::from_utf8_lossy(&out.stdout).trim().to_string();
            detail.push_str(&format!(
                "Python {py_ver} ({py_display}): orateur import OK ({v})\n"
            ));
            let cli_ok = orateur_module_version(&py_args);
            Ok(OrateurEnvCheck {
                orateur_installed: true,
                python_ok: true,
                python_version: Some(py_ver),
                python_executable: Some(py_display),
                orateur_cli_works: cli_ok,
                detail: detail.trim().to_string(),
            })
        }
        Ok(out) if out.status.code() == Some(2) => Ok(OrateurEnvCheck {
            orateur_installed: false,
            python_ok: false,
            python_version: Some(py_ver.clone()),
            python_executable: Some(py_display.clone()),
            orateur_cli_works: false,
            detail: format!("Python {py_ver} is older than 3.10."),
        }),
        Ok(out) => {
            let (_, err) = output_string_lossy(&out);
            detail.push_str(&format!(
                "Python {py_ver} ({py_display}): orateur import failed.\n{err}"
            ));
            Ok(OrateurEnvCheck {
                orateur_installed: false,
                python_ok: true,
                python_version: Some(py_ver),
                python_executable: Some(py_display),
                orateur_cli_works: false,
                detail: detail.trim().to_string(),
            })
        }
        Err(e) => Ok(OrateurEnvCheck {
            orateur_installed: false,
            python_ok: true,
            python_version: Some(py_ver),
            python_executable: Some(py_display),
            orateur_cli_works: false,
            detail: e,
        }),
    }
}

#[tauri::command]
pub fn install_orateur_from_desktop(app: AppHandle) -> Result<OrateurInstallResult, String> {
    let pip_target = if let Some(wheel) = bundled_wheel_path(&app) {
        wheel.to_string_lossy().into_owned()
    } else {
        read_pip_spec(&app)
    };

    let Some((_, py_args)) = resolve_python_invocation() else {
        return Ok(OrateurInstallResult {
            ok: false,
            stdout: String::new(),
            stderr: "No Python 3.10+ found. Install Python before installing Orateur.".to_string(),
        });
    };

    #[cfg(unix)]
    {
        if let Ok(res_dir) = app.path().resource_dir() {
            let install_sh = res_dir.join("install.sh");
            if install_sh.is_file() {
                let mut cmd = Command::new("bash");
                cmd.arg(&install_sh);
                cmd.env("ORATEUR_VERSION", install_orateur_version(&app));
                if let Some(w) = bundled_wheel_path(&app) {
                    cmd.env("ORATEUR_WHEEL", w);
                } else {
                    let spec = read_pip_spec(&app);
                    if spec.starts_with("http://") || spec.starts_with("https://") {
                        cmd.env("ORATEUR_WHEEL", spec);
                    }
                }
                let launcher = res_dir.join("orateur-launcher");
                if launcher.is_file() {
                    cmd.env("ORATEUR_LAUNCHER", launcher);
                }
                return match run_cmd_timeout(cmd, INSTALL_TIMEOUT, "install.sh") {
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
    }

    let mut cmd = Command::new(&py_args[0]);
    for a in py_args.iter().skip(1) {
        cmd.arg(a);
    }
    cmd.arg("-m");
    cmd.arg("pip");
    cmd.arg("install");
    cmd.arg("--user");
    cmd.arg(pip_target);

    match run_cmd_timeout(cmd, INSTALL_TIMEOUT, "pip install") {
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
