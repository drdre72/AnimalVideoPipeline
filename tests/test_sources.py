import io
import json
import wave
from pathlib import Path

import pytest

from animal_pipeline import sources
from animal_pipeline.core import connect


def _sine(path: Path, seconds: float = 1.0) -> Path:
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-f", "lavfi", "-i", "color=red:size=64x64:duration=1",
                    "-shortest", str(path)], check=True, timeout=60)
    return path


def test_source_registry_and_errors():
    assert sources.source("noaa").license == sources.PD_USGOV
    with pytest.raises(ValueError, match="unknown source"):
        sources.source("mixkit")
    with pytest.raises(ValueError, match="no search API"):
        sources.search("noaa", "otter")


def test_pick_format_prefers_https_mp4_under_1080():
    info = {"formats": [
        {"url": "http://cdn.example.com/a.mp4", "ext": "mp4", "height": 720, "vcodec": "h264"},
        {"url": "https://cdn.example.com/a480.mp4", "ext": "mp4", "height": 480,
         "vcodec": "h264", "acodec": "aac"},
        {"url": "https://cdn.example.com/a1080.mp4", "ext": "mp4", "height": 1080,
         "vcodec": "h264", "acodec": "aac"},
        {"url": "https://cdn.example.com/master.m3u8", "ext": "mp4", "height": 1080,
         "vcodec": "h264"},
        {"url": "https://cdn.example.com/a4k.mp4", "ext": "mp4", "height": 2160,
         "vcodec": "h264", "acodec": "aac"},
    ]}
    assert sources._pick_format(info).endswith("a1080.mp4")


def test_list_category_stays_on_host(tmp_path, monkeypatch):
    body = (b'<a href="/detail/videos/b-roll:/video/111/a">x</a>'
            b'<a href="https://evil.example.com/video/222/b">y</a>'
            b'<a href="/detail/videos/b-roll:/video/333/c">z</a>')
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(body))
    links = sources.list_category(
        "noaa", "https://videos.fisheries.noaa.gov/category/videos/x")
    assert len(links) == 2
    assert all("videos.fisheries.noaa.gov" in l["url"] for l in links)


