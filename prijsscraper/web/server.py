"""Lichte webserver (alleen standaardbibliotheek) voor de prijsmonitor-omgeving.

  GET  /                     de app
  GET  /api/data             alle gegevens (JSON)
  POST /api/voorstel/<id>    {"status": "akkoord" | "afgewezen" | "open"}
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import laad_config
from ..db import DB
from .data import bouw_payload

APP = Path(__file__).with_name("app.html")
STATUSSEN = {"akkoord", "afgewezen", "open"}


def render_app(payload: dict | None = None) -> str:
    """De app-HTML; met payload ingebed = statische (deelbare) versie zonder server."""
    html = APP.read_text(encoding="utf-8")
    data = "null" if payload is None else json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("/*__DATA__*/null", data)


def maak_handler(cfgmap: Path, dbpad: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # stiller dan standaard
            pass

        def _stuur(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._stuur(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._stuur(200, render_app().encode(), "text/html; charset=utf-8")
            elif self.path == "/api/data":
                db = DB(dbpad)
                try:
                    self._json(bouw_payload(laad_config(cfgmap), db))
                finally:
                    db.close()
            else:
                self._json({"fout": "niet gevonden"}, 404)

        def do_POST(self):
            m = re.fullmatch(r"/api/voorstel/(\d+)", self.path)
            if not m:
                return self._json({"fout": "niet gevonden"}, 404)
            try:
                lengte = int(self.headers.get("Content-Length", 0))
                status = json.loads(self.rfile.read(lengte) or b"{}").get("status")
            except (ValueError, json.JSONDecodeError):
                return self._json({"fout": "ongeldige invoer"}, 400)
            if status not in STATUSSEN:
                return self._json({"fout": f"status moet een van {sorted(STATUSSEN)} zijn"}, 400)
            db = DB(dbpad)
            try:
                db.zet_status(int(m.group(1)), status)
                db.commit()
            finally:
                db.close()
            self._json({"id": int(m.group(1)), "status": status})

    return Handler


def serve(cfgmap: Path, dbpad: Path, host: str = "127.0.0.1", poort: int = 8080):
    srv = ThreadingHTTPServer((host, poort), maak_handler(cfgmap, dbpad))
    print(f"Prijsmonitor draait op http://{host}:{poort}  (Ctrl+C om te stoppen)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
