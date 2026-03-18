"""
Set up LD_LIBRARY_PATH for CUDA/ROCm before pywhispercpp loads.

Discovers paths dynamically via ldconfig and nvcc (no hardcoded paths).
Without this, whisper.cpp reports 'no GPU found' even when CUDA is installed.

NOTE: Preload (_preload_cuda_libs) is disabled - it conflicts with
pywhispercpp wheel's bundled CUDA libs. For CUDA 13+ / Blackwell GPUs,
use: orateur setup --build-from-source (editable install).
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path


def _discover_cuda_rocm_paths() -> list[str]:
    """Discover CUDA and ROCm library paths from system (ldconfig, nvcc, ld.so.conf)."""
    paths: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if p and p not in seen and Path(p).is_dir():
            seen.add(p)
            paths.append(p)

    # 1. ldconfig -p: find libcudart / libcublas / libamdhip64
    try:
        result = subprocess.run(
            ["ldconfig", "-p"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.splitlines():
                # Format: "libcudart.so.13 => /opt/cuda/targets/x86_64-linux/lib/libcudart.so.13"
                m = re.search(r"=>\s+(.+)", line)
                if m and any(
                    name in line
                    for name in (
                        "libcudart",
                        "libcublas",
                        "libamdhip64",
                        "librocblas",
                    )
                ):
                    lib_path = m.group(1).strip()
                    parent = str(Path(lib_path).parent)
                    _add(parent)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 2. nvcc (from PATH): derive CUDA root, add lib dirs that exist
    nvcc_path = shutil.which("nvcc")
    if nvcc_path:
        cuda_root = Path(nvcc_path).resolve().parent.parent
        for sub in ("targets/x86_64-linux/lib", "lib", "lib64"):
            d = cuda_root / sub
            if d.is_dir():
                _add(str(d))

    # 3. ld.so.conf.d: parse for cuda/rocm paths
    for conf_dir in [Path("/etc/ld.so.conf.d"), Path("/etc/ld.so.conf")]:
        if not conf_dir.exists():
            continue
        to_read = [conf_dir] if conf_dir.is_file() else sorted(conf_dir.glob("*.conf"))
        for f in to_read:
            try:
                content = f.read_text()
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("include "):
                        continue
                    p = Path(line)
                    if p.is_absolute() and p.is_dir():
                        if "cuda" in line.lower() or "rocm" in line.lower():
                            _add(line)
            except (OSError, UnicodeDecodeError):
                pass

    return paths


def _preload_cuda_libs(paths: list[str]) -> None:
    """Preload CUDA libs from discovered paths so dlopen finds them."""
    import ctypes

    libs_to_find = ["libcudart", "libcublas"]
    preloaded = []
    for lib_dir in paths:
        if "cuda" not in lib_dir.lower():
            continue
        for base in libs_to_find:
            candidates = sorted(Path(lib_dir).glob(f"{base}.so*"))
            for p in candidates:
                if p.is_file():
                    try:
                        ctypes.CDLL(str(p), ctypes.RTLD_GLOBAL)
                        preloaded.append(p.name)
                        break
                    except OSError:
                        continue
    if preloaded:
        logging.getLogger("orateur._cuda_env").info("Preloaded: %s", ", ".join(preloaded))


def _setup() -> None:
    paths = _discover_cuda_rocm_paths()
    if paths:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        combined = ":".join(paths + ([existing] if existing else []))
        os.environ["LD_LIBRARY_PATH"] = combined
        # _preload_cuda_libs(paths)


_setup()
