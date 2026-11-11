import asyncio
import json
import subprocess
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from animal_pipeline import analytics, budget, competitors, creative, editor, evidence, media, review, scheduler, server
from animal_pipeline.core import connect


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def footage(tmp_path):
    path = tmp_path / "fixture.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3",
                    "-c:v", "h264_videotoolbox", "-b:v", "1M", str(path)],
                   check=True, timeout=60)
    return path


@pytest.fixture
def setup(conn, footage):
    topic = evidence.add_topic(conn, "Fixture animal", "Species fixture", "Tiny but Extraordinary")
    fact = evidence.add_fact(conn, topic, "The fixture has 2 ears.", "Test collection",
                             "local:study", "The fixture has 2 ears.", "2026-01-01")
    evidence.approve_fact(conn, fact)
    asset = media.import_asset(conn, footage, source_ref="local:fixture", creator="Test operator",
                               license_type="original", evidence="test fixture generated locally",
                               attribution="Test operator", species="Fixture animal",
                               commercial=True, modifications=True)
    media.approve_asset(conn, asset)
    return topic, fact, asset


def plan(topic, fact, asset, text="The fixture has 2 ears."):
    return {"topic_id": topic, "title": "Fixture", "hooks": ["One", "Two", "Three"],
            "segments": [{"text": text, "fact_ids": [fact], "asset_id": asset,
                          "start_s": 0, "duration_s": 2}]}


def test_unsupported_claim_and_number_rejected(conn, setup):
    topic, fact, asset = setup
    assert evidence.validate_segments(conn, topic, [{"text": "It has 99 ears.",
            "fact_ids": [fact]}]) == ["segment 0: unsupported number 99"]
    with pytest.raises(ValueError, match="invalid or stale"):
        creative.save_plan(conn, plan(topic, "invented", asset))


def test_spelled_out_magnitude_is_not_exempt_from_fact_gate(conn, setup):
    topic, fact, asset = setup
    with pytest.raises(ValueError, match="unsupported quantity"):
        creative.save_plan(conn, plan(topic, fact, asset, "It has millions of ears."))


def test_missing_or_expired_rights(conn, setup):
    topic, fact, asset = setup
    conn.execute("UPDATE assets SET evidence='' WHERE id=?", (asset,))
    assert "missing rights evidence" in media.rights_errors(
        conn.execute("SELECT * FROM assets WHERE id=?", (asset,)).fetchone())
    conn.execute("UPDATE assets SET evidence='evidence',expires_at=? WHERE id=?",
                 ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), asset))
    with pytest.raises(ValueError, match="expired"):
        creative.save_plan(conn, plan(topic, fact, asset))


def test_source_revision_and_approval_invalidation(conn, setup):
    topic, fact, asset = setup
    first = creative.save_plan(conn, plan(topic, fact, asset))
    conn.execute("UPDATE plans SET status='APPROVED',approved_hash='fake' WHERE id=?", (first,))
    second_fact = evidence.add_fact(conn, topic, "The fixture has 3 ears.", "Test collection",
                                    "local:new-study", "3 ears", "2026-01-02", supersedes=fact)
    assert conn.execute("SELECT approved_hash FROM plans WHERE id=?", (first,)).fetchone()[0] is None
    evidence.approve_fact(conn, second_fact)
    assert evidence.validate_segments(conn, topic, plan(topic, fact, asset)["segments"])


def test_corrupt_footage_and_render_failure(conn, tmp_path):
    fake = tmp_path / "bad.mp4"
    fake.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="corrupt"):
        media.import_asset(conn, fake, source_ref="local", creator="test", license_type="original",
                           evidence="receipt", attribution="", species="test",
                           commercial=True, modifications=True)
    with pytest.raises(ValueError, match="unknown plan"):
        editor.compose(conn, "missing")


def test_no_approval_without_qc_and_no_unauthorized_export(conn, setup):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    with pytest.raises(ValueError, match="exact rendered"):
        review.approve(conn, identifier, "wrong")
    with pytest.raises(ValueError, match="approved"):
        review.export(conn, identifier, "youtube", dry_run=True)


def test_local_export_includes_thumbnail_and_exact_revision(conn, setup, tmp_path, monkeypatch):
    from animal_pipeline.core import digest
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    video = Path(conn.execute("SELECT path FROM assets WHERE id=?", (asset,)).fetchone()[0])
    video_hash = digest(video)
    conn.execute("UPDATE plans SET status='APPROVED',video_path=?,video_hash=?,approved_hash=? "
                 "WHERE id=?", (str(video), video_hash, video_hash, identifier))
    monkeypatch.setattr(review, "ROOT", tmp_path)
    monkeypatch.setattr(review, "review_errors", lambda connection, row: [])
    result = review.export(conn, identifier, "youtube")
    folder = Path(result["directory"])
    assert (folder / "thumbnail.jpg").is_file()
    assert digest(folder / "Naminal_Hub.mp4") == video_hash


def test_queue_recovery_and_idempotence(conn, setup):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    assert scheduler.enqueue(conn, "render", identifier)
    assert not scheduler.enqueue(conn, "render", identifier)
    conn.execute("UPDATE jobs SET status='running'")
    assert scheduler.recover(conn) == 1
    assert scheduler.recover(conn) == 0


