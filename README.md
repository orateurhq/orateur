# Orateur

Minimal python local speech-to-text, text-to-speech and speech-to-speech assistant.

## Features

- **STT**: Whisper (pywhispercpp) for transcription
- **TTS**: Pocket TTS for text-to-speech
- **STS**: Speech-to-Speech (STT → Ollama/LLM → TTS)
- **MCP**: LLM plugin system via Model Context Protocol
- **Shortcuts**: Global keyboard shortcuts (evdev)
- **Systemd**: Background service with pre-loaded models

## Installation

### From package manager (no uv required)

When installed via your distro (e.g. AUR), run setup once to create the venv and install GPU support:

```bash
orateur setup
```

### Development (with uv)

```bash
cd orateur
uv sync
```

## GPU acceleration (NVIDIA CUDA)

The default `pywhispercpp` wheel is CPU-only. Run setup to install a CUDA build for your GPU:

```bash
# Installed users
orateur setup

# Development
uv run orateur setup
```

Setup detects CUDA (via `nvcc` or `nvidia-smi`) and either builds pywhispercpp from source with GPU support (Linux x86_64) or installs the CPU wheel from PyPI.

Options:

```bash
orateur setup --backend auto   # default: detect CUDA
orateur setup --backend nvidia # force CUDA build (fails if no CUDA)
orateur setup --backend cpu    # PyPI CPU only
orateur setup --build-from-source  # force build from source (e.g. CUDA 13+ / Blackwell GPUs)
```

On non-Linux x86_64 or when CUDA is not detected, setup uses PyPI (CPU). GPU build may take several minutes.

## Usage

```bash
# Run main loop (used by systemd)
orateur run

# Transcribe
orateur transcribe

# Speech-to-Speech
orateur sts

# TTS from selection
orateur speak
```

For development, prefix with `uv run`:

```bash
uv run orateur run
uv run orateur transcribe
```

## Configuration

Config: `~/.config/orateur/config.json`

```bash
orateur config init
orateur config show
```

### MCP servers (Cursor-style)

Define MCP servers in `mcpServers`. The LLM has access to all of them: one must expose `llm_generate` for STS, and tools from every server are aggregated for the LLM to use.

```json
{
  "mcpServers": {
    "weather-forecast": {
      "command": "uvx",
      "args": ["weather-forecast-server"]
    },
    "my-llm": {
      "command": "uvx",
      "args": ["my-mcp-llm-package"]
    }
  },
  "llm_backend": "mcp"
}
```

- **mcpServers**: Named servers with `command` and `args` (Cursor-compatible)
- One server must expose `llm_generate` or `llm_chat` for STS
- Tools from all servers are passed to the LLM

List configured servers with `orateur mcp list`.

## Stopping

- **Ctrl+C** in the terminal stops `orateur run`
- If `kill <pid>` doesn't work: kill the Python process (the one with higher memory in `ps aux`), or use `pkill -f "orateur run"` to stop all
