# Windows setup

The pipeline runs on Windows as-is: the default TTS backend is `kokoro_onnx`
(ONNX Runtime, CPU — cross-platform and much lighter than the Apple-only
`mlx_audio` path), the H.264 encoder falls back from videotoolbox to a
hardware encoder or `libx264`, and the brand font resolves from
`config/branding/` or a system font. Only the optional `service` commands
(launchd scheduling) are macOS-only and refuse elsewhere.

## Prerequisites

| Tool | Install | Notes |
|---|---|---|
| Python 3.12+ | `winget install Python.Python.3.12` | or let `uv` fetch it |
| uv | `winget install astral-sh.uv` | package/venv manager |
| ffmpeg + ffprobe | `winget install Gyan.FFmpeg` | any build with `libx264` or `h264_nvenc`/`h264_qsv`/`h264_amf` |
| whisper.cpp | build from source (`cmake -B build && cmake --build build --config Release`) or a release zip | `whisper-cli.exe` must be on `PATH` |
| Ollama | `winget install Ollama.Ollama` | optional, only for `draft`/`plan` LLM steps |

espeak-ng is **not** needed: `kokoro-onnx` pulls `espeakng-loader`, which
bundles the library.

## Setup (PowerShell)

```powershell
git clone <repo-url>
cd AnimalVideoPipeline
uv sync --no-editable --extra dev --extra tts
```

Download the two TTS model files into `models/` (~340 MB total):

```powershell
mkdir models -ea 0
curl.exe -L -o models\kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl.exe -L -o models\voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Download the whisper.cpp alignment model (~141 MB):

```powershell
curl.exe -L -o models\ggml-base.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

Optional, for plan drafting: `ollama pull qwen3:8b`

## Verify

```powershell
uv run --no-editable --extra dev --extra tts pytest   # all tests should pass
uv run --no-editable python main.py doctor            # "ok": true expected
uv run --no-editable python main.py simulate          # end-to-end synthetic render
```

`doctor` checks are platform-aware: any supported OS, a usable H.264
encoder (hardware or `libx264`), the configured TTS backend with its model
files present, whisper-cli plus the ggml model, ffmpeg/ffprobe, approved
voice, and disk headroom.

## Configuration notes

`config/settings.yaml` is platform-neutral as shipped:

- `voice.backend: kokoro_onnx` — works on every OS. `voice.model` and
  `voice.voices_file` point at the files downloaded above (relative to the
  repo root). To use the MLX backend on Apple Silicon instead, set
  `backend: kokoro_mlx` and `model: mlx-community/Kokoro-82M-bf16`.
- `render.encoder: h264_videotoolbox` is a *preference*: at render time the
  pipeline probes ffmpeg and falls back through nvenc/qsv/amf to `libx264`.
- `service` commands (install/pause/resume/status/uninstall) are
  macOS-only launchd wrappers and refuse on Windows — use `run`, `draft`,
  `render` directly, or Task Scheduler if a schedule is ever wanted.
  Routine operation does not install background services anywhere.

## What differs from macOS

- No videotoolbox — renders use a hardware encoder or `libx264`; output is
  identical H.264.
- `whisper-cli` comes from a whisper.cpp build or release zip, not a
  package manager.
- The launchd agent is unavailable; scheduled runs are out of scope anyway.
- Brand font resolution order: any `.ttf`/`.otf` in `config/branding/`,
  then system fonts (Arial Bold on Windows, DIN Alternate on macOS,
  DejaVu Sans Bold on Linux).