def test_fresh_running_claim_survives_but_stale_claim_recovers(conn, setup):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    scheduler.enqueue(conn, "render", identifier)
    job = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.execute("UPDATE jobs SET status='running',claimed_at=? WHERE id=?",
                 (datetime.now(timezone.utc).isoformat(), job))
    assert scheduler.recover(conn) == 0
    stale = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    conn.execute("UPDATE jobs SET claimed_at=? WHERE id=?", (stale, job))
    assert scheduler.recover(conn) == 1
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "queued"


def test_recovery_is_bounded_by_attempts(conn, setup):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    scheduler.enqueue(conn, "render", identifier)
    job = conn.execute("SELECT id FROM jobs").fetchone()[0]
    stale = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    conn.execute("UPDATE jobs SET status='running',attempts=3,claimed_at=? WHERE id=?",
                 (stale, job))
    scheduler.recover(conn)
    row = conn.execute("SELECT status,error FROM jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "failed" and "exhausted" in row["error"]
    conn.execute("UPDATE jobs SET status='failed',attempts=1 WHERE id=?", (job,))
    assert scheduler.recover(conn) == 1
    conn.execute("UPDATE jobs SET status='failed',attempts=3 WHERE id=?", (job,))
    assert scheduler.recover(conn) == 0


def test_drain_records_claim_and_bounds_the_batch(conn, setup):
    topic, fact, asset = setup
    first = creative.save_plan(conn, plan(topic, fact, asset))
    second = creative.save_plan(conn, plan(topic, fact, asset))
    scheduler.enqueue(conn, "render", first)
    scheduler.enqueue(conn, "render", second)
    results = asyncio.run(scheduler.drain(conn, lambda c, target: {"ok": target}, limit=1))
    assert len(results) == 1 and results[0]["result"] == {"ok": first}
    done = conn.execute("SELECT status,claimed_at FROM jobs WHERE target_id=?", (first,)).fetchone()
    assert done["status"] == "done" and done["claimed_at"]
    assert conn.execute("SELECT status FROM jobs WHERE target_id=?",
                        (second,)).fetchone()[0] == "queued"


def test_queue_rejects_unknown_render_target(conn, setup):
    with pytest.raises(ValueError, match="unknown render target"):
        scheduler.enqueue(conn, "render", "plan_missing")


def test_draft_ready_is_bounded_idempotent_and_fail_closed(conn, setup, monkeypatch):
    topic, fact, asset = setup
    monkeypatch.setattr(creative, "generate_plan",
                        lambda c, t: plan(t, fact, asset))
    monkeypatch.setattr(creative, "footage_budget",
                        lambda c, t, **kw: {"needs_media": False, "profile": "EXTENDED",
                                            "reason": "extended profile fits", "target_s": 65,
                                            "total_usable_s": 65,
                                            "limits": {"max_shot_s": 30, "max_total_s": 65}})
    result = creative.draft_ready(conn, limit=2)
    assert result["drafted"] and not result["errors"]
    assert conn.execute("SELECT status FROM plans").fetchone()[0] == "SCRIPT_READY"
    assert creative.draft_ready(conn, limit=2)["drafted"] == []
    second = evidence.add_topic(conn, "Second animal", "Species two", "Tiny but Extraordinary")
    second_fact = evidence.add_fact(conn, second, "It exists.", "Org", "local:s",
                                    "It exists.", "2026-01-01")
    evidence.approve_fact(conn, second_fact)
    footage_path = Path(conn.execute("SELECT path FROM assets WHERE id=?",
                                     (asset,)).fetchone()[0])
    second_asset = media.import_asset(conn, footage_path, source_ref="local:fixture2",
                                      creator="Test operator", license_type="original",
                                      evidence="fixture", attribution="Test operator",
                                      species="Second animal", commercial=True,
                                      modifications=True)
    media.approve_asset(conn, second_asset)
    monkeypatch.setattr(creative, "generate_plan",
                        lambda c, t: plan(t, "invented", asset))
    rejected = creative.draft_ready(conn, limit=2)
    assert not rejected["drafted"] and second in rejected["errors"]
    assert conn.execute("SELECT COUNT(*) FROM plans WHERE topic_id=?",
                        (second,)).fetchone()[0] == 0


def test_draft_ready_stops_when_planner_unavailable(conn, setup, monkeypatch):
    topic, fact, asset = setup
    monkeypatch.setattr(creative, "footage_budget",
                        lambda c, t, **kw: {"needs_media": False, "profile": "EXTENDED",
                                            "reason": "extended profile fits", "target_s": 65,
                                            "total_usable_s": 65,
                                            "limits": {"max_shot_s": 30, "max_total_s": 65}})
    def unavailable(c, t):
        raise OSError("connection refused")
    monkeypatch.setattr(creative, "generate_plan", unavailable)
    result = creative.draft_ready(conn, limit=2)
    assert not result["drafted"] and "llm" in result["errors"]


def test_rank_prefers_approved_visuals(conn, setup):
    topic, fact, asset = setup
    second = evidence.add_topic(conn, "No footage", "None", "Tiny but Extraordinary")
    assert creative.rank_topics(conn)[0]["id"] == topic


def test_observed_performance_changes_topic_ranking(conn, setup):
    topic, fact, asset = setup
    second = evidence.add_topic(conn, "Second animal", "Species two", "Tiny but Extraordinary")
    second_fact = evidence.add_fact(conn, second, "It exists.", "Org", "local:s",
                                    "It exists.", "2026-01-01")
    evidence.approve_fact(conn, second_fact)
    footage_path = Path(conn.execute("SELECT path FROM assets WHERE id=?",
                                     (asset,)).fetchone()[0])
    second_asset = media.import_asset(conn, footage_path, source_ref="local:fixture2",
                                      creator="Test operator", license_type="original",
                                      evidence="fixture", attribution="Test operator",
                                      species="Second animal", commercial=True,
                                      modifications=True)
    media.approve_asset(conn, second_asset)
    plan_id = creative.save_plan(conn, plan(second, second_fact, second_asset, "It exists."))
    conn.execute("INSERT INTO analytics(id,video_id,platform,observed_at,published_at,"
                 "views,likes,watch_seconds,completion,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("s1", plan_id, "youtube", "2026-01-05T00:00:00+00:00",
                  "2026-01-03T00:00:00+00:00", 1000, None, None, None, "operator import"))
    ranking = creative.rank_topics(conn)
    assert ranking[0]["id"] == second
    assert ranking[0]["reasons"]["analytics"]["median_views_per_day"] == 500
    conn.execute("INSERT INTO analytics(id,video_id,platform,observed_at,published_at,"
                 "views,likes,watch_seconds,completion,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("s2", plan_id, "youtube", "2026-01-20T00:00:00+00:00",
                  "2026-01-03T00:00:00+00:00", 0, None, None, None, "operator import"))
    assert creative.rank_topics(conn)[0]["id"] == topic


