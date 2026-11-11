from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .core import settings


def synthesize(text: str, dest: Path) -> Path:
    voice = settings()["voice"]
    if voice["backend"] != "kokoro_mlx" or not voice["approved"] or not voice["name"]:
        raise RuntimeError("an explicitly approved Kokoro voice is required")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = dest.with_suffix("")
    proc = subprocess.run([sys.executable, "-m", "mlx_audio.tts.generate",
                           "--model", voice["model"], "--voice", voice["name"],
                           "--text", text, "--file_prefix", str(prefix),
                           "--audio_format", "wav"], capture_output=True, text=True, timeout=600)
    parts = sorted(dest.parent.glob(prefix.name + "_*.wav"))
    if proc.returncode or len(parts) != 1:
        raise RuntimeError("Kokoro narration failed or produced multiple unverified files")
    parts[0].replace(dest)
    return dest


def align(audio: Path, script: str, prefix: Path) -> list[dict]:
    model = Path(settings()["alignment"]["model_path"])
    if not model.is_absolute():
        from .core import ROOT
        model = ROOT / model
    if not model.is_file() or not shutil.which("whisper-cli"):
        raise RuntimeError("whisper.cpp binary and local model are required for captions")
    cmd = ["whisper-cli", "-m", str(model), "-f", str(audio), "-oj",
           "-of", str(prefix), "-nt"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError("whisper alignment failed")
    chunks = json.loads(prefix.with_suffix(".json").read_text()).get("transcription", [])
    captions = [{"start": c["offsets"]["from"] / 1000, "end": c["offsets"]["to"] / 1000,
                 "text": c["text"].strip()} for c in chunks if c.get("text", "").strip()]
    words = lambda s: set(re.findall(r"[a-z]+", s.lower()))
    expected = words(script)
    actual = words(" ".join(c["text"] for c in captions))
    if not captions or (expected and len(expected & actual) / len(expected) < 0.75):
        raise RuntimeError("caption alignment does not match narration script")
    return captions


def write_srt(captions: list[dict], path: Path) -> None:
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        hours, ms = divmod(ms, 3600000)
        minutes, ms = divmod(ms, 60000)
        secs, ms = divmod(ms, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"
    path.write_text("\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}"
                                for i, c in enumerate(captions, 1)) + "\n")