def test_gallery_import_records_pd_rights(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    monkeypatch.setattr(sources, "_inspect_gallery",
                        lambda src, url: {"title": "Turtle B-Roll", "url": "https://cdn/x.mp4",
                                          "page": url, "duration": 10.0,
                                          "creator": "NOAA Fisheries"})
    monkeypatch.setattr(sources, "_download", lambda url, dest, **k: clip)
    conn = connect(tmp_path / "t.db")
    aid = sources.import_video(
        conn, "noaa",
        "https://videos.fisheries.noaa.gov/detail/videos/x/video/1/a",
        "Green sea turtle", tmp_path / "cache")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == sources.PD_USGOV
    assert asset["commercial"] == 1 and asset["modifications"] == 1
    assert "NOAA" in asset["attribution"] and not asset["restrictions"]
    evidence = json.loads(asset["evidence"])
    assert evidence["license"].startswith("https://")


def test_pixabay_import_uses_api_key(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    monkeypatch.setattr(sources, "_api_keys", lambda: {"pixabay": "k"})
    payload = json.dumps({"hits": [{"id": 7, "pageURL": "https://pixabay.com/v/7",
                                    "user": "maker", "tags": "otter",
                                    "videos": {"medium": {"url": "https://cdn.pixabay.com/v.mp4"}}}]})
    monkeypatch.setattr(sources.net, "bounded_open", lambda *a, **k: io.BytesIO(payload.encode()))
    monkeypatch.setattr(sources, "_download", lambda url, dest, **k: clip)
    conn = connect(tmp_path / "t.db")
    aid = sources._api_import(conn, sources.source("pixabay"), "7", "Otter", tmp_path / "c")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == "Pixabay Content License"
    assert asset["creator"] == "maker" and "Pixabay" in asset["attribution"]


def test_api_import_requires_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "_api_keys", lambda: {})
    conn = connect(tmp_path / "t.db")
    with pytest.raises(ValueError, match="missing pexels key"):
        sources._api_import(conn, sources.source("pexels"), "1", "Bird", tmp_path / "c")


def test_search_caches_normalized_results_without_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ROOT", tmp_path)
    monkeypatch.setattr(sources, "_api_keys", lambda: {"pixabay": "private-test-key"})
    calls = []

    def respond(url, hosts, **kw):
        calls.append(url)
        return io.BytesIO(json.dumps({"hits": [{"id": 42, "tags": "otter",
                                                "pageURL": "https://pixabay.com/videos/42"}]}).encode())

    monkeypatch.setattr(sources.net, "bounded_open", respond)
    assert sources.search("pixabay", "sea otter", limit=5) == sources.search(
        "pixabay", "sea otter", limit=5)
    assert len(calls) == 1
    cache_files = list((tmp_path / "cache/source_search").glob("*.json"))
    assert len(cache_files) == 1
    assert "private-test-key" not in cache_files[0].read_text()


def test_coverr_search_excludes_ai_and_premium(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ROOT", tmp_path)
    monkeypatch.setattr(sources, "_api_keys", lambda: {"coverr": "private-test-key"})
    items = [{"id": "real", "title": "Otter", "is_ai_generated": False,
              "is_premium": False},
             {"id": "ai", "title": "Generated", "is_ai_generated": True},
             {"id": "unknown", "title": "Unclassified"},
             {"id": "premium", "title": "Paid", "is_ai_generated": False,
              "is_premium": True}]
    monkeypatch.setattr(sources.net, "bounded_request", lambda *a, **k:
                        io.BytesIO(json.dumps({"hits": items}).encode()))
    assert [item["ref"] for item in sources.search("coverr", "otter", limit=4)] == ["real"]


def test_item_license_gate():
    assert sources._allowed_item_license("cc-by-4.0", "")[0] == "CC BY 4.0"
    assert sources._allowed_item_license("CC BY 3.0", "https://x")[0] == "CC BY 3.0"
    assert sources._allowed_item_license("cc-zero", "")[0] == "Public Domain / CC0"
    assert sources._allowed_item_license("", "https://creativecommons.org/publicdomain/mark/1.0/")[0] == "Public Domain / CC0"
    for bad in ("cc-by-nc-4.0", "CC BY-NC-ND", "cc-by-sa-4.0", "", "notspecified",
                "mit-license", "other-open"):
        with pytest.raises(ValueError, match="not cleared"):
            sources._allowed_item_license(bad, "")


def test_zenodo_import_uses_item_license(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    record = {"metadata": {"title": "Beetle fight", "license": {"id": "cc-by-4.0"},
                           "creators": [{"name": "Dr Science"}]},
              "files": [{"key": "fight.mp4", "size": 100,
                         "links": {"self": "https://zenodo.org/api/records/1/files/fight.mp4/content"}},
                        {"key": "data.csv", "size": 50, "links": {}}]}
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(json.dumps(record).encode()))
    monkeypatch.setattr(sources, "_download", lambda url, dest, **k: clip)
    conn = connect(tmp_path / "t.db")
    aid = sources._api_import(conn, sources.source("zenodo"), "10682701", "Beetle", tmp_path / "c")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == "CC BY 4.0" and asset["creator"] == "Dr Science"
    assert "creativecommons.org" in json.loads(asset["evidence"])["license"]


def test_zenodo_refuses_uncleared_license(tmp_path, monkeypatch):
    record = {"metadata": {"title": "NC study", "license": {"id": "cc-by-nc-4.0"}},
              "files": [{"key": "v.mp4", "size": 1, "links": {"self": "https://zenodo.org/x"}}]}
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(json.dumps(record).encode()))
    conn = connect(tmp_path / "t.db")
    with pytest.raises(ValueError, match="not cleared"):
        sources._api_import(conn, sources.source("zenodo"), "1", "Bird", tmp_path / "c")


def test_figshare_import_uses_item_license(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    article = {"title": "<b>Pika</b> observation",
               "license": {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
               "authors": [{"full_name": "A. Researcher"}],
               "files": [{"name": "notes.txt", "size": 5},
                         {"name": "pika.webm", "size": 200,
                          "download_url": "https://ndownloader.figshare.com/files/9"}]}
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(json.dumps(article).encode()))
    monkeypatch.setattr(sources, "_download", lambda url, dest, **k: clip)
    conn = connect(tmp_path / "t.db")
    aid = sources._api_import(conn, sources.source("figshare"), "99", "Pika", tmp_path / "c")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == "CC BY 4.0" and asset["creator"] == "A. Researcher"


def test_archive_import_uses_licenseurl_and_mirror(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    metadata = {"server": "ia801409.us.archive.org", "dir": "/0/items/film",
                "metadata": {"title": "Old Wildlife Film", "creator": "EB Films",
                             "licenseurl": "https://creativecommons.org/licenses/by/3.0/"},
                "files": [{"name": "film.avi", "format": "Cinepack", "size": "9"},
                          {"name": "film.mp4", "format": "h.264", "size": "500"}]}
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(json.dumps(metadata).encode()))
    calls = []
    monkeypatch.setattr(sources, "_download",
                        lambda url, dest, **k: calls.append((url, k)) or clip)
    conn = connect(tmp_path / "t.db")
    aid = sources._api_import(conn, sources.source("archive"), "film", "Otter", tmp_path / "c")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == "CC BY 3.0"
    assert calls[0][0] == "https://ia801409.us.archive.org/0/items/film/film.mp4"
    assert calls[0][1]["extra_hosts"] == {"ia801409.us.archive.org"}


def test_archive_import_requires_item_license(tmp_path, monkeypatch):
    metadata = {"server": "s.us.archive.org", "dir": "/d",
                "metadata": {"title": "No license"},
                "files": [{"name": "v.mp4", "size": "10"}]}
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(json.dumps(metadata).encode()))
    conn = connect(tmp_path / "t.db")
    with pytest.raises(ValueError, match="not cleared"):
        sources._api_import(conn, sources.source("archive"), "v", "Otter", tmp_path / "c")


def test_vecteezy_free_assets_require_budget_clearance(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    from animal_pipeline import media
    aid_conn = connect(tmp_path / "t.db")
    aid = media.import_asset(aid_conn, clip, source_ref="https://www.vecteezy.com/video/42",
                             creator="Maker", license_type="Vecteezy License (free tier)",
                             evidence="License page", attribution="Maker",
                             species="Otter", commercial=True, modifications=True)
    asset = aid_conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()

    monkeypatch.setattr(media, "settings", lambda: {"vecteezy": {"budget_verified": False}})
    assert "Vecteezy free-tier production budget not verified" in media.rights_errors(asset)
    with pytest.raises(ValueError, match="budget"):
        media.approve_asset(aid_conn, aid)

    monkeypatch.setattr(media, "settings", lambda: {"vecteezy": {"budget_verified": True}})
    assert "Vecteezy free-tier production budget not verified" not in media.rights_errors(asset)
