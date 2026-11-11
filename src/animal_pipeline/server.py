from __future__ import annotations

import html
import json
import mimetypes
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .core import ROOT, connect
from .review import review_errors


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{_esc(title)} — Naminal Hub review</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:760px;margin:2rem auto;
padding:0 1rem;background:#111;color:#eee}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:.4rem;border-bottom:1px solid #333;text-align:left;font-size:.9rem}}
a{{color:#8ec7ff}}video{{width:100%;max-width:360px;border-radius:8px}}
pre{{background:#000;padding:.7rem;overflow:auto;font-size:.75rem}}
.badge{{padding:.15rem .5rem;border-radius:4px;background:#333;font-size:.75rem}}
.block{{color:#ff9c9c}}</style></head><body><h1>Naminal Hub</h1>{body}
<p><a href="/">← Review queue</a></p></body></html>"""


def queue_page(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT p.id,p.revision,p.status,p.created_at,t.species FROM plans p "
        "JOIN topics t ON t.id=p.topic_id ORDER BY p.created_at DESC").fetchall()
    lines = "".join(
        f"<tr><td><a href='/review/{_esc(r['id'])}'>{_esc(r['species'])}</a></td>"
        f"<td>r{_esc(r['revision'])}</td><td><span class='badge'>{_esc(r['status'])}</span></td>"
        f"<td>{_esc(r['created_at'][:19])}</td></tr>" for r in rows)
    body = ("<h2>Review queue</h2><table><tr><th>Subject</th><th>Rev</th>"
            "<th>Status</th><th>Created (UTC)</th></tr>" + lines + "</table>"
            "<p>Approvals are deliberately CLI-only: <code>python main.py approve "
            "PLAN_ID SHA256 --verify-facts --verify-visuals --verify-rights "
            "--verify-audio</code></p>")
    return _page("Queue", body)


def review_page(conn: sqlite3.Connection, plan_id: str) -> str:
    row = conn.execute("SELECT p.*, t.species FROM plans p JOIN topics t ON t.id=p.topic_id "
                       "WHERE p.id=?", (plan_id,)).fetchone()
    if not row:
        return _page("Unknown", "<p>Unknown plan.</p>")
    plan = json.loads(row["content"])
    blocking = review_errors(conn, row)
    manifest_path = Path(row["video_path"]).with_name("manifest.json") if row["video_path"] else None
    manifest = (manifest_path.read_text() if manifest_path and manifest_path.is_file() else "{}")
    video = (f"<video controls src='/video/{_esc(plan_id)}'></video>"
             if row["video_path"] and Path(row["video_path"]).is_file() else
             "<p>No rendered video.</p>")
    errors = "".join(f"<li class='block'>{_esc(e)}</li>" for e in blocking)
    script = "".join(f"<li>{_esc(s['text'])} <em>({_esc(s['duration_s'])}s)</em></li>"
                     for s in plan.get("segments", []))
    body = (f"<h2>{_esc(plan.get('title', row['species']))}</h2>"
            f"<p><span class='badge'>{_esc(row['status'])}</span> "
            f"rev {_esc(row['revision'])} · <code>{_esc(row['video_hash'] or 'no video')}</code></p>"
            f"{video}<h3>Blocking checks</h3><ul>{errors or '<li>none</li>'}</ul>"
            f"<h3>Script</h3><ul>{script}</ul><h3>Description</h3>"
            f"<p>{_esc(plan.get('description', ''))}</p><h3>Manifest</h3><pre>{_esc(manifest)}</pre>")
    return _page(row["species"], body)


def make_handler(db_path: Path | None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content: bytes, mime: str = "text/html; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(content)

        def _video(self, conn: sqlite3.Connection, plan_id: str):
            row = conn.execute("SELECT video_path, video_hash FROM plans WHERE id=?",
                               (plan_id,)).fetchone()
            if not row or not row["video_path"]:
                return self._send(404, b"no video")
            path = Path(row["video_path"]).resolve()
            if not path.is_file() or ROOT not in path.parents:
                return self._send(404, b"no video")
            return self._send(200, path.read_bytes(),
                              mimetypes.guess_type(path.name)[0] or "video/mp4")

        def do_GET(self):
            conn = connect(db_path)
            try:
                path = urlparse(self.path).path
                if path == "/":
                    return self._send(200, queue_page(conn).encode())
                if path.startswith("/review/"):
                    target = path[len("/review/"):].split("/")[0]
                    return self._send(200, review_page(conn, target).encode())
                if path.startswith("/video/"):
                    return self._video(conn, path[len("/video/"):].split("/")[0])
                self._send(404, b"not found")
            finally:
                conn.close()

        def do_HEAD(self):
            self.do_GET()

        def log_message(self, *_args):
            return

    return Handler


def serve(db_path: Path | None = None, host: str = "127.0.0.1",
          port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"Review UI (read-only, local): http://{host}:{port}")
    httpd.serve_forever()
