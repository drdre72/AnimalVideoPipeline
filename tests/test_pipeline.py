import json
import subprocess
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from animal_pipeline import analytics, competitors, creative, editor, evidence, media, review, scheduler, server
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
    conn.execute("INSERT INTO analytics VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("s1", plan_id, "youtube", "2026-01-05T00:00:00+00:00",
                  "2026-01-03T00:00:00+00:00", 1000, None, None, None, "operator import"))
    ranking = creative.rank_topics(conn)
    assert ranking[0]["id"] == second
    assert ranking[0]["reasons"]["analytics"]["median_views_per_day"] == 500
    conn.execute("INSERT INTO analytics VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("s2", plan_id, "youtube", "2026-01-20T00:00:00+00:00",
                  "2026-01-03T00:00:00+00:00", 0, None, None, None, "operator import"))
    assert creative.rank_topics(conn)[0]["id"] == topic


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


def test_kokoro_multi_part_narration_is_joined(monkeypatch, tmp_path):
    from animal_pipeline import audio
    monkeypatch.setattr(audio, "settings", lambda: {"voice": {
        "backend": "kokoro_mlx", "approved": True, "name": "af_bella", "model": "local"}})
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
