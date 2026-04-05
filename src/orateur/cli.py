#!/usr/bin/env python3
"""Orateur CLI."""

# CUDA: bin/orateur sets LD_LIBRARY_PATH. _cuda_env preload conflicted with wheel
# bundled libs. Editable install (setup --build-from-source) uses system CUDA.
# from . import _cuda_env  # noqa: F401

# Configure logging before any other orateur imports
from . import log as log_config

log_config.setup_logging()

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__
from .config import ConfigManager

logger = logging.getLogger(__name__)
from .audio_capture import AudioCapture
from .llm import get_llm_backend, is_llm_disabled, list_llm_backends
from .main import _get_text_from_selection, run
from .paths import CONFIG_FILE
from .sts_pipeline import run_sts
from .stt import get_stt_backend, list_stt_backends
from .text_injector import TextInjector
from .tts import get_tts_backend, list_tts_backends


def cmd_run(args):
    """Run main loop."""
    run()


def cmd_speak(args):
    """TTS from arg, clipboard, or selection."""
    config = ConfigManager()
    text = args.text
    if not text:
        text = _get_text_from_selection(config)
    if not text:
        logger.error("No text to speak")
        return 1
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    if not tts or not tts.is_ready():
        logger.error("TTS not ready")
        return 1
    tts.synthesize_and_play(text)
    return 0


def cmd_transcribe(args):
    """Record and transcribe only."""
    config = ConfigManager()
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    if not stt or not stt.is_ready():
        logger.error("STT not ready")
        return 1
    audio = AudioCapture(config=config)
    logger.info("Recording... (Ctrl+C to stop)")
    try:
        audio.start_recording()
        while True:
            import time

            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    logger.info("Stopping...")
    # Run stop_recording in a thread so main thread stays responsive to Ctrl+C
    result: list = []

    def _run_stop():
        try:
            result.append(audio.stop_recording())
        except Exception as e:
            result.append(e)

    worker = threading.Thread(target=_run_stop, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("Stopping... (please wait)")
        worker.join(timeout=5.0)
    data = result[0] if result else None
    if isinstance(data, BaseException):
        raise data
    if data is None:
        logger.error("No audio")
        return 1
    logger.info("Transcribing...")
    text = stt.transcribe(data)
    if not text or not text.strip():
        logger.error("No transcription")
        return 1
    injector = TextInjector(config)
    if not injector.inject_text(text):
        logger.warning("Could not paste - text copied to clipboard")
    print(text)
    return 0


def cmd_sts(args):
    """Speech-to-Speech: record -> STT -> LLM -> TTS."""
    config = ConfigManager()
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    llm_name = config.get_setting("llm_backend", "ollama")
    if is_llm_disabled(llm_name):
        logger.error("STS needs an LLM; set llm_backend to ollama (currently %s)", llm_name)
        return 1
    llm = get_llm_backend(llm_name, config)
    if not all([stt and stt.is_ready(), tts and tts.is_ready(), llm and llm.is_ready()]):
        logger.error("STT, TTS, or LLM not ready")
        return 1
    audio = AudioCapture(config=config)
    logger.info("Recording for STS... (Ctrl+C to stop)")
    try:
        audio.start_recording()
        while True:
            import time

            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    logger.info("Stopping...")
    result: list = []

    def _run_stop():
        try:
            result.append(audio.stop_recording())
        except Exception as e:
            result.append(e)

    worker = threading.Thread(target=_run_stop, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("Stopping... (please wait)")
        worker.join(timeout=5.0)
    data = result[0] if result else None
    if isinstance(data, BaseException):
        raise data
    if data is None:
        logger.error("No audio")
        return 1
    run_sts(config, data, stt=stt, tts=tts, llm=llm)
    return 0


def cmd_config_init(args):
    config = ConfigManager()
    config.save_config()
    print(f"Config initialized: {CONFIG_FILE}")
    return 0


def cmd_config_show(args):
    config = ConfigManager()
    print(json.dumps(config.config, indent=2))
    return 0


def cmd_config_edit(args):
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(CONFIG_FILE)])
    return 0


def cmd_systemd_install(args):
    # Find project root (orateur package root)
    project_root = os.environ.get("ORATEUR_ROOT") or Path(__file__).resolve().parent.parent.parent
    project_root = Path(project_root)
    src = project_root / "config" / "orateur.service"
    if not src.exists():
        content = _default_service_content()
    else:
        content = src.read_text()
    dest = Path.home() / ".config" / "systemd" / "user" / "orateur.service"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Use launcher when in dev (bin/orateur exists); else assume orateur in PATH
    launcher = project_root / "bin" / "orateur"
    if launcher.exists():
        exec_start = f"/usr/bin/env bash -lc 'cd {project_root} && ./bin/orateur run'"
    else:
        exec_start = "orateur run"
    content = content.replace("{{EXEC_START}}", exec_start)
    dest.write_text(content)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "orateur.service"])
    subprocess.run(["systemctl", "--user", "start", "orateur.service"])
    print("Systemd service installed and started")
    return 0


