#!/usr/bin/env python3
"""Orateur CLI."""

# CUDA: bin/orateur sets LD_LIBRARY_PATH. _cuda_env preload conflicted with wheel
# bundled libs. Editable install (setup --build-from-source) uses system CUDA.
# from . import _cuda_env  # noqa: F401

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .config import ConfigManager
from .paths import CONFIG_DIR, CONFIG_FILE, MCP_SERVERS_FILE
from .stt import get_stt_backend, list_stt_backends
from .tts import get_tts_backend, list_tts_backends
from .llm import get_llm_backend, list_llm_backends
from .audio_capture import AudioCapture
from .sts_pipeline import run_sts
from .text_injector import TextInjector
from .main import _get_text_from_selection, run


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
        print("No text to speak")
        return 1
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    if not tts or not tts.is_ready():
        print("TTS not ready")
        return 1
    tts.synthesize_and_play(text)
    return 0


def cmd_transcribe(args):
    """Record and transcribe only."""
    config = ConfigManager()
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    if not stt or not stt.is_ready():
        print("STT not ready")
        return 1
    audio = AudioCapture(config=config)
    print("Recording... (Ctrl+C to stop)")
    try:
        audio.start_recording()
        while True:
            import time
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    print("Stopping...", flush=True)
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
        print("\nStopping... (please wait)", flush=True)
        worker.join(timeout=5.0)
    data = result[0] if result else None
    if isinstance(data, BaseException):
        raise data
    if data is None:
        print("No audio")
        return 1
    print("Transcribing...", flush=True)
    text = stt.transcribe(data)
    if not text or not text.strip():
        print("No transcription")
        return 1
    injector = TextInjector(config)
    if not injector.inject_text(text):
        print("[WARN] Could not paste - text copied to clipboard", flush=True)
    print(text)
    return 0


def cmd_sts(args):
    """Speech-to-Speech: record -> STT -> LLM -> TTS."""
    config = ConfigManager()
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    llm = get_llm_backend(config.get_setting("llm_backend", "ollama"), config)
    if not all([stt and stt.is_ready(), tts and tts.is_ready(), llm and llm.is_ready()]):
        print("STT, TTS, or LLM not ready")
        return 1
    audio = AudioCapture(config=config)
    print("Recording for STS... (Ctrl+C to stop)")
    try:
        audio.start_recording()
        while True:
            import time
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    print("Stopping...", flush=True)
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
        print("\nStopping... (please wait)", flush=True)
        worker.join(timeout=5.0)
    data = result[0] if result else None
    if isinstance(data, BaseException):
        raise data
    if data is None:
        print("No audio")
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
    llm = get_llm_backend(config.get_setting("llm_backend", "ollama"), config)
    if llm:
        print("LLM models:", ", ".join(llm.get_available_models()) if llm.get_available_models() else "(none)")
    return 0


def cmd_mcp_list(args):
    if not MCP_SERVERS_FILE.exists():
        print("No MCP servers configured")
        return 0
    data = json.loads(MCP_SERVERS_FILE.read_text())
    for name, cfg in data.items():
        print(f"  {name}: {cfg.get('description', cfg)}")
    return 0


def cmd_shortcuts_list(args):
    config = ConfigManager()
    print("primary_shortcut:", config.get_setting("primary_shortcut"))
    print("secondary_shortcut:", config.get_setting("secondary_shortcut"))
    print("tts_shortcut:", config.get_setting("tts_shortcut"))
    print("sts_shortcut:", config.get_setting("sts_shortcut"))
    return 0


def cmd_setup(args):
    """Install GPU-accelerated pywhispercpp (detects CUDA, builds from source or uses PyPI)."""
    from .install_stt import install_pywhispercpp, _build_pywhispercpp_cuda_from_source

    if getattr(args, "build_from_source", False):
        ok = _build_pywhispercpp_cuda_from_source()
        return 0 if ok else 1

    backend = getattr(args, "backend", "auto")
    if backend == "auto":
        backend = None
    ok = install_pywhispercpp(backend=backend)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(prog="orateur", description="Minimal local speech-to-text and speech-to-speech")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run main loop (systemd)")
    sp = sub.add_parser("speak", help="TTS from selection/clipboard")
    sp.add_argument("text", nargs="?", help="Text to speak")
    sub.add_parser("transcribe", help="Record and transcribe")
    sub.add_parser("sts", help="Speech-to-Speech")

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
        choices=["auto", "nvidia", "cpu"],
        default="auto",
        help="Backend: auto (detect CUDA), nvidia (force CUDA build), cpu (PyPI only)",
    )
    setup_p.add_argument(
        "--build-from-source",
        action="store_true",
        help="Force build from source with CUDA (for Blackwell/new GPUs when 'no GPU found')",
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
