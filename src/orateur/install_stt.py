"""
Setup-time installation of pywhispercpp with GPU support.

Installs either:
- Build from source (absadiki/pywhispercpp) with CUDA on Linux x86_64 when CUDA is detected
- Build from source with Metal on macOS Apple Silicon (arm64)
- PyPI (CPU) otherwise

When run via the launcher (installed users): installs into ~/.local/share/orateur/venv.
When run via uv run (development): installs into project .venv.
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .paths import PYWHISPERCPP_SRC_DIR, VENV_DIR

log = logging.getLogger(__name__)

# Written under PYWHISPERCPP_SRC_DIR after a successful editable GPU build (cuda or metal).
ORATEUR_BACKEND_MARKER = ".orateur-backend"

# PEP 668: uv-managed envs and some distros refuse `pip install` without this. We only target a venv.
_PIP_BREAK_SYSTEM = "--break-system-packages"


def _detect_cuda_version() -> Optional[str]:
    """Detect installed CUDA version from nvcc or nvidia-smi."""
    # Try nvcc first (more reliable for build compatibility)
    nvcc_path = shutil.which("nvcc")
    if not nvcc_path:
        nvcc_path = "/opt/cuda/bin/nvcc" if Path("/opt/cuda/bin/nvcc").exists() else None

    if nvcc_path:
        try:
            result = subprocess.run(
                [nvcc_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                # Parse "release 12.2, V12.2.140" -> "12.2"
                match = re.search(r"release (\d+)\.(\d+)", result.stdout)
                if match:
                    return f"{match.group(1)}.{match.group(2)}"
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback to nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                # Parse "CUDA Version: 12.2" -> "12.2"
                match = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", result.stdout)
                if match:
                    return f"{match.group(1)}.{match.group(2)}"
        except (subprocess.TimeoutExpired, OSError):
            pass

    return None


def _detect_compute_capability() -> Optional[str]:
    """Detect GPU compute capability (e.g. '12.0' -> '120' for CMAKE_CUDA_ARCHITECTURES)."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            cap = result.stdout.strip().split("\n")[0].strip()
            # "12.0" -> "120", "8.6" -> "86"
            parts = cap.split(".")
            if len(parts) >= 2:
                return f"{int(parts[0])}{int(parts[1])}"
            if len(parts) == 1:
                return f"{int(parts[0])}0"
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def _is_linux_x86_64() -> bool:
    """Check if we're on Linux x86_64 (source build with CUDA is supported there)."""
    return platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64")


def _is_apple_silicon() -> bool:
    """macOS on Apple Silicon (Metal GPU build)."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _write_backend_marker(kind: str) -> None:
    try:
        p = PYWHISPERCPP_SRC_DIR / ORATEUR_BACKEND_MARKER
        p.write_text(kind.strip() + "\n", encoding="utf-8")
    except OSError:
        pass


def _read_editable_backend_kind() -> str:
    """Infer cuda vs metal for an editable install under PYWHISPERCPP_SRC_DIR."""
    marker = PYWHISPERCPP_SRC_DIR / ORATEUR_BACKEND_MARKER
    if marker.exists():
        line = marker.read_text(encoding="utf-8").strip().splitlines()
        if line and line[0] in ("cuda", "metal"):
            return line[0]
    # Legacy installs before the marker file existed
    if platform.system() == "Linux":
        return "cuda"
    if platform.system() == "Darwin":
        return "metal"
    return "cpu"


def _sanitize_pywhispercpp_build_env(env: dict[str, str]) -> dict[str, str]:
    """Strip vars that would be forwarded as -DKEY=... by pywhispercpp's setup.py and override CMake Python."""
    out = dict(env)
    for k in (
        "PYTHON_EXECUTABLE",
        "Python3_EXECUTABLE",
        "Python_EXECUTABLE",
        "PYTHONHOME",
        "PYTHON",
    ):
        out.pop(k, None)
    return out


def _python_for_pip_install() -> Optional[Path]:
    """Interpreter next to the pip used for installs (must match the built extension ABI)."""
    pip = _get_pip_bin()
    if not pip:
        return None
    bindir = pip.parent
    for name in ("python", "python3"):
        p = bindir / name
        if p.exists():
            return p.resolve()
    for p in sorted(bindir.glob("python3.*")):
        if p.is_file() or p.is_symlink():
            return p.resolve()
    return None


