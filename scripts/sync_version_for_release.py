#!/usr/bin/env python3
"""Bump release version across repo files. Reads current version from pyproject.toml."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("Could not find version in pyproject.toml")
    return m.group(1)


def write_if_changed(path: Path, content: str) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def patch_pyproject(old: str, new: str) -> bool:
    path = ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    out, n = re.subn(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{new}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit("Expected exactly one version line in pyproject.toml")
    return write_if_changed(path, out)


def patch_init(old: str, new: str) -> bool:
    path = ROOT / "src" / "orateur" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    out, n = re.subn(
        r'^__version__\s*=\s*"[^"]+"',
        f'__version__ = "{new}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit("Expected exactly one __version__ in src/orateur/__init__.py")
    return write_if_changed(path, out)


def patch_readme(old: str, new: str) -> bool:
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    # URLs and examples: v0.1.2 / 0.1.2 — avoid touching unrelated semvers (e.g. pocket-tts>=0.1.0).
    out = text
    out = out.replace(f"download/v{old}/", f"download/v{new}/")
    out = out.replace(f"./install.sh {old}", f"./install.sh {new}")
    out = out.replace(f"ORATEUR_VERSION={old}", f"ORATEUR_VERSION={new}")
    out = out.replace(f"git tag v{old}", f"git tag v{new}")
    out = out.replace(f"git push origin v{old}", f"git push origin v{new}")
    return write_if_changed(path, out)


def patch_desktop_readme(old: str, new: str) -> bool:
    path = ROOT / "desktop" / "README.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    out = text.replace(f"orateur=={old}", f"orateur=={new}")
    return write_if_changed(path, out)


def patch_pip_spec(new: str) -> bool:
    path = ROOT / "desktop" / "src-tauri" / "resources" / "orateur-pip-spec.txt"
    content = f"orateur=={new}\n"
    return write_if_changed(path, content)


def patch_env_check_rs(old: str, new: str) -> bool:
    path = ROOT / "desktop" / "src-tauri" / "src" / "env_check.rs"
    text = path.read_text(encoding="utf-8")
    out = text
    out = re.sub(
        r'const DEFAULT_PIP_SPEC: &str = "orateur==[^"]+"',
        f'const DEFAULT_PIP_SPEC: &str = "orateur=={new}"',
        out,
        count=1,
    )
    out = re.sub(
        r'unwrap_or_else\(\|\| "[^"]+"\.to_string\(\)\)',
        f'unwrap_or_else(|| "{new}".to_string())',
        out,
        count=1,
    )
    return write_if_changed(path, out)


def patch_json_version(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    changed = False
    if data.get("version") == old:
        data["version"] = new
        changed = True
    pkgs = data.get("packages", {})
    root_pkg = pkgs.get("")
    if isinstance(root_pkg, dict) and root_pkg.get("version") == old:
        root_pkg["version"] = new
        changed = True
    if not changed:
        return False
    out = json.dumps(data, indent=2) + "\n"
    return write_if_changed(path, out)


def patch_package_json(old: str, new: str) -> bool:
    path = ROOT / "desktop" / "package.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if data.get("version") != old:
        return False
    data["version"] = new
    out = json.dumps(data, indent=2) + "\n"
    return write_if_changed(path, out)


def patch_tauri_conf(old: str, new: str) -> bool:
    path = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if data.get("version") != old:
        return False
    data["version"] = new
    out = json.dumps(data, indent=2) + "\n"
    return write_if_changed(path, out)


def patch_cargo_toml(old: str, new: str) -> bool:
    path = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    out, n = re.subn(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{new}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit("Expected [package] version in desktop/src-tauri/Cargo.toml")
    return write_if_changed(path, out)


def patch_cargo_lock(new: str) -> bool:
    """Sync [[package]] orateur-desktop version (must match Cargo.toml)."""
    path = ROOT / "desktop" / "src-tauri" / "Cargo.lock"
    text = path.read_text(encoding="utf-8")
    out, n = re.subn(
        r'(name = "orateur-desktop"\nversion = )"[^"]+"',
        rf'\1"{new}"',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit("Could not find [[package]] orateur-desktop in Cargo.lock")
    return write_if_changed(path, out)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: sync_version_for_release.py <new_version>", file=sys.stderr)
        sys.exit(2)
    new = sys.argv[1].strip()
    if not re.match(r"^\d+\.\d+\.\d+$", new):
        print("Version must be semver X.Y.Z (e.g. 0.1.3)", file=sys.stderr)
        sys.exit(1)
    old = read_pyproject_version()
    if old == new:
        print(f"No version change (already {new}).")
        return

    changed: list[str] = []
    for label, fn in [
        ("pyproject.toml", lambda: patch_pyproject(old, new)),
        ("src/orateur/__init__.py", lambda: patch_init(old, new)),
        ("README.md", lambda: patch_readme(old, new)),
        ("desktop/README.md", lambda: patch_desktop_readme(old, new)),
        ("orateur-pip-spec.txt", lambda: patch_pip_spec(new)),
        ("env_check.rs", lambda: patch_env_check_rs(old, new)),
        ("desktop/package.json", lambda: patch_package_json(old, new)),
        ("desktop/package-lock.json", lambda: patch_json_version(ROOT / "desktop" / "package-lock.json", old, new)),
        ("tauri.conf.json", lambda: patch_tauri_conf(old, new)),
        ("Cargo.toml", lambda: patch_cargo_toml(old, new)),
        ("Cargo.lock", lambda: patch_cargo_lock(new)),
    ]:
        if fn():
            changed.append(label)
    print(f"Bumped {old} -> {new} ({len(changed)} files updated: {', '.join(changed)})")


if __name__ == "__main__":
    main()
