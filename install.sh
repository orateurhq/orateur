#!/usr/bin/env bash
# Install Orateur into ~/.local/share/orateur/venv and ~/.local/bin/orateur (or ORATEUR_BIN_DIR).
# Release builds set DEFAULT_ORATEUR_VERSION so this can be run with no arguments.
#
# Environment (optional):
#   ORATEUR_REPO           default: antoineFrau/orateur
#   ORATEUR_VERSION        semver (overrides DEFAULT_ORATEUR_VERSION / $1)
#   ORATEUR_WHEEL          local path or https URL to wheel (skips default GitHub URL)
#   ORATEUR_LAUNCHER       path to launcher script to install (desktop bundle)
#   ORATEUR_SKIP_QUICKSHELL if 1, do not fetch quickshell-orateur.tar.gz
#   ORATEUR_BIN_DIR        default: ~/.local/bin

set -euo pipefail

# Populated by .github/workflows/release.yml for release artifacts (semver only, no "v" prefix):
DEFAULT_ORATEUR_VERSION="0.1.3"

ORATEUR_REPO="${ORATEUR_REPO:-antoineFrau/orateur}"
ORATEUR_BIN_DIR="${ORATEUR_BIN_DIR:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orateur"
VENV_DIR="$DATA_DIR/venv"
LAUNCHER_DEST="$ORATEUR_BIN_DIR/orateur"

die() {
  echo "Error: $*" >&2
  exit 1
}

get_system_python() {
  local hb=""
  [ -d "/opt/homebrew/bin" ] && hb="${hb:+$hb:}/opt/homebrew/bin"
  [ -n "${HOME:-}" ] && [ -d "$HOME/.linuxbrew/bin" ] && hb="${hb:+$hb:}$HOME/.linuxbrew/bin"
  [ -d "/home/linuxbrew/.linuxbrew/bin" ] && hb="${hb:+$hb:}/home/linuxbrew/.linuxbrew/bin"
  local system_path="${hb:+$hb:}/usr/bin:/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/sbin"
  local py
  py="$(PATH="$system_path" command -v python3 2>/dev/null)" && [ -x "$py" ] && { echo "$py"; return 0; }
  py="$(PATH="$system_path" command -v python 2>/dev/null)" && [ -x "$py" ] && { echo "$py"; return 0; }
  return 1
}

require_python_310() {
  local py="$1"
  if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    local ver
    ver="$("$py" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "?")"
    die "Python 3.10+ required (found Python ${ver})."
  fi
}

PY="$(get_system_python || true)"
[[ -n "$PY" ]] || die "No Python 3 found. Install Python 3.10+ (e.g. python.org, Homebrew, or your distro)."
require_python_310 "$PY"

VER_RAW="${ORATEUR_VERSION:-$DEFAULT_ORATEUR_VERSION}"
if [[ -z "$VER_RAW" ]] && [[ -n "${1:-}" ]]; then
  VER_RAW="$1"
fi
VER_RAW="${VER_RAW#v}"
VER_RAW="${VER_RAW%% *}"

WHEEL_REF="${ORATEUR_WHEEL:-}"
if [[ -z "$WHEEL_REF" ]]; then
  [[ -n "$VER_RAW" ]] || die "Set ORATEUR_VERSION, pass a version argument, or use an install.sh from a GitHub release."
  WHEEL_REF="https://github.com/${ORATEUR_REPO}/releases/download/v${VER_RAW}/orateur-${VER_RAW}-py3-none-any.whl"
fi

# Semver for release assets (quickshell tarball, launcher URL) when not passed explicitly.
RESOLVED_VER="$VER_RAW"
if [[ -z "$RESOLVED_VER" ]] && [[ "$WHEEL_REF" =~ orateur-([0-9]+\.[0-9]+\.[0-9]+) ]]; then
  RESOLVED_VER="${BASH_REMATCH[1]}"
fi

mkdir -p "$DATA_DIR" "$ORATEUR_BIN_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PY" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install --upgrade "$WHEEL_REF"

if [[ "${ORATEUR_SKIP_QUICKSHELL:-}" != "1" ]] && [[ -n "$RESOLVED_VER" ]]; then
  QS_URL="https://github.com/${ORATEUR_REPO}/releases/download/v${RESOLVED_VER}/quickshell-orateur.tar.gz"
  if curl -fsSL "$QS_URL" | tar -xz -C "$DATA_DIR" 2>/dev/null; then
    :
  else
    echo "Note: Optional Quickshell assets were not installed (network or missing release file)." >&2
  fi
fi

if [[ -n "${ORATEUR_LAUNCHER:-}" ]]; then
  [[ -f "$ORATEUR_LAUNCHER" ]] || die "ORATEUR_LAUNCHER is not a file: $ORATEUR_LAUNCHER"
  cp "$ORATEUR_LAUNCHER" "$LAUNCHER_DEST"
else
  [[ -n "$RESOLVED_VER" ]] || die "Cannot download launcher: set ORATEUR_LAUNCHER or a resolvable version (ORATEUR_VERSION / release wheel URL)."
  curl -fsSL "https://github.com/${ORATEUR_REPO}/releases/download/v${RESOLVED_VER}/orateur" -o "$LAUNCHER_DEST"
fi
chmod +x "$LAUNCHER_DEST"

echo "Orateur installed."
echo "  venv:    $VENV_DIR"
echo "  command: $LAUNCHER_DEST"
case ":${PATH:-}:" in
  *":$ORATEUR_BIN_DIR:"*) ;;
  *)
    echo "Add to PATH if needed: export PATH=\"$ORATEUR_BIN_DIR:\$PATH\""
    ;;
esac
echo "Next: run  orateur setup  (models / GPU STT / Quickshell), then  orateur run"
