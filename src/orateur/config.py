"""Configuration manager for Orateur."""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict

from .paths import CONFIG_DIR, CONFIG_FILE

log = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration and settings."""

    def __init__(self):
        self.default_config = {
            "primary_shortcut": "SUPER+ALT+D",
            "secondary_shortcut": "SUPER+ALT+E",
            "tts_shortcut": "SUPER+ALT+T",
            "sts_shortcut": "SUPER+ALT+S",
            "recording_mode": "toggle",
            "grab_keys": False,
            "selected_device_path": None,
            "selected_device_name": None,
            "audio_device_id": None,
            "audio_device_name": None,
            "stt_backend": "pywhispercpp",
            "stt_model": "base",
            "stt_language": None,
            "stt_language_secondary": None,
            "stt_threads": 4,
            "stt_whisper_prompt": "Transcribe with proper capitalization.",
            "stt_whisper_prompt_secondary": None,
            "stt_whisper_verbose": False,
            "tts_backend": "pocket_tts",
            "tts_voice": "alba",
            "tts_volume": 1.0,
            "llm_backend": "ollama",
            "llm_model": "llama3.2",
            "llm_system_prompt": "You are a helpful assistant. Respond concisely.",
            "llm_base_url": "http://localhost:11434",
            "llm_mcp_transport": "stdio",
            "llm_mcp_url": None,
            "llm_mcp_tool": "llm_generate",
            "mcpServers": {},
            "paste_mode": "ctrl_shift",
            "paste_keycode": 47,
        }

        self.config_dir = CONFIG_DIR
        self.config_file = CONFIG_FILE
        self.config = copy.deepcopy(self.default_config)
        self._ensure_config_dir()
        self._load_config()

    def _ensure_config_dir(self) -> None:
        """Ensure the configuration directory exists."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.warning("Could not create config directory: %s", e)

    def _load_config(self) -> None:
        """Load configuration from file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                loaded.pop("$schema", None)
                self.config.update(loaded)
        except Exception as e:
            log.warning("Could not load config: %s", e)

    def save_config(self) -> bool:
        """Save current configuration to file."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True
        except Exception as e:
            log.error("Could not save config: %s", e)
            return False

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a configuration setting."""
        return self.config.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        """Set a configuration setting."""
        self.config[key] = value

    def get_temp_directory(self) -> Path:
        """Get the temporary directory for audio files."""
        from .paths import TEMP_DIR
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        return TEMP_DIR
