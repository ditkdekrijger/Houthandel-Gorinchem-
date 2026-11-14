import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from prijsscraper.cli import main
from prijsscraper.config import laad_config
from prijsscraper.db import DB
from prijsscraper.web.data import bouw_payload
from prijsscraper.web.server import maak_handler, render_app


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    map_ = tmp_path_factory.mktemp("web") / "demo"
    main(["demo", "--map", str(map_)])
    return map_


def test_payload(demo):
    db = DB(demo / "prijzen.db")
    p = bouw_payload(laad_config(demo / "config"), db, "voorbeeld")
    assert p["kpis"]["voorstellen"] == len(p["voorstellen"]) > 0
    assert p["kpis"]["omhoog"] + p["kpis"]["omlaag"] == p["kpis"]["voorstellen"]
    assert len(p["concurrenten"]) == 10 and all(c["status"] == "actief" for c in p["concurrenten"])
    v = p["voorstellen"][0]
    assert v["concurrenten"] and len(p["historie"][v["sku"]]) >= 20
    assert p["signalen"]


def test_statische_export_bevat_data(demo, tmp_path):
    uit = tmp_path / "o.html"
    main(["--config", str(demo / "config"), "--db", str(demo / "prijzen.db"), "web", "--export", str(uit)])
    html = uit.read_text()
    assert "/*__DATA__*/null" not in html and '"voorstellen"' in html


def test_server_api(demo):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), maak_handler(demo / "config", demo / "prijzen.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    basis = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert "Prijsmonitor" in urllib.request.urlopen(basis + "/").read().decode()
        data = json.loads(urllib.request.urlopen(basis + "/api/data").read())
        vid = data["voorstellen"][0]["id"]
        req = urllib.request.Request(f"{basis}/api/voorstel/{vid}", data=b'{"status":"akkoord"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["status"] == "akkoord"
        data = json.loads(urllib.request.urlopen(basis + "/api/data").read())
        assert next(v for v in data["voorstellen"] if v["id"] == vid)["status"] == "akkoord"
        bad = urllib.request.Request(f"{basis}/api/voorstel/{vid}", data=b'{"status":"weg"}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        srv.shutdown()


def test_render_app_escapet_script_einde():
    html = render_app({"x": "</script><script>alert(1)</script>"})
    assert "</script><script>alert" not in html
