import io
import json
import subprocess

import pytest

from animal_pipeline import commons
from animal_pipeline.core import connect


def _metadata(*, license_name="CC BY 2.0", video_url="https://upload.wikimedia.org/wikipedia/commons/a/aa/Octopus.ogv"):
    return json.dumps({"query": {"pages": {"1": {"imageinfo": [{
        "url": video_url,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Octopus.ogv",
        "size": 10000,
        "extmetadata": {
            "Artist": {"value": "Silke Baron"},
            "LicenseShortName": {"value": license_name},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/2.0"},
            "Restrictions": {"value": ""},
        },
    }]}}}}).encode()


class Response(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url

    def geturl(self):
        return self.url


@pytest.fixture
def footage(tmp_path):
    path = tmp_path / "short.ogv"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=2",
                    "-c:v", "libtheora", str(path)], check=True, timeout=60)
    return path.read_bytes()


def test_commons_import_is_unapproved_idempotent_and_bounded(tmp_path, monkeypatch, footage):
    metadata = _metadata()
    seen = []
    def open_response(url, hosts):
        seen.append(url)
        return Response(metadata if "api.php" in url else footage, url)
    monkeypatch.setattr(commons, "_open", open_response)
    conn = connect(tmp_path / "catalog.db")
    asset_id = commons.import_video(conn, "File:Octopus.ogv", "Day octopus", tmp_path / "cache")
    assert conn.execute("SELECT approved,license_type FROM assets WHERE id=?",
                        (asset_id,)).fetchone()[:] == (0, "CC BY 2.0")
    assert commons.import_video(conn, "File:Octopus.ogv", "Day octopus", tmp_path / "cache") == asset_id
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert all(url.startswith("https://") for url in seen)
    conn.close()


@pytest.mark.parametrize("metadata,expected", [
    (_metadata(license_name="CC BY-NC 4.0"), "license"),
    (_metadata(video_url="http://127.0.0.1/clip.ogv"), "URL"),
])
def test_commons_rejects_restricted_license_and_unsafe_url(tmp_path, monkeypatch, metadata, expected):
    monkeypatch.setattr(commons, "_open",
                        lambda url, hosts: Response(metadata, url))
    conn = connect(tmp_path / "catalog.db")
    with pytest.raises(ValueError, match=expected):
        commons.import_video(conn, "File:Octopus.ogv", "Day octopus", tmp_path / "cache")
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    conn.close()


def test_commons_download_aborts_above_byte_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(commons, "MAX_MEDIA_BYTES", 10)
    def open_response(url, hosts):
        return Response(_metadata() if "api.php" in url else b"X" * 11, url)
    monkeypatch.setattr(commons, "_open", open_response)
    conn = connect(tmp_path / "catalog.db")
    with pytest.raises(ValueError, match="limit"):
        commons.import_video(conn, "File:Octopus.ogv", "Day octopus", tmp_path / "cache")
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("url", [
    "http://upload.wikimedia.org/wikipedia/commons/a/a.ogv",
    "https://upload.wikimedia.org.evil.test/wikipedia/commons/a/a.ogv",
    "https://upload.wikimedia.org:8443/wikipedia/commons/a/a.ogv",
])
def test_commons_url_guard_rejects_downgrades_and_lookalikes(url):
    with pytest.raises(ValueError, match="unsafe"):
        commons._check_url(url, {commons.MEDIA_HOST})


def test_commons_search_lists_video_titles_without_download(monkeypatch):
    payload = json.dumps({"query": {"search": [
        {"title": "File:Octopus.ogv"}, {"title": "File:Octopus.ogv"},
        {"title": "File:Octopus.jpg"}]}}).encode()
    def open_response(url, hosts):
        assert "filetype%3Avideo" in url or "filetype:video" in url
        return Response(payload, url)
    monkeypatch.setattr(commons, "_open", open_response)
    assert commons.search("Octopus cyanea") == ["File:Octopus.ogv"]


def _wiki_extract(text):
    return json.dumps({"query": {"pages": [{
        "title": "Octopus cyanea", "fullurl": "https://en.wikipedia.org/wiki/Octopus_cyanea",
        "extract": text}]}}).encode()


def test_research_proposes_verbatim_candidates_idempotently(tmp_path, monkeypatch):
    from animal_pipeline import evidence, research
    extract = ("The day octopus is active during daylight hours. "
               "It can change colour to match coral and rock surfaces. "
               "Short.")
    monkeypatch.setattr(research, "_open",
                        lambda url, hosts: Response(_wiki_extract(extract), url))
    conn = connect(tmp_path / "facts.db")
    topic = evidence.add_topic(conn, "Day octopus", "Octopus cyanea", "Tiny but Extraordinary")
    ids = research.propose(conn, topic, "Octopus cyanea")
    assert len(ids) == 2
    rows = conn.execute("SELECT statement,excerpt,status,source_ref FROM facts").fetchall()
    assert {r["status"] for r in rows} == {"proposed"}
    assert rows[0]["statement"] == rows[0]["excerpt"]
    assert rows[0]["source_ref"] == "https://en.wikipedia.org/wiki/Octopus_cyanea"
    assert research.propose(conn, topic, "Octopus cyanea") == []
    with pytest.raises(ValueError, match="invalid or stale"):
        from animal_pipeline import creative
        creative.save_plan(conn, {"topic_id": topic, "title": "T", "hooks": ["a", "b", "c"],
            "segments": [{"text": rows[0]["statement"], "fact_ids": [ids[0]],
                          "asset_id": "a", "start_s": 0, "duration_s": 2}]})
    conn.close()


def test_research_rejects_unsafe_title_and_oversized_response(tmp_path, monkeypatch):
    from animal_pipeline import evidence, research
    conn = connect(tmp_path / "facts.db")
    topic = evidence.add_topic(conn, "Day octopus", "Octopus cyanea", "Tiny but Extraordinary")
    with pytest.raises(ValueError, match="title"):
        research.propose(conn, topic, "Octopus cyanea#Section")
    monkeypatch.setattr(research, "MAX_BYTES", 10)
    monkeypatch.setattr(research, "_open",
                        lambda url, hosts: Response(_wiki_extract("Some extract."), url))
    with pytest.raises(ValueError, match="limit"):
        research.propose(conn, topic, "Octopus cyanea")
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    conn.close()


def test_reject_fact_completes_triage(tmp_path):
    from animal_pipeline import evidence
    conn = connect(tmp_path / "facts.db")
    topic = evidence.add_topic(conn, "Day octopus", "Octopus cyanea", "Tiny but Extraordinary")
    fact = evidence.add_fact(conn, topic, "Proposed claim.", "Org", "local:s",
                             "Proposed claim.", "2026-01-01")
    evidence.reject_fact(conn, fact)
    assert conn.execute("SELECT status FROM facts WHERE id=?", (fact,)).fetchone()[0] == "rejected"
    approved = evidence.add_fact(conn, topic, "Approved claim.", "Org", "local:s2",
                                 "Approved claim.", "2026-01-01")
    evidence.approve_fact(conn, approved)
    with pytest.raises(ValueError, match="not proposed"):
        evidence.reject_fact(conn, approved)
    conn.close()
