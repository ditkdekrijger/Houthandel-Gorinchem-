from datetime import datetime, timedelta

import pytest
import yaml

from prijsscraper.config import Concurrent, Config, Product
from prijsscraper.db import DB
from prijsscraper.pricing import PrijsEngine, gewogen_kwantiel, rond_af, selecteer

NU = datetime(2026, 11, 14, 6, 0)


def strategie():
    with open("config/strategie.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def maak(tmp_path, producten, prijzen, s=None):
    """prijzen = {sku: {concurrent: prijs}}; observatie van vandaag + 30 dagen stabiele historie."""
    conc = {c: Concurrent(c, c.upper(), "online", 1.0, True, "") for c in "abcdef"}
    cfg = Config({}, conc, {p.sku: p for p in producten}, [], s or strategie())
    db = DB(tmp_path / "t.db")
    for sku, per in prijzen.items():
        for c, p in per.items():
            hist, nu = (p if isinstance(p, tuple) else (p, p))
            for d in range(30, 0, -1):
                db.voeg_observatie_toe(sku=sku, concurrent=c, url="", prijs_ruw=hist, prijs_incl=hist,
                                       tijdstip=(NU - timedelta(days=d)).isoformat())
            db.voeg_observatie_toe(sku=sku, concurrent=c, url="", prijs_ruw=nu, prijs_incl=nu,
                                   tijdstip=(NU - timedelta(hours=1)).isoformat())
    db.commit()
    return cfg, db


def prod(sku="X1", inkoop=10.0, prijs=25.0, rol="kern", afzet=20):
    return Product(sku, "Test", "hout", "stuk", inkoop, prijs, rol, afzet)


def test_kwantiel():
    assert gewogen_kwantiel([(10, 1), (20, 1), (30, 1)], 0.5) == 20
    assert gewogen_kwantiel([(10, 1), (20, 1)], 0.0) == 10
    # zwaarder gewicht trekt mediaan naar zich toe
    assert gewogen_kwantiel([(10, 3), (20, 1)], 0.5) < 15


@pytest.mark.parametrize("p,r,v", [(12.30, "omlaag", 11.99), (12.30, "omhoog", 12.49), (7.10, "omlaag", 6.99),
                                   (7.10, "omhoog", 7.25), (131.0, "omlaag", 130.95), (131.0, "omhoog", 131.95)])
def test_afronden(p, r, v):
    assert rond_af(p, "psychologisch", r) == v


def test_verhoging_als_we_te_goedkoop_zijn(tmp_path):
    cfg, db = maak(tmp_path, [prod(prijs=25.0)], {"X1": {"a": 30, "b": 31, "c": 32, "d": 33}})
    v = PrijsEngine(cfg, db, NU).analyseer_product(cfg.producten["X1"], [])
    assert v.richting == "omhoog"
    assert v.voorgestelde_prijs <= 25.0 * 1.08 + 1e-9        # max-stap gerespecteerd
    assert v.brutowinst_delta_week > 0


def test_verlaging_maar_nooit_onder_margebodem(tmp_path):
    # inkoop 14 -> bodem kern (20% marge) = 14/0.8*1.21 = 21.18; markt zit daaronder
    cfg, db = maak(tmp_path, [prod(inkoop=14, prijs=24.0)], {"X1": {"a": 20, "b": 20.5, "c": 21}})
    sig = []
    v = PrijsEngine(cfg, db, NU).analyseer_product(cfg.producten["X1"], sig)
    assert v.richting == "omlaag" and v.voorgestelde_prijs >= 21.18
    assert v.marge_nieuw >= 0.20
    assert any(s.soort == "onder_bodem" for s in sig)


def test_uitschieter_wordt_genegeerd(tmp_path):
    cfg, db = maak(tmp_path, [prod(prijs=30.0)], {"X1": {"a": 30, "b": 31, "c": 29.5, "d": 75}})
    sig = []
    v = PrijsEngine(cfg, db, NU).analyseer_product(cfg.producten["X1"], sig)
    assert any(s.soort == "uitschieter" for s in sig)
    assert v is None or v.hoogste < 75


def test_actie_telt_minder_mee(tmp_path):
    # concurrent 'a' zit vandaag tijdelijk 25% lager dan zijn 30-dagen prijs
    cfg, db = maak(tmp_path, [prod(prijs=30.0)], {"X1": {"a": (30, 22.5), "b": 30, "c": 30.5}})
    prijzen = PrijsEngine(cfg, db, NU)._concurrentprijzen("X1")
    a = next(c for c in prijzen if c.concurrent == "a")
    assert a.actie and a.gewicht == 0.5


def test_te_weinig_data(tmp_path):
    cfg, db = maak(tmp_path, [prod()], {"X1": {"a": 30}})
    sig = []
    assert PrijsEngine(cfg, db, NU).analyseer_product(cfg.producten["X1"], sig) is None
    assert sig[0].soort == "te_weinig_data"


def test_geen_ruis_voorstellen(tmp_path):
    cfg, db = maak(tmp_path, [prod(prijs=29.95)], {"X1": {"a": 30.2, "b": 30.5, "c": 31}})
    assert PrijsEngine(cfg, db, NU).analyseer_product(cfg.producten["X1"], []) is None


def test_afkoelperiode(tmp_path):
    cfg, db = maak(tmp_path, [prod(prijs=25.0)], {"X1": {"a": 30, "b": 31, "c": 32}})
    eng = PrijsEngine(cfg, db, NU)
    v = eng.analyseer_product(cfg.producten["X1"], [])
    db.bewaar_voorstel((NU - timedelta(days=1)).date().isoformat(), v)
    assert eng.analyseer_product(cfg.producten["X1"], []) is None


def test_selectie_balans():
    from types import SimpleNamespace as N
    k = [N(score=100 - i, richting="omhoog") for i in range(30)] + [N(score=5 - i * 0.1, richting="omlaag") for i in range(10)]
    gekozen = selecteer(k, {"voorstellen_per_dag": 20, "min_aandeel_omhoog": 0.25, "min_aandeel_omlaag": 0.25})
    assert len(gekozen) == 20
    assert sum(v.richting == "omlaag" for v in gekozen) == 5