def test_ranking_is_versioned_explainable_and_capped(conn, setup):
    topic, fact, asset = setup
    ranked = creative.rank_topics(conn)
    top = next(r for r in ranked if r["id"] == topic)
    assert top["reasons"]["formula"] == "v2"
    contributions = top["reasons"]["contributions"]
    assert top["score"] == pytest.approx(sum(contributions.values()), abs=0.01)
    assert top["reasons"]["signals"]["evidence"]["basis"] == "observed"
    assert top["reasons"]["signals"]["cost"]["basis"] == "predicted"


def test_viral_outlier_cannot_drown_exploration(conn, setup, tmp_path):
    topic, fact, asset = setup
    second = evidence.add_topic(conn, "Viral animal", "Species viral", "New pillar")
    second_fact = evidence.add_fact(conn, second, "It is viral.", "Org", "local:v",
                                    "It is viral.", "2026-01-01")
    evidence.approve_fact(conn, second_fact)
    footage_path = Path(conn.execute("SELECT path FROM assets WHERE id=?",
                                     (asset,)).fetchone()[0])
    second_asset = media.import_asset(conn, footage_path, source_ref="local:v",
                                      creator="T", license_type="original",
                                      evidence="fixture", attribution="T",
                                      species="Viral animal", commercial=True,
                                      modifications=True)
    media.approve_asset(conn, second_asset)
    plan_id = creative.save_plan(conn, plan(second, second_fact, second_asset, "It is viral."))
    conn.execute("INSERT INTO analytics(id,video_id,platform,observed_at,published_at,"
                 "views,source) VALUES('s',?,'youtube','2026-01-10',"
                 "'2026-01-01',900000,'operator import')", (plan_id,))
    ranked = creative.rank_topics(conn)
    boost = ranked[0]["reasons"]["contributions"]["own_performance"]
    assert boost <= 8.0  # log-capped; a viral hit cannot exceed the weight
    assert ranked[0]["reasons"]["signals"]["own_performance"]["value"] <= 500


def test_unsaturated_pillar_gets_exploration_signal(conn, setup):
    topic, fact, asset = setup
    ranked = creative.rank_topics(conn)
    novelty = next(r for r in ranked if r["id"] == topic)["reasons"]["signals"]["novelty"]
    assert novelty["value"] == 1 and novelty["basis"] == "heuristic"


def test_analytics_import_age_normalization(conn, tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps([{"video_id": "v1", "platform": "youtube",
        "published_at": "2026-01-01T00:00:00+00:00",
        "observed_at": "2026-01-03T00:00:00+00:00", "views": 100,
        "source": "operator import"}]))
    assert analytics.import_snapshots(conn, path) == 1
    assert analytics.import_snapshots(conn, path) == 0
    assert analytics.report(conn)[0]["views_per_day"] == 50


def test_competitor_metadata_import_no_media_download(conn, tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps([{"channel": "@example", "video_id": "vid1",
        "title": "Fixture title", "category": "biology",
        "uploaded_at": "2026-01-01T00:00:00+00:00",
        "observed_at": "2026-01-03T00:00:00+00:00",
        "duration_s": 65, "views": 200, "source": "operator CSV export"}]))
    assert competitors.import_metadata(conn, path) == 1
    assert competitors.report(conn)[0]["views_per_day"] == 100


def test_low_disk_blocks_jobs(conn, setup, monkeypatch):
    from animal_pipeline import scheduler as scheduling
    monkeypatch.setattr(scheduling.shutil, "disk_usage",
                        lambda path: type("Usage", (), {"free": 0})())
    assert scheduling.resource_errors() == ["low disk"] or "low disk" in scheduling.resource_errors()


