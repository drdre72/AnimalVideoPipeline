from __future__ import annotations

import subprocess

from .core import ROOT, connect
from .creative import save_plan
from .editor import compose
from .evidence import add_fact, add_topic, approve_fact
from .media import approve_asset, import_asset


def demo() -> dict:
    cache = ROOT / "cache" / "demo"
    cache.mkdir(parents=True, exist_ok=True)
    video = cache / "synthetic.mp4"
    audio = cache / "tone.wav"
    if not video.exists():
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=3",
                        "-c:v", "h264_videotoolbox", "-b:v", "2M", str(video)],
                       check=True, timeout=90)
    if not audio.exists():
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:a", "pcm_s16le", str(audio)], check=True, timeout=60)
    conn = connect()
    try:
        topic = add_topic(conn, "Synthetic test pattern", "Fixture only", "Biological Superpowers")
        fact = add_fact(conn, topic, "This is synthetic test footage.", "Naminal Hub test",
                        "local:synthetic-test-pattern", "This is synthetic test footage.",
                        "2026-01-01")
        approve_fact(conn, fact)
        asset = import_asset(conn, video, source_ref="local:synthetic-test-pattern",
                             creator="Naminal Hub test", license_type="original synthetic fixture",
                             evidence="generated in simulation.py", attribution="Synthetic test pattern",
                             species="Synthetic test pattern", commercial=True,
                             modifications=True, test_only=True)
        approve_asset(conn, asset)
        plan = save_plan(conn, {"topic_id": topic, "title": "Synthetic test only",
                                "hooks": ["Test A", "Test B", "Test C"],
                                "segments": [{"text": "This is synthetic test footage.",
                                              "fact_ids": [fact], "asset_id": asset,
                                              "start_s": 0, "duration_s": 2}]}, simulation=True)
        result = compose(conn, plan, audio, simulation=True)
        return {"plan_id": plan, "video": result["video"], "qc": result["qc"],
                "warning": "Synthetic test pattern and tone; not wildlife; cannot be approved or exported"}
    finally:
        conn.close()
