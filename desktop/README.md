# Orateur desktop (Tauri)

A small **status overlay** that follows **`ui_events.jsonl`** with the same event semantics as the Quickshell panel (`quickshell/orateur/OrateurWidget.qml`): waveform, timers, and status text.

## Prerequisites

- [Node.js](https://nodejs.org/) (for `npm`)
- [Rust](https://rustup.rs/) (for the Tauri backend)

## Development

1. **Run the Orateur daemon** so the JSONL file is written (from the repo root):

   ```bash
   uv sync
   uv run orateur run
   ```

   Ensure **`ui_events_mirror`** is **`true`** in **`~/.config/orateur/config.json`** (default) so events are appended to **`~/.cache/orateur/ui_events.jsonl`**.

2. **Start the Tauri app** (in this directory):

   ```bash
   npm install
   npm run tauri dev
   ```

The **overlay** window is borderless and transparent: drag it by the bar, use **×** to hide it to the tray, or use the **tray icon** (menu bar on macOS, system tray on Linux/Windows):

- **Show status bar** — brings the overlay back
- **Settings** — path to `ui_events.jsonl` and “Apply path & restart tail”
- **Quit** — exit the app

Closing either window hides it (the app keeps running until **Quit**). The backend still tails `ui_events.jsonl` from the end of the file on connect (like `tail -n0 -F`).

## Build

```bash
npm run build
cargo build --release --manifest-path src-tauri/Cargo.toml
```

Use **`npm run tauri build`** when your environment supports the full bundler (icons, platform packages).

## Control model

See [CONTROL.md](./CONTROL.md) for how this relates to **`orateur run`**, shortcuts, and **`cmd.fifo`**.