def _pywhispercpp_cmake_env(base: dict[str, str]) -> dict[str, str]:
    """CMAKE_ARGS + PATH so CMake/pybind11 use the same Python as pip (avoids cpython-312 vs cpython-313 mismatch)."""
    env = _sanitize_pywhispercpp_build_env(dict(base))
    py = _python_for_pip_install()
    if not py:
        return env
    prefix = ""
    try:
        r = subprocess.run(
            [str(py), "-c", "import sys; print(sys.prefix)"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            prefix = r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    py_s = str(py)
    parts = [
        f"-DPython3_EXECUTABLE={py_s}",
        f"-DPython_EXECUTABLE={py_s}",
        f"-DPYTHON_EXECUTABLE={py_s}",
    ]
    if prefix:
        parts.append(f"-DPython3_ROOT_DIR={prefix}")
    cmake_extra = " ".join(parts)
    existing = env.get("CMAKE_ARGS", "").strip()
    env["CMAKE_ARGS"] = f"{existing} {cmake_extra}".strip() if existing else cmake_extra
    venv_bin = str(py.parent)
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
    return env


def _pywhispercpp_installed() -> tuple[bool, Optional[str]]:
    """Return (installed, backend) where backend is 'cuda', 'metal', or 'cpu'.

    Resolves pywhispercpp in the same venv as pip (fixed venv when present), not only the
    current process — matches bin/orateur run vs uv run.
    """
    py = _python_for_pip_install()
    if not py:
        return (False, None)
    try:
        r = subprocess.run(
            [
                str(py),
                "-c",
                "import importlib.util; s=importlib.util.find_spec('pywhispercpp'); "
                "print(s.origin if s and s.origin else '')",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            return (False, None)
        origin_s = (r.stdout or "").strip()
        if not origin_s:
            return (False, None)
        origin = Path(origin_s).resolve()
        src_dir = PYWHISPERCPP_SRC_DIR.resolve()
        try:
            origin.relative_to(src_dir)
            return (True, _read_editable_backend_kind())
        except ValueError:
            pass
        return (True, "cpu")
    except Exception:
        return (False, None)


def _ensure_pywhispercpp_repo() -> bool:
    """Clone or update pywhispercpp at PYWHISPERCPP_SRC_DIR."""
    try:
        if not PYWHISPERCPP_SRC_DIR.exists() or not (PYWHISPERCPP_SRC_DIR / ".git").exists():
            log.info("Cloning pywhispercpp → %s", PYWHISPERCPP_SRC_DIR)
            PYWHISPERCPP_SRC_DIR.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--recurse-submodules",
                    "https://github.com/absadiki/pywhispercpp.git",
                    str(PYWHISPERCPP_SRC_DIR),
                ],
                check=True,
                timeout=120,
            )
        else:
            log.info("Updating pywhispercpp in %s", PYWHISPERCPP_SRC_DIR)
            subprocess.run(
                ["git", "-C", str(PYWHISPERCPP_SRC_DIR), "fetch", "--tags"],
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["git", "-C", str(PYWHISPERCPP_SRC_DIR), "submodule", "update", "--init", "--recursive"],
                check=True,
                timeout=60,
            )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.error("Failed to prepare pywhispercpp sources: %s", e)
        return False


def _run_pip_install_editable(env: dict[str, str], force: bool) -> bool:
    py = _python_for_pip_install()
    if not py:
        log.error("No pip/Python for pywhispercpp install (run orateur setup from a venv or after venv exists)")
        return False
    env = _sanitize_pywhispercpp_build_env(env)
    subprocess.run([str(py), "-m", "pip", "uninstall", "-y", "pywhispercpp"], capture_output=True, env=env)
    pip_args = [
        str(py),
        "-m",
        "pip",
        "install",
        _PIP_BREAK_SYSTEM,
        "-e",
        str(PYWHISPERCPP_SRC_DIR),
    ]
    if force:
        pip_args.extend(["--no-cache-dir", "--force-reinstall"])
    try:
        result = subprocess.run(pip_args, env=env, timeout=600)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("Build timed out")
        return False
    except Exception as e:
        log.error("Build failed: %s", e)
        return False


def _build_pywhispercpp_cuda_from_source(force: bool = False) -> bool:
    """Build pywhispercpp from source with CUDA via editable install.

    Clones to a fixed dir and uses pip install -e so .so files link to system
    CUDA. Avoids bundled lib conflicts that cause init errors.
    """
    if not force:
        installed, backend = _pywhispercpp_installed()
        if installed and backend == "cuda":
            log.info("pywhispercpp (CUDA) already installed, skipping")
            return True

    compute_cap = _detect_compute_capability()
    if not compute_cap:
        log.error("Could not detect GPU compute capability (nvidia-smi --query-gpu=compute_cap)")
        return False

    log.info("Building pywhispercpp from source with CUDA (arch=sm_%s)...", compute_cap)
    log.info("Editable install links to system CUDA (avoids bundled lib conflicts).")
    log.info("This may take several minutes.")

    env = _pywhispercpp_cmake_env(dict(os.environ))
    env["GGML_CUDA"] = "1"
    env["CMAKE_CUDA_ARCHITECTURES"] = compute_cap
    env.pop("GGML_METAL", None)

    if not _ensure_pywhispercpp_repo():
        return False
    ok = _run_pip_install_editable(env, force)
    if ok:
        _write_backend_marker("cuda")
        log.info("pywhispercpp (CUDA) built and installed successfully")
    return ok


def _build_pywhispercpp_metal_from_source(force: bool = False) -> bool:
    """Build pywhispercpp from source with Metal (Apple Silicon GPU) via editable install."""
    if not _is_apple_silicon():
        log.error("Metal build is only supported on macOS Apple Silicon (arm64)")
        return False

    if not force:
        installed, backend = _pywhispercpp_installed()
        if installed and backend == "metal":
            log.info("pywhispercpp (Metal) already installed, skipping")
            return True

    log.info("Building pywhispercpp from source with Metal (Apple GPU)...")
    log.info("This may take several minutes (requires Xcode Command Line Tools).")

    env = _pywhispercpp_cmake_env(dict(os.environ))
    env["GGML_METAL"] = "1"
    env.pop("GGML_CUDA", None)
    env.pop("CMAKE_CUDA_ARCHITECTURES", None)

    if not _ensure_pywhispercpp_repo():
        return False
    ok = _run_pip_install_editable(env, force)
    if ok:
        _write_backend_marker("metal")
        log.info("pywhispercpp (Metal) built and installed successfully")
    return ok


def install_pywhispercpp(backend: Optional[str] = None, force: bool = False) -> bool:
    """Detect GPU backend and install pywhispercpp.

    backend: 'nvidia' = CUDA from source (Linux x86_64 + CUDA);
             'metal' = Metal from source (macOS arm64);
             'cpu' = PyPI CPU wheel;
             None = auto (CUDA on Linux+CUDA, Metal on Apple Silicon, else PyPI).
    force: If True, reinstall even when already installed.
    """
    if not force:
        installed, current_backend = _pywhispercpp_installed()
        if installed:
            if backend == "cpu":
                want_cuda = want_metal = False
            elif backend == "nvidia":
                want_cuda, want_metal = True, False
            elif backend == "metal":
                want_cuda, want_metal = False, True
            else:
                want_cuda = bool(_detect_cuda_version() and _is_linux_x86_64())
                want_metal = bool(_is_apple_silicon())
            if want_cuda and current_backend == "cuda":
                log.info("pywhispercpp (CUDA) already installed, skipping")
                return True
            if want_metal and current_backend == "metal":
                log.info("pywhispercpp (Metal) already installed, skipping")
                return True
            if not want_cuda and not want_metal and current_backend == "cpu":
                log.info("pywhispercpp (CPU) already installed, skipping")
                return True

    if backend == "cpu":
        return _install_from_pypi(force=force)

    if backend == "metal":
        if not _is_apple_silicon():
            log.error("Metal backend requires macOS on Apple Silicon (arm64)")
            return False
        return _build_pywhispercpp_metal_from_source(force=force)

    if backend == "nvidia":
        cuda_version = _detect_cuda_version()
        if not cuda_version:
            log.error("NVIDIA backend requested but no CUDA detected")
            return False
        if not _is_linux_x86_64():
            log.warning("CUDA build from source only supported on Linux x86_64. Using PyPI (CPU).")
            return _install_from_pypi(force=force)
        log.info("Detected CUDA %s -> building from source...", cuda_version)
        return _build_pywhispercpp_cuda_from_source(force=force)

    # Auto-detect
    if _is_apple_silicon():
        log.info("Apple Silicon detected -> building pywhispercpp with Metal from source...")
        return _build_pywhispercpp_metal_from_source(force=force)

    cuda_version = _detect_cuda_version()
    if not cuda_version:
        return _install_from_pypi(force=force)
    if not _is_linux_x86_64():
        log.warning("CUDA build only on Linux x86_64. Using PyPI (CPU).")
        return _install_from_pypi(force=force)
    log.info("Detected CUDA %s -> building from source...", cuda_version)
    return _build_pywhispercpp_cuda_from_source(force=force)


def _project_root() -> Path:
    """Project root (directory containing pyproject.toml)."""
    root = os.environ.get("ORATEUR_ROOT")
    if root:
        return Path(root)
    p = Path(__file__).resolve().parent
    for _ in range(5):
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path(__file__).resolve().parent.parent.parent


def _get_pip_bin() -> Optional[Path]:
    """Return pip for the venv that should receive pywhispercpp.

    Prefer the *active* interpreter's venv (sys.prefix) when one is active — same interpreter
    as ``uv run orateur`` or ``bin/orateur`` when it execs that venv's Python. Installing into
    ~/.local/share/orateur/venv first while ``run`` uses a project .venv caused import failures.
    If not in a venv, use or create the fixed venv under XDG data.
    """
    if sys.prefix != sys.base_prefix:
        pip_bin = Path(sys.prefix) / "bin" / "pip"
        if pip_bin.exists():
            return pip_bin

    fixed_pip = VENV_DIR / "bin" / "pip"
    if fixed_pip.exists():
        return fixed_pip

    # System Python: ensure fixed venv exists
    venv_python = VENV_DIR / "bin" / "python"
    if not venv_python.exists():
        log.info("Creating venv at %s", VENV_DIR)
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        py = sys.executable
        try:
            subprocess.run([py, "-m", "venv", str(VENV_DIR)], check=True, timeout=60)
        except subprocess.CalledProcessError as e:
            log.error("Failed to create venv: %s", e)
            return None
        except subprocess.TimeoutExpired:
            log.error("Venv creation timed out")
            return None

        # Install orateur and deps into the new venv
        project_root = _project_root()
        pyproject = project_root / "pyproject.toml"
        if pyproject.exists():
            log.info("Installing orateur and dependencies...")
            pip_bin = VENV_DIR / "bin" / "pip"
            try:
                subprocess.run(
                    [str(pip_bin), "install", _PIP_BREAK_SYSTEM, "-e", str(project_root)],
                    check=True,
                    timeout=300,
                )
            except subprocess.CalledProcessError as e:
                log.error("Failed to install orateur: %s", e)
                return None
            except subprocess.TimeoutExpired:
                log.error("Install timed out")
                return None

    pip_bin = VENV_DIR / "bin" / "pip"
    return pip_bin if pip_bin.exists() else None


def _run_venv_pip(args: list[str], force: bool = False) -> bool:
    """Run pip install in the target venv (fixed venv or current venv)."""
    pip_bin = _get_pip_bin()
    if not pip_bin:
        log.error("No pip found. Run from project with uv: uv run orateur setup")
        return False
    cmd = [str(pip_bin), "install", _PIP_BREAK_SYSTEM] + (["--force-reinstall"] if force else []) + args
    try:
        result = subprocess.run(cmd, timeout=120)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("Install timed out")
        return False
    except Exception as e:
        log.error("Install failed: %s", e)
        return False


def _install_from_pypi(force: bool = False) -> bool:
    """Install pywhispercpp from PyPI (CPU)."""
    if _run_venv_pip(["pywhispercpp>=1.4.0"], force=force):
        log.info("pywhispercpp (CPU) installed from PyPI")
        return True
    return False


def download_whisper_model(model_name: Optional[str] = None) -> bool:
    """Download ggml weights into pywhispercpp's MODELS_DIR (same layout as Model()).

    Runs in a subprocess with the same Python as pip/pywhispercpp so (1) the package is
    visible immediately after pip install without restarting the parent process, and (2) we
    use the venv where STT packages were installed (active venv or fixed venv).
    """
    py = _python_for_pip_install()
    if not py:
        log.error("No Python interpreter for model download")
        return False
    name = (model_name or "base").strip()
    code = f"""import sys
name = {name!r}
from pywhispercpp.constants import AVAILABLE_MODELS
from pywhispercpp.utils import download_model
if name not in AVAILABLE_MODELS:
    print("unknown model", file=sys.stderr)
    sys.exit(2)
p = download_model(name)
print(p or "")
sys.exit(0 if p else 1)
"""
    try:
        r = subprocess.run(
            [str(py), "-c", code],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode == 2:
            log.error("Unknown Whisper model %r (see pywhispercpp.constants.AVAILABLE_MODELS)", name)
            return False
        if r.returncode != 0:
            err = (r.stderr or "") + (r.stdout or "")
            if "No module named" in err or "ModuleNotFoundError" in err:
                log.error("pywhispercpp is not installed in the target venv")
            else:
                log.error("download_model failed: %s", err[:800] if err else "unknown")
            return False
        out = (r.stdout or "").strip()
        if out:
            log.info("Whisper weights: %s", out)
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("Failed to download Whisper model %s: %s", name, e)
        return False