def test_wrong_duration_and_narration_block(conn, setup, monkeypatch):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    with pytest.raises(ValueError, match="duration"):
        editor.compose(conn, identifier)
    with pytest.raises(ValueError, match="approved voice"):
        editor.compose(conn, identifier, Path("/fake/audio.wav"))


def test_footage_first_duration_requires_explicit_editorial_reason(conn, setup):
    topic, fact, asset = setup
    candidate = plan(topic, fact, asset)
    with pytest.raises(ValueError, match="reason"):
        creative.save_plan(conn, candidate, footage_first=True)
    identifier = creative.save_plan(conn, candidate, footage_first=True,
                                    reason="Use the available distinct footage, not padding")
    assert json.loads(conn.execute("SELECT content FROM plans WHERE id=?", (identifier,)
                           ).fetchone()[0])["runtime_exception"]
    with pytest.raises(ValueError, match="explicit"):
        creative.save_plan(conn, {"runtime_exception": "silently bypass", **candidate})


def test_new_plan_supersedes_previous_review(conn, setup):
    topic, fact, asset = setup
    first = creative.save_plan(conn, plan(topic, fact, asset))
    conn.execute("UPDATE plans SET status='APPROVED',approved_hash='fake' WHERE id=?", (first,))
    creative.revise_plan(conn, first, plan(topic, fact, asset))
    row = conn.execute("SELECT status,approved_hash FROM plans WHERE id=?", (first,)).fetchone()
    assert (row["status"], row["approved_hash"]) == ("CHANGES_REQUESTED", None)


def test_voice_failure_is_strict(monkeypatch, tmp_path):
    from animal_pipeline import audio
    monkeypatch.setattr(audio, "settings", lambda: {"voice": {"backend": "kokoro_mlx",
        "approved": False, "name": ""}})
    with pytest.raises(RuntimeError, match="approved Kokoro"):
        audio.synthesize("hello", tmp_path / "narration.wav")


class _DeadWorker:
    def synthesize(self, *args):
        raise RuntimeError("worker unavailable")


def _stub_worker_command(*, dies_after_ready: bool = False):
    import textwrap
    script = textwrap.dedent("""
        import json, sys, wave
        sys.stdout.write(json.dumps({"ready": True}) + "\\n")
        sys.stdout.flush()
    """)
    if dies_after_ready:
        script += "sys.exit(1)\n"
    else:
        script += textwrap.dedent("""
            for line in sys.stdin:
                req = json.loads(line)
                out = req["file_prefix"] + "_000.wav"
                with wave.open(out, "wb") as w:
                    w.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    w.writeframes(b"\\0\\0" * 2400)
                sys.stdout.write(json.dumps({"ok": True, "files": [out]}) + "\\n")
                sys.stdout.flush()
        """)
    return [sys.executable, "-c", script]


def test_resident_worker_reuses_one_process(monkeypatch, tmp_path):
    from animal_pipeline import audio
    worker = audio._KokoroWorker("model", command=_stub_worker_command())
    try:
        first = worker.synthesize("hello", "af_bella", tmp_path / "a")
        second = worker.synthesize("world", "af_bella", tmp_path / "b")
        assert first and second and worker._proc.poll() is None
    finally:
        worker._kill()


def test_dead_worker_respawns_once_then_raises(monkeypatch, tmp_path):
    from animal_pipeline import audio
    worker = audio._KokoroWorker("model",
                                 command=_stub_worker_command(dies_after_ready=True))
    try:
        with pytest.raises(RuntimeError):
            worker.synthesize("hello", "af_bella", tmp_path / "a")
    finally:
        worker._kill()


def test_worker_failure_falls_back_to_cold_cli(monkeypatch, tmp_path):
    from animal_pipeline import audio
    monkeypatch.setattr(audio, "settings", lambda: {"voice": {
        "backend": "kokoro_mlx", "approved": True, "name": "af_bella", "model": "local"}})
    monkeypatch.setattr(audio, "_worker_for", lambda model: _DeadWorker())
    original_run = subprocess.run
    def run(cmd, **kwargs):
        if cmd[:3] == [sys.executable, "-m", "mlx_audio.tts.generate"]:
            prefix = Path(cmd[cmd.index("--file_prefix") + 1])
            with wave.open(str(prefix.parent / f"{prefix.name}_000.wav"), "wb") as out:
                out.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                out.writeframes(b"\0\0" * 24000)
            return subprocess.CompletedProcess(cmd, 0)
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(audio.subprocess, "run", run)
    result = audio.synthesize("narration", tmp_path / "n.wav")
    with wave.open(str(result)) as produced:
        assert produced.getnframes() == 24000


def test_kokoro_multi_part_narration_is_joined(monkeypatch, tmp_path):
    from animal_pipeline import audio
    monkeypatch.setattr(audio, "settings", lambda: {"voice": {
        "backend": "kokoro_mlx", "approved": True, "name": "af_bella", "model": "local"}})
    monkeypatch.setattr(audio, "_worker_for", lambda model: _DeadWorker())
    original_run = subprocess.run
    def run(cmd, **kwargs):
        if cmd[:3] == [sys.executable, "-m", "mlx_audio.tts.generate"]:
            prefix = Path(cmd[cmd.index("--file_prefix") + 1])
            for i in range(2):
                with wave.open(str(prefix.parent / f"{prefix.name}_{i:03d}.wav"), "wb") as out:
                    out.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    out.writeframes(b"\0\0" * 24000)
            return subprocess.CompletedProcess(cmd, 0)
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(audio.subprocess, "run", run)
    result = audio.synthesize("long narration", tmp_path / "narration.wav")
    with wave.open(str(result)) as combined:
        assert combined.getnframes() == 48000


