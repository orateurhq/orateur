"""Centralized path constants for Orateur with XDG Base Directory support."""

import os
from pathlib import Path

HOME = Path.home()
XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))
XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state"))
XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))

CONFIG_DIR = XDG_CONFIG_HOME / "orateur"
DATA_DIR = XDG_DATA_HOME / "orateur"
STATE_DIR = XDG_STATE_HOME / "orateur"

# Fixed venv for installed users (created by orateur setup).
VENV_DIR = XDG_DATA_HOME / "orateur" / "venv"
# Editable pywhispercpp source (links to system CUDA, avoids bundled lib conflicts)
PYWHISPERCPP_SRC_DIR = XDG_DATA_HOME / "orateur" / "pywhispercpp-src"

CONFIG_FILE = CONFIG_DIR / "config.json"
MCP_SERVERS_FILE = CONFIG_DIR / "mcp-servers.json"

RECORDING_STATUS_FILE = CONFIG_DIR / "recording_status"
RECORDING_CONTROL_FILE = CONFIG_DIR / "recording_control"
LOCK_FILE = CONFIG_DIR / "orateur.lock"

TEMP_DIR = DATA_DIR / "temp"
CACHE_DIR = XDG_CACHE_HOME / "orateur"
CMD_FIFO = CACHE_DIR / "cmd.fifo"
# NDJSON written by orateur run for Quickshell (tail -F); same schema as orateur ui stdout.
UI_EVENTS_JSONL = CACHE_DIR / "ui_events.jsonl"
