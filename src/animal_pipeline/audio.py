from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .core import new_id, settings


def synthesize(text: str, dest: Path) -> Path:
    voice = settings()["voice"]
    if voice["backend"] != "kokoro_mlx" or not voice["approved"] or not voice["name"]:
        raise RuntimeError("an explicitly approved Kokoro voice is required")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = dest.with_name(new_id("narration"))
    proc = subprocess.run([sys.executable, "-m", "mlx_audio.tts.generate",
                           "--model", voice["model"], "--voice", voice["name"],
                           "--text", text, "--file_prefix", str(prefix),
                           "--audio_format", "wav"], capture_output=True, text=True, timeout=600)
    parts = sorted(dest.parent.glob(prefix.name + "_*.wav"))
    if proc.returncode or not parts:
        raise RuntimeError("Kokoro narration failed or produced no audio")
    if len(parts) == 1:
        parts[0].replace(dest)
    else:
        tmp = dest.with_name(dest.stem + ".part.wav")
        inputs = [arg for part in parts for arg in ("-i", str(part))]
        concat = "".join(f"[{i}:a]" for i in range(len(parts)))
        result = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
             "-filter_complex", f"{concat}concat=n={len(parts)}:v=0:a=1[a]",
             "-map", "[a]", "-c:a", "pcm_s16le", str(tmp)],
            capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("Kokoro narration concatenation failed")
        os.replace(tmp, dest)
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
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
        capture_output=True, text=True, check=True, timeout=60).stdout.strip())
    chunks = json.loads(prefix.with_suffix(".json").read_text()).get("transcription", [])
    captions = []
    for chunk in chunks:
        start = chunk["offsets"]["from"] / 1000
        end = min(duration, chunk["offsets"]["to"] / 1000)
        words = chunk.get("text", "").split()
        if not words or start >= duration or end <= start:
            continue
        groups: list[list[str]] = []
        for word in words:
            if not groups or len(groups[-1]) == 6 or len(" ".join([*groups[-1], word])) > 42:
                groups.append([])
            groups[-1].append(word)
        consumed = 0
        for group in groups:
            caption_start = start + (end - start) * consumed / len(words)
            consumed += len(group)
            caption_end = start + (end - start) * consumed / len(words)
            captions.append({"start": caption_start, "end": caption_end,
                             "text": " ".join(group)})
    if any(c["end"] <= c["start"] for c in captions):
        raise RuntimeError("invalid caption timestamps")
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
