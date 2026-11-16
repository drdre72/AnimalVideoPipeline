"""Resident Kokoro TTS worker — the model loads once, not per render.

Spawned by audio._KokoroWorker as `python -m animal_pipeline.kokoro_worker
--model <hf_path>`; stays alive for the process lifetime. Protocol is
JSON lines on stdio — one request in, one response out:

  in : {"text": "...", "voice": "af_bella", "file_prefix": "/abs/narration"}
  out: {"ok": true, "files": ["/abs/narration_000.wav", ...], "error": null}

mlx_audio's generate_audio() prints progress and swallows exceptions, so
its stdout is redirected here and success is judged by files produced.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import io
import json
import sys


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    args = ap.parse_args()

    try:
        from mlx_audio.tts.utils import load_model
        with contextlib.redirect_stdout(io.StringIO()):
            model = load_model(model_path=args.model)
        _emit({"ready": True})
    except Exception as exc:
        _emit({"ready": False, "error": str(exc)})
        return 2

    from mlx_audio.tts.generate import generate_audio

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({"ok": False, "files": [], "error": f"bad request: {exc}"})
            continue
        prefix = req["file_prefix"]
        try:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                generate_audio(text=req["text"], model=model,
                               voice=req.get("voice") or None,
                               file_prefix=prefix, audio_format="wav",
                               verbose=False)
            files = sorted(glob.glob(f"{prefix}_*.wav"))
            if files:
                _emit({"ok": True, "files": files, "error": None})
            else:
                _emit({"ok": False, "files": [],
                       "error": captured.getvalue()[-400:] or "no audio produced"})
        except Exception as exc:
            _emit({"ok": False, "files": [], "error": str(exc)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
