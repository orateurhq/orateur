"""
Setup-time installation of pywhispercpp with GPU support.

Detects CUDA (nvcc/nvidia-smi) and installs either:
- PyPI (CPU) for non-CUDA or non-Linux x86_64
- Build from source (absadiki/pywhispercpp) for CUDA on Linux x86_64

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

from .paths import VENV_DIR, PYWHISPERCPP_SRC_DIR

log = logging.getLogger(__name__)


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


def _build_pywhispercpp_cuda_from_source() -> bool:
    """Build pywhispercpp from source with CUDA via editable install.

    Clones to a fixed dir and uses pip install -e so .so files link to system
    CUDA. Avoids bundled lib conflicts that cause init errors.
    """
    compute_cap = _detect_compute_capability()
    if not compute_cap:
        log.error("Could not detect GPU compute capability (nvidia-smi --query-gpu=compute_cap)")
        return False

    log.info("Building pywhispercpp from source with CUDA (arch=sm_%s)...", compute_cap)
    log.info("Editable install links to system CUDA (avoids bundled lib conflicts).")
    log.info("This may take several minutes.")

    pip_bin = _get_pip_bin()
    if not pip_bin:
        return False

    env = dict(os.environ)
    env["GGML_CUDA"] = "1"
    env["CMAKE_CUDA_ARCHITECTURES"] = compute_cap
    venv_python = VENV_DIR / "bin" / "python"
    if venv_python.exists():
        env["CMAKE_ARGS"] = f"-DPython3_EXECUTABLE={venv_python}"
        env["PYTHON_EXECUTABLE"] = str(venv_python)
        venv_bin = str(VENV_DIR / "bin")
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

    subprocess.run([str(pip_bin), "uninstall", "-y", "pywhispercpp"], capture_output=True)

    # Clone or update pywhispercpp sources
    if not PYWHISPERCPP_SRC_DIR.exists() or not (PYWHISPERCPP_SRC_DIR / ".git").exists():
        log.info("Cloning pywhispercpp → %s", PYWHISPERCPP_SRC_DIR)
        PYWHISPERCPP_SRC_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--recurse-submodules",
             "https://github.com/absadiki/pywhispercpp.git", str(PYWHISPERCPP_SRC_DIR)],
            check=True,
            timeout=120,
        )
    else:
        log.info("Updating pywhispercpp in %s", PYWHISPERCPP_SRC_DIR)
        subprocess.run(["git", "-C", str(PYWHISPERCPP_SRC_DIR), "fetch", "--tags"], capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(PYWHISPERCPP_SRC_DIR), "submodule", "update", "--init", "--recursive"], check=True, timeout=60)

    try:
        result = subprocess.run(
            [str(pip_bin), "install", "-e", str(PYWHISPERCPP_SRC_DIR),
             "--no-cache-dir", "--force-reinstall"],
            env=env,
            timeout=600,
        )
        if result.returncode == 0:
            log.info("pywhispercpp (CUDA) built and installed successfully")
            return True
        return False
    except subprocess.TimeoutExpired:
        log.error("Build timed out")
        return False
    except Exception as e:
        log.error("Build failed: %s", e)
        return False


def install_pywhispercpp(backend: Optional[str] = None) -> bool:
    """Detect GPU/CUDA and install pywhispercpp.

    backend: Optional override. 'nvidia' = detect CUDA and build from source;
             'cpu' = use PyPI CPU wheel; None = auto-detect.
    """
    if backend == "cpu":
        return _install_from_pypi()

    if backend == "nvidia":
        cuda_version = _detect_cuda_version()
        if not cuda_version:
            log.error("NVIDIA backend requested but no CUDA detected")
            return False
        if not _is_linux_x86_64():
            log.warning("CUDA build from source only supported on Linux x86_64. Using PyPI (CPU).")
            return _install_from_pypi()
        log.info("Detected CUDA %s -> building from source...", cuda_version)
        return _build_pywhispercpp_cuda_from_source()

    # Auto-detect
    cuda_version = _detect_cuda_version()
    if not cuda_version:
        return _install_from_pypi()
    if not _is_linux_x86_64():
        log.warning("CUDA build only on Linux x86_64. Using PyPI (CPU).")
        return _install_from_pypi()
    log.info("Detected CUDA %s -> building from source...", cuda_version)
    return _build_pywhispercpp_cuda_from_source()


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
    """Return pip binary for the target venv.

    - If already in a venv (dev or fixed): use that venv's pip.
    - If running with system Python: create fixed venv, install orateur deps, return its pip.
    """
    # Already in a venv
    if sys.prefix != sys.base_prefix:
        pip_bin = Path(sys.prefix) / "bin" / "pip"
        if pip_bin.exists():
            return pip_bin

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
                    [str(pip_bin), "install", "-e", str(project_root)],
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


def _run_venv_pip(args: list[str]) -> bool:
    """Run pip install in the target venv (fixed venv or current venv)."""
    pip_bin = _get_pip_bin()
    if not pip_bin:
        log.error("No pip found. Run from project with uv: uv run orateur setup")
        return False
    cmd = [str(pip_bin), "install", "--force-reinstall"] + args
    try:
        result = subprocess.run(cmd, timeout=120)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("Install timed out")
        return False
    except Exception as e:
        log.error("Install failed: %s", e)
        return False


def _install_from_pypi() -> bool:
    """Install pywhispercpp from PyPI (CPU)."""
    if _run_venv_pip(["pywhispercpp>=1.4.0"]):
        log.info("pywhispercpp (CPU) installed from PyPI")
        return True
    return False