def _default_service_content():
    return """[Unit]
Description=Orateur speech-to-text
After=graphical-session.target pipewire.service
Wants=pipewire.service

[Service]
Type=simple
ExecStart={{EXEC_START}}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
"""


def cmd_systemd_status(args):
    r = subprocess.run(["systemctl", "--user", "status", "orateur.service"], capture_output=True, text=True)
    print(r.stdout or r.stderr)
    return r.returncode


def cmd_systemd_restart(args):
    subprocess.run(["systemctl", "--user", "restart", "orateur.service"])
    return 0


def cmd_model_list(args):
    config = ConfigManager()
    print("STT backends:", ", ".join(list_stt_backends()))
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    if stt:
        print("STT models:", ", ".join(stt.get_available_models()) if stt.get_available_models() else "(none)")
    print("TTS backends:", ", ".join(list_tts_backends()))
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    if tts:
        print("TTS voices:", ", ".join(tts.get_available_voices()) if tts.get_available_voices() else "(none)")
    print("LLM backends:", ", ".join(list_llm_backends()))
    llm_name = config.get_setting("llm_backend", "ollama")
    if is_llm_disabled(llm_name):
        print(f"LLM models: (disabled — llm_backend={llm_name})")
    else:
        llm = get_llm_backend(llm_name, config)
        if llm:
            print("LLM models:", ", ".join(llm.get_available_models()) if llm.get_available_models() else "(none)")
    return 0


def cmd_mcp_list(args):
    config = ConfigManager()
    servers = config.get_setting("mcpServers") or {}
    if not servers:
        print("No MCP servers configured (add mcpServers to config.json)")
        return 0
    for name, cfg in servers.items():
        if isinstance(cfg, dict):
            cmd = cfg.get("command", "?")
            args = cfg.get("args", [])
            args_str = " ".join(str(a) for a in args) if args else ""
            print(f"  {name}: {cmd} {args_str}".strip())
        else:
            print(f"  {name}: {cfg}")
    return 0


def cmd_shortcuts_list(args):
    config = ConfigManager()
    print("primary_shortcut:", config.get_setting("primary_shortcut"))
    print("secondary_shortcut:", config.get_setting("secondary_shortcut"))
    print("tts_shortcut:", config.get_setting("tts_shortcut"))
    print("sts_shortcut:", config.get_setting("sts_shortcut"))
    return 0


def cmd_ui(args):
    """Run UI daemon for Quickshell (FIFO commands, JSON events on stdout)."""
    from .ui_daemon import _run_ui_daemon

    _run_ui_daemon(events_only=getattr(args, "events_only", False))
    return 0


def cmd_ui_send(args):
    """Send a JSON command to the UI daemon (reads from stdin or first arg)."""
    import sys as _sys

    from .paths import CACHE_DIR, CMD_FIFO

    data = getattr(args, "json_data", None) or ""
    if not data:
        data = _sys.stdin.read().strip()
    if not data:
        logger.error("No JSON data (pass as arg or stdin)")
        return 1
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CMD_FIFO.exists():
        logger.error("UI daemon not running (FIFO not found). Run 'orateur ui' first.")
        return 1
    try:
        with open(CMD_FIFO, "w", encoding="utf-8") as f:
            f.write(data)
            if not data.endswith("\n"):
                f.write("\n")
    except OSError as e:
        logger.error("Failed to write to FIFO: %s", e)
        return 1
    return 0


