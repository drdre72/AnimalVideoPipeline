import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from animal_pipeline import analytics, creative, editor, evidence, media, review, scheduler
from animal_pipeline.core import connect, digest


@pytest.fixture
def footage(tmp_path):
    path = tmp_path / "fennec.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=30",
                    "-c:v", "h264_videotoolbox", "-b:v", "1M", str(path)],
                   check=True, timeout=120)
    return path


def test_multi_day_desk_cycle(tmp_path, monkeypatch, footage):
    """Three-day operator cycle through the real pipeline gates.

    Only the two ML calls (Kokoro synthesis and whisper alignment) are
    replaced with a deterministic tone and fixed captions; every render,
    rights, claims, QC, approval, export and scheduler gate runs for real.
    """
    monkeypatch.setattr(editor, "ROOT", tmp_path)
    monkeypatch.setattr(review, "ROOT", tmp_path)
    conn = connect(tmp_path / "desk.db")
    duration = 24.0

    def fake_synthesize(text, dest):
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
                        "-c:a", "pcm_s16le", str(dest)], check=True, timeout=60)
        return dest

    monkeypatch.setattr(editor, "synthesize", fake_synthesize)
    monkeypatch.setattr(editor, "align", lambda audio, script, prefix: [
        {"start": i * 6.0, "end": (i + 1) * 6.0, "text": f"caption {i}"}
        for i in range(4)])

    # Day 1: topic, evidence and rights-cleared footage
    topic = evidence.add_topic(conn, "Fennec fox", "Vulpes zerda", "Tiny but Extraordinary")
    statements = ("The fennec fox is the smallest wild canid.",
                  "Its oversized ears dissipate heat in the Sahara.",
                  "Fennecs live in underground dens.")
    facts = []
    for statement in statements:
        facts.append(evidence.add_fact(conn, topic, statement, "Fixture library",
                                       "local:fennec", statement, "2026-01-01"))
    bad = evidence.add_fact(conn, topic, "Unverified rumour.", "Fixture library",
                            "local:fennec", "Unverified rumour.", "2026-01-01")
    evidence.reject_fact(conn, bad)
    for fact in facts:
        evidence.approve_fact(conn, fact)
    asset = media.import_asset(conn, footage, source_ref="local:fennec",
                               creator="Fixture", license_type="original",
                               evidence="generated locally for acceptance",
                               attribution="Fixture", species="Fennec fox",
                               commercial=True, modifications=True)
    media.approve_asset(conn, asset)
    segments = [{"text": statements[i // 2], "fact_ids": [facts[i // 2]],
                 "asset_id": asset, "start_s": i * 4, "duration_s": 4}
                for i in range(6)]
    plan_id = creative.save_plan(
        conn, {"topic_id": topic, "title": "Fennec fox", "hooks": ["a", "b", "c"],
               "segments": segments}, footage_first=True,
        reason="Acceptance draft uses distinct footage windows only")

    # Day 2: queue, crash, recover, render through the real QC gates
    assert scheduler.enqueue(conn, "render", plan_id)
    job = conn.execute("SELECT id FROM jobs").fetchone()[0]
    stale = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    conn.execute("UPDATE jobs SET status='running',claimed_at=? WHERE id=?", (stale, job))
    assert scheduler.recover(conn) == 1
    results = asyncio.run(scheduler.drain(conn, editor.compose, limit=4))
    assert results[0]["result"]["qc"]["ok"]
    assert results[0]["result"]["qc"]["checks"]["loudness"]
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    assert row["status"] == "READY_FOR_REVIEW" and not review.review_errors(conn, row)
    review.approve(conn, plan_id, row["video_hash"],
                   verified=("facts", "visuals", "rights", "audio"))
    package = review.export(conn, plan_id, "youtube")
    exported = Path(package["directory"]) / "Naminal_Hub.mp4"
    assert digest(exported) == row["video_hash"]

    # Day 3: analytics steer ranking; a superseded fact revokes approval
    second = evidence.add_topic(conn, "Pangolin", "Manis", "Tiny but Extraordinary")
    snapshot = tmp_path / "analytics.json"
    snapshot.write_text(json.dumps([{"video_id": plan_id, "platform": "youtube",
        "published_at": "2026-01-01T00:00:00+00:00",
        "observed_at": "2026-01-03T00:00:00+00:00", "views": 600,
        "source": "operator import"}]))
    analytics.import_snapshots(conn, snapshot)
    ranked = creative.rank_topics(conn)
    assert ranked[0]["id"] == topic and ranked[0]["reasons"]["analytics"]
    newer = evidence.add_fact(conn, topic, "The fennec fox is the smallest canid overall.",
                              "Fixture library", "local:fennec-v2",
                              "The fennec fox is the smallest canid overall.",
                              "2026-01-03", supersedes=facts[0])
    stale_plan = conn.execute("SELECT status,approved_hash FROM plans WHERE id=?",
                              (plan_id,)).fetchone()
    assert (stale_plan["status"], stale_plan["approved_hash"]) == ("CHANGES_REQUESTED", None)
    with pytest.raises(ValueError):
        review.export(conn, plan_id, "youtube")
    evidence.approve_fact(conn, newer)
    assert evidence.validate_segments(
        conn, topic, [{"text": "The fennec fox is the smallest canid overall.",
                       "fact_ids": [newer]}]) == []
    conn.close()