def test_failed_alignment_blocks_render(monkeypatch, tmp_path):
    from animal_pipeline import audio
    monkeypatch.setattr(audio, "settings", lambda: {"alignment": {"model_path": "missing.bin"}})
    with pytest.raises(RuntimeError, match="whisper.cpp"):
        audio.align(tmp_path / "audio.wav", "Script", tmp_path / "out")


def test_whisper_caption_times_are_clamped_to_audio(tmp_path, monkeypatch):
    from animal_pipeline import audio
    source = tmp_path / "short.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    str(source)], check=True, timeout=60)
    model = tmp_path / "model.bin"
    model.touch()
    monkeypatch.setattr(audio, "settings", lambda: {"alignment": {"model_path": str(model)}})
    original_run = subprocess.run
    def run(cmd, **kwargs):
        if cmd[0] == "whisper-cli":
            Path(cmd[cmd.index("-of") + 1] + ".json").write_text(
                json.dumps({"transcription": [{"offsets": {"from": 0, "to": 30000},
                                                 "text": "Three seconds of audio"}]}))
            return subprocess.CompletedProcess(cmd, 0)
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(audio.subprocess, "run", run)
    captions = audio.align(source, "Three seconds of audio", tmp_path / "timing")
    assert captions[-1]["end"] == 3
    assert all(len(c["text"]) <= 42 for c in captions)


def test_excessive_tail_silence_fails_audio_gate():
    assert not editor.audio_timing_ok(46, 37.875)
    assert editor.audio_timing_ok(40, 37.875)


def test_loudness_measurement_and_bounded_gain(tmp_path):
    loud = tmp_path / "loud.wav"
    quiet = tmp_path / "quiet.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(loud)],
                   check=True, timeout=60)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3,volume=0.02",
                    str(quiet)], check=True, timeout=60)
    measured = editor.loudness_lufs(loud)
    assert -40 < measured < 0
    gain = editor.narration_gain(loud)
    normalized = tmp_path / "normalized.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(loud),
                    "-af", f"volume={gain:.2f}dB,alimiter=limit=0.891:level=false",
                    str(normalized)], check=True, timeout=60)
    assert abs(editor.loudness_lufs(normalized) - (-14)) <= 1.5
    with pytest.raises(RuntimeError, match="unsafe"):
        editor.narration_gain(quiet)


def test_rights_expiration_after_render_blocks_review(conn, setup, monkeypatch):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    conn.execute("UPDATE plans SET status='READY_FOR_REVIEW',qc='{\"ok\":true}',"
                 "video_path=?,video_hash=? WHERE id=?",
                 (str(Path(conn.execute("SELECT path FROM assets WHERE id=?", (asset,)).fetchone()[0])),
                  conn.execute("SELECT sha256 FROM assets WHERE id=?", (asset,)).fetchone()[0], identifier))
    conn.execute("UPDATE assets SET expires_at=? WHERE id=?",
                 ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), asset))
    assert "expired or ambiguous rights expiry" in review.review_errors(
        conn, conn.execute("SELECT * FROM plans WHERE id=?", (identifier,)).fetchone())


def test_inspect_asset_creates_real_visual_previews(conn, setup, tmp_path):
    _, _, asset = setup
    result = media.inspect_asset(conn, asset, tmp_path / "previews")
    assert Path(result["vertical_preview"]).is_file()
    assert media.probe(Path(result["playable_preview"]))["codec"] == "h264"
    assert len(result["visual_fingerprint"]) == 64


def test_theora_with_audio_gets_playable_preview(conn, tmp_path):
    source = tmp_path / "theora.ogv"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libtheora", "-c:a", "libvorbis", str(source)],
                   check=True, timeout=60)
    asset = media.import_asset(conn, source, source_ref="local:fixture",
                               creator="Test", license_type="original",
                               evidence="generated in test", attribution="",
                               species="Synthetic pattern", commercial=True, modifications=True)
    preview = Path(media.inspect_asset(conn, asset, tmp_path / "previews")["playable_preview"])
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_name", "-of", "json", str(preview)],
                             capture_output=True, text=True, check=True).stdout
    assert {stream["codec_name"] for stream in json.loads(streams)["streams"]} == {"h264", "aac"}
    assert media.probe(source)["codec"] == "theora"


def test_job_retry_bounded(conn, setup):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    scheduler.enqueue(conn, "render", identifier)
    job = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.execute("UPDATE jobs SET status='failed',attempts=2 WHERE id=?", (job,))
    assert scheduler.retry(conn, job)
    conn.execute("UPDATE jobs SET status='failed',attempts=3 WHERE id=?", (job,))
    assert not scheduler.retry(conn, job)


