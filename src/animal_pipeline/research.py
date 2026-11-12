from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse

from . import evidence, net
from .core import now

API = "https://en.wikipedia.org/w/api.php"
HOST = "en.wikipedia.org"
MAX_BYTES = 500_000
MAX_CANDIDATES = 25
MIN_SENTENCE = 25
TITLE = re.compile(r"[^#\[\]{}|<>\x00-\x1f]{1,200}")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _open(url: str, hosts: set[str]):
    return net.bounded_open(url, hosts, name="Wikipedia URL")


def _api(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(
        {"format": "json", "formatversion": "2", **params})
    with _open(url, {HOST}) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Wikipedia response exceeds byte limit")
    return json.loads(payload)


def search(query: str, limit: int = 10) -> list[str]:
    if not query.strip() or not 1 <= limit <= 50:
        raise ValueError("invalid Wikipedia query or limit")
    data = _api({"action": "query", "list": "search", "srnamespace": "0",
                 "srsearch": query, "srlimit": str(limit)})
    return [member["title"] for member in data.get("query", {}).get("search", [])]


def _extract(title: str) -> tuple[str, str]:
    if not TITLE.fullmatch(title.strip()):
        raise ValueError("a plain Wikipedia article title is required")
    data = _api({"action": "query", "prop": "extracts|info", "explaintext": "1",
                 "exintro": "1", "inprop": "url", "redirects": "1",
                 "titles": title.strip()})
    page = next((p for p in data.get("query", {}).get("pages", [])
                 if not p.get("missing")), None)
    if not page or not page.get("extract", "").strip():
        raise ValueError("Wikipedia article not found or empty")
    url = page["fullurl"]
    net.check_url(url, {HOST}, "Wikipedia URL")
    return url, page["extract"].strip()


def propose(conn: sqlite3.Connection, topic_id: str, title: str) -> list[str]:
    if not conn.execute("SELECT 1 FROM topics WHERE id=?", (topic_id,)).fetchone():
        raise ValueError("unknown topic")
    url, extract = _extract(title)
    existing = {row["statement"] for row in conn.execute(
        "SELECT statement FROM facts WHERE topic_id=?", (topic_id,))}
    ids = []
    for sentence in SENTENCE.split(extract):
        statement = sentence.strip()
        if len(statement) < MIN_SENTENCE or statement in existing:
            continue
        ids.append(evidence.add_fact(conn, topic_id, statement, "Wikipedia",
                                     url, statement, now()[:10]))
        existing.add(statement)
        if len(ids) >= MAX_CANDIDATES:
            break
    return ids
