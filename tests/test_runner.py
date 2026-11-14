from pathlib import Path

from prijsscraper.config import laad_config
from prijsscraper.db import DB
from prijsscraper.scraper.fetch import RobotsGeblokkeerd
from prijsscraper.scraper.runner import normaliseer, scrape

FIX = Path(__file__).parent / "fixtures"


class NepFetcher:
    def haal(self, url, interval=None):
        if "bouwmaat" in url:
            return (FIX / "microdata.html").read_text()   # 27,50 excl. btw, niet op voorraad
        if "gamma" in url:
            raise RobotsGeblokkeerd(url)
        if "praxis" in url:
            raise RuntimeError("timeout")
        return (FIX / "jsonld.html").read_text()          # 11,49 incl.


def test_normaliseer():
    assert normaliseer(10, False, 0.21, 1) == 12.1
    assert normaliseer(10, True, 0.21, 1.2) == 12.0


def test_scrape_met_nepfetcher(tmp_path):
    cfg = laad_config("config")
    db = DB(tmp_path / "t.db")
    res = scrape(cfg, db, fetcher=NepFetcher())
    assert res["ok"] >= 3 and res["robots"] == 1 and res["fout"] == 1
    rij = db.con.execute("SELECT * FROM observaties WHERE concurrent='bouwmaat'").fetchone()
    assert round(rij["prijs_incl"], 2) == round(27.5 * 1.21, 2) and rij["op_voorraad"] == 0
    rij = db.con.execute("SELECT * FROM observaties WHERE concurrent='onlinetuinhout'").fetchone()
    assert round(rij["prijs_incl"], 2) == round(11.49 * 1.2, 2)


def test_demo_end_to_end(tmp_path, monkeypatch):
    from prijsscraper.cli import main
    assert main(["demo", "--map", str(tmp_path / "demo")]) == 0
    rapporten = list((tmp_path / "demo" / "output").rglob("rapport.html"))
    assert rapporten and "Prijsvoorstellen" in rapporten[0].read_text()
    db = DB(tmp_path / "demo" / "prijzen.db")
    vs = db.voorstellen()
    assert 15 <= len(vs) <= 20
    assert {"omhoog", "omlaag"} <= {v["richting"] for v in vs}