def test_real_mp4_probe_and_synthetic_review_block(footage, tmp_path, monkeypatch):
    from animal_pipeline import core
    render_settings = core.settings()
    monkeypatch.setattr(core, "ROOT", tmp_path)
    from animal_pipeline import editor as editing
    monkeypatch.setattr(editing, "ROOT", tmp_path)
    monkeypatch.setattr(editing, "settings", lambda: render_settings)
    monkeypatch.setattr(review, "settings", lambda: render_settings)
    monkeypatch.setattr(budget, "settings", lambda: render_settings)
    monkeypatch.setattr(budget, "content", lambda: {})
    db = connect(tmp_path / "test.db")
    topic = evidence.add_topic(db, "Synthetic pattern", "Fixture", "Test")
    fact = evidence.add_fact(db, topic, "This is a test.", "Fixtures", "local",
                             "This is a test.", "2026-01-01")
    evidence.approve_fact(db, fact)
    asset = media.import_asset(db, footage, source_ref="local", creator="Fixtures",
                               license_type="original", evidence="generated locally",
                               attribution="", species="Synthetic pattern", commercial=True,
                               modifications=True, test_only=True)
    media.approve_asset(db, asset)
    identifier = creative.save_plan(db, plan(topic, fact, asset, "This is a test."), simulation=True)
    audio = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(audio)], check=True, timeout=60)
    result = editing.compose(db, identifier, audio, simulation=True)
    assert media.probe(Path(result["video"]))["height"] == 1920
    assert result["qc"]["ok"] is False
    with pytest.raises(ValueError, match="exact rendered|QC"):
        review.approve(db, identifier, result["manifest"]["video_hash"])
    db.close()


def test_voice_identity_change_flags_but_new_keys_do_not(conn, setup, monkeypatch):
    from animal_pipeline.core import settings as live_settings
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    voice = live_settings()["voice"]
    asset_path = conn.execute("SELECT path FROM assets WHERE id=?", (asset,)).fetchone()[0]
    asset_hash = conn.execute("SELECT sha256 FROM assets WHERE id=?", (asset,)).fetchone()[0]
    qc = json.dumps({"ok": True, "voice": {"backend": voice["backend"],
                                           "name": voice["name"], "model": voice["model"]}})
    conn.execute("UPDATE plans SET status='READY_FOR_REVIEW',qc=?,video_path=?,video_hash=? "
                 "WHERE id=?", (qc, asset_path, asset_hash, identifier))
    errors = review.review_errors(
        conn, conn.execute("SELECT * FROM plans WHERE id=?", (identifier,)).fetchone())
    assert "approved voice configuration changed" not in errors
    bad_qc = json.dumps({"ok": True, "voice": {"backend": voice["backend"],
                                               "name": "af_sky", "model": voice["model"]}})
    conn.execute("UPDATE plans SET qc=? WHERE id=?", (bad_qc, identifier))
    errors = review.review_errors(
        conn, conn.execute("SELECT * FROM plans WHERE id=?", (identifier,)).fetchone())
    assert "approved voice configuration changed" in errors


def test_review_ui_escapes_and_shows_gates(conn, setup):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset, "It has <b>2</b> ears."))
    html = server.queue_page(conn)
    assert identifier in html and "SCRIPT_READY" in html
    page = server.review_page(conn, identifier)
    assert "&lt;b&gt;2&lt;/b&gt;" in page and "<b>2</b>" not in page
    assert "exact rendered video SHA-256" not in page
    assert "mandatory QC did not pass" in page
    assert "Unknown plan" in server.review_page(conn, "nope")


def test_launchd_plist_is_deterministic_and_bounded(tmp_path):
    from animal_pipeline import service
    content = service.plist(Path("/opt/naminal"), interval_s=3600, limit=2)
    assert "ai.naminal.desk" in content and "<integer>3600</integer>" in content
    assert "<string>/opt/naminal</string>" in content
    assert "--limit" in content and "<string>2</string>" in content
    written = service.install(tmp_path, tmp_path / "agents", install_launchctl=False)
    assert (tmp_path / "agents" / "ai.naminal.desk.plist").is_file()
    assert (tmp_path / "output" / "logs").is_dir()
    assert written["bootstrapped"] is False


def test_review_page_prefills_commands_only_when_unblocked(conn, setup, monkeypatch):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    page = server.review_page(conn, identifier)
    assert "python main.py approve" not in page
    monkeypatch.setattr(server, "review_errors", lambda c, r: [])
    conn.execute("UPDATE plans SET status='READY_FOR_REVIEW',video_hash='abc123' WHERE id=?",
                 (identifier,))
    page = server.review_page(conn, identifier)
    assert f"python main.py approve {identifier} abc123" in page
    assert "--verify-facts --verify-visuals --verify-rights --verify-audio" in page
    conn.execute("UPDATE plans SET status='APPROVED' WHERE id=?", (identifier,))
    page = server.review_page(conn, identifier)
    assert "--platform youtube" in page and "--platform instagram" in page


def test_triage_page_lists_pending_verification_commands(conn, setup):
    topic, fact, asset = setup
    pending_fact = evidence.add_fact(conn, topic, "Pending claim.", "Org", "local:x",
                                     "Pending claim.", "2026-01-02")
    pending_asset = media.import_asset(conn, Path(conn.execute(
        "SELECT path FROM assets WHERE id=?", (asset,)).fetchone()[0]),
        source_ref="local:unverified", creator="T", license_type="original",
        evidence="fixture", attribution="T", species="Fixture animal",
        commercial=True, modifications=True)
    page = server.triage_page(conn)
    assert f"verify-fact {pending_fact}" in page
    assert f"verify-rights {pending_asset}" in page
    assert "Pending claim." in page
    assert fact not in page  # approved facts need no action


