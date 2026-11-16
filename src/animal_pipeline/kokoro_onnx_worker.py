"""Resident Kokoro-ONNX TTS worker — same stdio JSON protocol as
kokoro_worker, but backed by the ONNX Runtime build so narration runs on
Windows and Linux as well as macOS (mlx_audio is Apple Silicon only).

Spawned by audio._KokoroWorker as `python -m animal_pipeline.kokoro_onnx_worker
--model <path to kokoro-v1.0.onnx> --voices <path to voices-v1.0.bin>`.
With --text it synthesizes once and exits (cold fallback path).
"""
from __future__ import annotations

import argparse
import json
import re
import sys


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    return parts or [text]


def _synth(kokoro, text: str, voice: str, prefix: str) -> list[str]:
    import soundfile as sf
    files = []
    for index, sentence in enumerate(_sentences(text)):
        samples, rate = kokoro.create(sentence, voice=voice, speed=1.0, lang="en-us")
        out = f"{prefix}_{index:03d}.wav"
        sf.write(out, samples, rate)
        files.append(out)
    return files


def _handle(kokoro, req: dict) -> dict:
    try:
        files = _synth(kokoro, req["text"], req.get("voice") or "af_heart",
                       req["file_prefix"])
        return {"ok": True, "files": files, "error": None}
    except Exception as exc:
        return {"ok": False, "files": [], "error": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--voices", required=True)
    ap.add_argument("--text")
    ap.add_argument("--voice")
    ap.add_argument("--file_prefix")
    args = ap.parse_args()

    try:
        from kokoro_onnx import Kokoro
        kokoro = Kokoro(args.model, args.voices)
        _emit({"ready": True})
    except Exception as exc:
        _emit({"ready": False, "error": str(exc)})
        return 2

    if args.text is not None:
        result = _handle(kokoro, {"text": args.text, "voice": args.voice,
                                  "file_prefix": args.file_prefix})
        _emit(result)
        return 0 if result["ok"] else 3

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({"ok": False, "files": [], "error": f"bad request: {exc}"})
            continue
        _emit(_handle(kokoro, req))
    return 0


if __name__ == "__main__":
    sys.exit(main())