def cmd_setup(args):
    """Install GPU-accelerated pywhispercpp (CUDA, Metal, or PyPI CPU)."""
    from .install_quickshell import install_quickshell
    from .install_stt import (
        _build_pywhispercpp_cuda_from_source,
        _build_pywhispercpp_metal_from_source,
        _is_apple_silicon,
        _is_linux_x86_64,
        download_whisper_model,
        install_pywhispercpp,
    )

    force = getattr(args, "force", False)
    if getattr(args, "build_from_source", False):
        if _is_apple_silicon():
            ok = _build_pywhispercpp_metal_from_source(force=force)
        elif _is_linux_x86_64():
            ok = _build_pywhispercpp_cuda_from_source(force=force)
        else:
            logger.error("--build-from-source is supported on Linux x86_64 (CUDA) or macOS Apple Silicon (Metal)")
            return 1
        if not ok:
            return 1
        config = ConfigManager()
        if not download_whisper_model(config.get_setting("stt_model", "base")):
            logger.warning("Whisper model download failed; first run will try again (needs network)")
        install_quickshell()
        return 0

    backend = getattr(args, "backend", "auto")
    if backend == "auto":
        backend = None
    ok = install_pywhispercpp(backend=backend, force=force)
    if ok:
        config = ConfigManager()
        if not download_whisper_model(config.get_setting("stt_model", "base")):
            logger.warning("Whisper model download failed; first run will try again (needs network)")
    install_quickshell()
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(prog="orateur", description="Minimal local speech-to-text and speech-to-speech")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print version and exit",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run main loop (systemd)")
    sp = sub.add_parser("speak", help="TTS from selection/clipboard")
    sp.add_argument("text", nargs="?", help="Text to speak")
    sub.add_parser("transcribe", help="Record and transcribe")
    sub.add_parser("sts", help="Speech-to-Speech")
    ui_p = sub.add_parser("ui", help="UI daemon for Quickshell (JSON-RPC over FIFO/stdout)")
    ui_p.add_argument(
        "--events-only",
        action="store_true",
        help="FIFO relay only (no STT/TTS/LLM). For Quickshell when orateur run holds models.",
    )
    ui_p.add_argument("--send", dest="ui_send", action="store_true", help=argparse.SUPPRESS)
    ui_send_p = sub.add_parser("ui-send", help="Send command to UI daemon (for Quickshell)")
    ui_send_p.add_argument("json_data", nargs="?", help="JSON command (or read from stdin)")

    cfg = sub.add_parser("config", help="Config")
    cfg_sub = cfg.add_subparsers(dest="config_action")
    cfg_sub.add_parser("init")
    cfg_sub.add_parser("show")
    cfg_sub.add_parser("edit")

    sd = sub.add_parser("systemd", help="Systemd")
    sd_sub = sd.add_subparsers(dest="systemd_action")
    sd_sub.add_parser("install")
    sd_sub.add_parser("status")
    sd_sub.add_parser("restart")

    model_p = sub.add_parser("model", help="Models")
    model_sub = model_p.add_subparsers(dest="model_action")
    model_sub.add_parser("list")
    mcp_p = sub.add_parser("mcp", help="MCP servers")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("list")
    sub.add_parser("shortcuts", help="List shortcuts")

    setup_p = sub.add_parser("setup", help="Install GPU-accelerated pywhispercpp (optional)")
    setup_p.add_argument(
        "--backend",
        choices=["auto", "nvidia", "metal", "cpu"],
        default="auto",
        help="auto: CUDA (Linux+GPU) or Metal (Apple Silicon); nvidia/metal/cpu: force that backend",
    )
    setup_p.add_argument(
        "--build-from-source",
        action="store_true",
        help="Force editable build: CUDA on Linux x86_64, Metal on Apple Silicon",
    )
    setup_p.add_argument(
        "--force",
        action="store_true",
        help="Reinstall pywhispercpp even if already installed",
    )

    args = parser.parse_args()

    if args.command == "run":
        return cmd_run(args) or 0
    if args.command == "speak":
        return cmd_speak(args)
    if args.command == "transcribe":
        return cmd_transcribe(args)
    if args.command == "sts":
        return cmd_sts(args)
    if args.command == "ui":
        if getattr(args, "ui_send", False):
            return cmd_ui_send(args)
        return cmd_ui(args)
    if args.command == "ui-send":
        return cmd_ui_send(args)
    if args.command == "config":
        if args.config_action == "init":
            return cmd_config_init(args)
        if args.config_action == "show":
            return cmd_config_show(args)
        if args.config_action == "edit":
            return cmd_config_edit(args)
        cfg.print_help()
        return 0
    if args.command == "systemd":
        if args.systemd_action == "install":
            return cmd_systemd_install(args)
        if args.systemd_action == "status":
            return cmd_systemd_status(args)
        if args.systemd_action == "restart":
            return cmd_systemd_restart(args)
        sd.print_help()
        return 0
    if args.command == "model":
        if getattr(args, "model_action", None) == "list":
            return cmd_model_list(args)
        return cmd_model_list(args)
    if args.command == "mcp":
        if getattr(args, "mcp_action", None) == "list":
            return cmd_mcp_list(args)
        return cmd_mcp_list(args)
    if args.command == "shortcuts":
        return cmd_shortcuts_list(args)
    if args.command == "setup":
        return cmd_setup(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