def test_service_pause_resume_and_health(tmp_path, monkeypatch):
    from animal_pipeline import service
    calls = []
    class Proc:
        def __init__(self, stdout="", rc=0):
            self.stdout, self.returncode = stdout, rc
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "print-disabled" in cmd:
            return Proc("disabled\n" if service._paused_marker(tmp_path).is_file()
                        else "enabled\n")
        return Proc()
    monkeypatch.setattr(service.subprocess, "run", fake_run)
    monkeypatch.setattr(service, "AGENTS", tmp_path / "agents")
    assert service.pause(tmp_path)["paused"] is True
    assert service._paused_marker(tmp_path).is_file()
    assert "disable" in calls[-1]
    assert service.resume(tmp_path)["paused"] is False
    assert "enable" in calls[-1]
    health = service.health(tmp_path)
    assert health["paused"] is False and "log_dir" in health


def test_drain_circuit_breaker_stops_consecutive_failures(conn, setup, monkeypatch):
    topic, fact, asset = setup
    ids = [creative.save_plan(conn, plan(topic, fact, asset)) for _ in range(3)]
    for plan_id in ids:
        scheduler.enqueue(conn, "render", plan_id)
    def broken(c, target):
        raise RuntimeError("renderer down")
    results = asyncio.run(scheduler.drain(conn, broken, limit=3))
    assert len(results) == 3  # attempts bound also breaks
    assert all("error" in r for r in results)


def test_ollama_unavailable_marks_waiting_for_model(conn, setup, monkeypatch):
    from animal_pipeline import cli
    topic, fact, asset = setup
    monkeypatch.setattr(cli, "_ollama_up", lambda cfg: False)
    monkeypatch.setattr(cli, "connect", lambda *a, **kw: conn)
    summary = cli.dispatch(argparse_for("run"))
    assert summary["draft_stage"] == "WAITING_FOR_MODEL"
    assert not summary["drafted"]


def argparse_for(*argv):
    import argparse
    parser = __import__("animal_pipeline.cli", fromlist=["cli"]).parser()
    return parser.parse_args(list(argv))


def test_dashboard_and_report_pages_render_pipeline_state(conn, setup, tmp_path):
    topic, fact, asset = setup
    identifier = creative.save_plan(conn, plan(topic, fact, asset))
    page = server.dashboard_page(conn)
    assert "Operator dashboard" in page and "Fixture animal" in page
    assert "MICRO" in page or "NEEDS_MEDIA" in page or "usable" in page
    assert "Drafts awaiting attention" in page and identifier in page
    report = server.report_page(conn)
    assert "Weekly optimization report" in report and "not causal" in report
    assert "Recommended experiments" in report


def test_review_ui_serves_over_http(conn, setup, tmp_path):
    import threading
    import urllib.request
    topic, fact, asset = setup
    creative.save_plan(conn, plan(topic, fact, asset))
    conn.close()
    db = tmp_path / "ui.db"
    db_conn = connect(db)
    db_conn.close()
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(db))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        assert "Review queue" in urllib.request.urlopen(url, timeout=5).read().decode()
    finally:
        httpd.shutdown()


def clip(tmp_path, name, seconds):
    path = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=s=320x240:r=30:d={seconds}",
                    "-c:v", "h264_videotoolbox", "-b:v", "1M", str(path)],
                   check=True, timeout=120)
    return path


def approved_clip(conn, path, species, behavior=""):
    asset = media.import_asset(conn, path, source_ref="local:clip", creator="Op",
                               license_type="original", evidence="fixture",
                               attribution="Op", species=species, behavior=behavior,
                               commercial=True, modifications=True)
    media.approve_asset(conn, asset)
    return asset


def test_footage_budget_contract_reports_assets_limits_and_review(conn, tmp_path):
    topic = evidence.add_topic(conn, "Budget animal", "Species budget", "Tiny but Extraordinary")
    asset = approved_clip(conn, clip(tmp_path, "a.mp4", 17), "Budget animal",
                          behavior="display behaviour")
    result = budget.footage_budget(conn, topic)
    assert result["total_usable_s"] == pytest.approx(17, abs=0.5)
    entry = result["assets"][0]
    assert entry["asset_id"] == asset and entry["species_match"] == "exact"
    assert entry["in_s"] == 0 and entry["out_s"] == pytest.approx(17, abs=0.5)
    assert entry["behavior"] == "display behaviour" and entry["rights_ok"]
    assert result["limits"]["max_shot_s"] > 0
    assert result["limits"]["max_total_s"] == pytest.approx(17, abs=0.5)
    assert result["narration"]["estimated_max_words"] > 0
    assert result["visual_supplements"] == []
    assert result["required_review"]


def test_derived_species_footage_is_one_directional(conn, tmp_path):
    generic = evidence.add_topic(conn, "Mantis shrimp", "Stomatopoda", "Tiny but Extraordinary")
    specific = evidence.add_topic(conn, "Peacock mantis shrimp", "Odontodactylus scyllarus",
                                  "Tiny but Extraordinary")
    approved_clip(conn, clip(tmp_path, "peacock.mp4", 10), "Peacock mantis shrimp")
    approved_clip(conn, clip(tmp_path, "generic.mp4", 14), "Mantis shrimp")
    generic_budget = budget.footage_budget(conn, generic)
    matches = {a["asset_id"]: a["species_match"] for a in generic_budget["assets"]}
    assert set(matches.values()) == {"exact", "derived"}
    assert generic_budget["total_usable_s"] == pytest.approx(24, abs=0.5)
    specific_budget = budget.footage_budget(conn, specific)
    assert specific_budget["total_usable_s"] == pytest.approx(10, abs=0.5)
    assert all(a["species_match"] == "exact" for a in specific_budget["assets"])


def test_ineligible_assets_excluded_with_reasons(conn, tmp_path):
    topic = evidence.add_topic(conn, "Budget animal", "Species budget", "Tiny but Extraordinary")
    approved_clip(conn, clip(tmp_path, "good.mp4", 16), "Budget animal")
    unapproved = media.import_asset(conn, clip(tmp_path, "raw.mp4", 40),
                                    source_ref="local:x", creator="T", license_type="original",
                                    evidence="fixture", attribution="T", species="Budget animal",
                                    commercial=True, modifications=True)
    result = budget.footage_budget(conn, topic)
    assert result["total_usable_s"] == pytest.approx(16, abs=0.5)
    assert unapproved in {a["asset_id"] for a in result["excluded"]}
    assert all(a["reasons"] for a in result["excluded"])


def test_select_profile_bounds_and_needs_media(conn, tmp_path):
    topic = evidence.add_topic(conn, "Budget animal", "Species budget", "Tiny but Extraordinary")
    assert budget.select_profile({"total_usable_s": 10})["profile"] == "NEEDS_MEDIA"
    assert budget.select_profile({"total_usable_s": 18})["profile"] == "MICRO"
    assert budget.select_profile({"total_usable_s": 45})["profile"] == "STANDARD"
    extended = budget.select_profile({"total_usable_s": 80})
    assert extended["profile"] == "EXTENDED" and extended["target_s"] <= 70
    assert all(sel["reason"] for sel in
               (extended, budget.select_profile({"total_usable_s": 18})))


def test_save_plan_rejects_reused_footage_over_usable_budget(conn, setup):
    topic, fact, asset = setup
    reused = {"topic_id": topic, "title": "Reuse", "hooks": ["a", "b", "c"],
              "segments": [{"text": "2 ears.", "fact_ids": [fact], "asset_id": asset,
                            "start_s": 0, "duration_s": 2},
                           {"text": "2 ears.", "fact_ids": [fact], "asset_id": asset,
                            "start_s": 0, "duration_s": 2.5}]}
    with pytest.raises(ValueError, match="usable footage budget"):
        creative.save_plan(conn, reused)


def test_generate_plan_prompt_carries_duration_budget(conn, setup, monkeypatch):
    topic, fact, asset = setup
    captured = {}
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def read(self):
            return json.dumps({"response": json.dumps(plan(topic, fact, asset))}).encode()
    def fake_open(req, timeout):
        captured["body"] = json.loads(req.data)
        return Response()
    monkeypatch.setattr(creative.urllib.request, "urlopen", fake_open)
    creative.generate_plan(conn, topic)
    prompt = json.loads(captured["body"]["prompt"])
    footage = prompt["footage_budget"]
    assert footage["profile"] == "NEEDS_MEDIA" or footage["target_s"] <= 3.1
    assert footage["limits"]["max_total_s"] == pytest.approx(3, abs=0.3)
    assert footage["assets"][0]["asset_id"] == asset


def test_draft_ready_returns_needs_media_and_records_profile_reason(conn, tmp_path, monkeypatch):
    tiny = evidence.add_topic(conn, "Tiny animal", "Species tiny", "Tiny but Extraordinary")
    tiny_fact = evidence.add_fact(conn, tiny, "It is tiny.", "Org", "local:t",
                                  "It is tiny.", "2026-01-01")
    evidence.approve_fact(conn, tiny_fact)
    approved_clip(conn, clip(tmp_path, "tiny.mp4", 10), "Tiny animal")
    small = evidence.add_topic(conn, "Small animal", "Species small", "Tiny but Extraordinary")
    small_fact = evidence.add_fact(conn, small, "It is small.", "Org", "local:s",
                                   "It is small.", "2026-01-01")
    evidence.approve_fact(conn, small_fact)
    small_asset = approved_clip(conn, clip(tmp_path, "small.mp4", 18), "Small animal")
    def stub(c, topic_id):
        fact, asset = (small_fact, small_asset) if topic_id == small else (tiny_fact, None)
        return {"topic_id": topic_id, "title": "T", "hooks": ["a", "b", "c"],
                "segments": [{"text": "It is small.", "fact_ids": [fact],
                              "asset_id": asset or "", "start_s": 0, "duration_s": 2}]}
    monkeypatch.setattr(creative, "generate_plan", stub)
    result = creative.draft_ready(conn, limit=5)
    assert tiny in result["needs_media"] and "usable" in result["needs_media"][tiny]
    assert result["drafted"]
    stored = json.loads(conn.execute("SELECT content FROM plans WHERE topic_id=?",
                                     (small,)).fetchone()[0])
    assert stored["runtime_exception"] and "MICRO" in stored["runtime_exception"]
    assert stored["footage_profile"]["profile"] == "MICRO"
