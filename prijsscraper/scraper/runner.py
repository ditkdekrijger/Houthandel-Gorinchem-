"""Scrape-run: haalt alle gematchte concurrent-URL's op en slaat genormaliseerde prijzen op.

Elke concurrent krijgt een eigen worker-thread; binnen een domein wordt sequentieel en
met pauzes gescraped, zodat we geen enkele site belasten.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from ..config import Config, Match
from ..db import DB, nu
from .extract import extraheer
from .fetch import Fetcher, RobotsGeblokkeerd

log = logging.getLogger(__name__)


def normaliseer(prijs: float, incl_btw: bool, btw: float, omrekenfactor: float) -> float:
    """Naar prijs incl. btw per eigen verkoopeenheid."""
    p = prijs if incl_btw else prijs * (1 + btw)
    return round(p * omrekenfactor, 4)


def _scrape_concurrent(fetcher: Fetcher, conc, matches: list[Match], btw: float):
    resultaten = []
    for m in matches:
        basis = dict(sku=m.sku, concurrent=m.concurrent, url=m.url, tijdstip=nu())
        try:
            html = fetcher.haal(m.url, conc.min_interval_sec)
            ex = extraheer(html, conc.selectors, m.url)
            if not ex.prijs:
                resultaten.append({**basis, "prijs_ruw": None, "prijs_incl": None,
                                   "status": "geen_prijs",
                                   "melding": "pagina heeft meerdere varianten: zet ?variant=<id> in de URL"
                                   if ex.methode == "variant_vereist" else "geen prijs gevonden op pagina"})
                continue
            if ex.betrouwbaarheid == "laag":
                # gok-methode (heuristiek): opslaan ter controle, niet gebruiken voor prijsadvies
                resultaten.append({**basis, "prijs_ruw": ex.prijs, "prijs_incl": None, "status": "onzeker",
                                   "melding": f"prijs {ex.prijs} via {ex.methode}: stel CSS-selector in voor deze site"})
                continue
            van = normaliseer(ex.van_prijs, conc.prijs_incl_btw, btw, m.omrekenfactor) if ex.van_prijs else None
            resultaten.append({**basis, "prijs_ruw": ex.prijs,
                               "prijs_incl": normaliseer(ex.prijs, conc.prijs_incl_btw, btw, m.omrekenfactor),
                               "adviesprijs": van, "op_voorraad": ex.op_voorraad, "status": "ok",
                               "melding": f"{ex.methode}/{ex.betrouwbaarheid}"})
        except RobotsGeblokkeerd:
            resultaten.append({**basis, "prijs_ruw": None, "prijs_incl": None, "status": "robots",
                               "melding": "geblokkeerd door robots.txt"})
        except Exception as e:  # noqa: BLE001 – één kapotte pagina mag de run niet stoppen
            log.warning("Fout bij %s: %s", m.url, e)
            resultaten.append({**basis, "prijs_ruw": None, "prijs_incl": None, "status": "fout",
                               "melding": str(e)[:300]})
    return resultaten


def scrape(cfg: Config, db: DB, fetcher: Fetcher | None = None, alleen: list[str] | None = None) -> dict:
    inst = cfg.instellingen
    btw = cfg.strategie["algemeen"]["btw"]
    fetcher = fetcher or Fetcher(
        user_agent=inst.get("user_agent", "Prijsmonitor/1.0"),
        timeout=inst.get("timeout_sec", 20),
        max_pogingen=inst.get("max_pogingen", 3),
        respecteer_robots=inst.get("respecteer_robots_txt", True),
        standaard_interval=inst.get("standaard_min_interval_sec", 4),
    )
    per_conc: dict[str, list[Match]] = defaultdict(list)
    for m in cfg.matches:
        if m.concurrent not in cfg.concurrenten:
            log.warning("Onbekende concurrent '%s' in matches.csv", m.concurrent)
            continue
        if m.sku not in cfg.producten:
            log.warning("Onbekende sku '%s' in matches.csv", m.sku)
            continue
        if alleen and m.concurrent not in alleen:
            continue
        per_conc[m.concurrent].append(m)

    teller = defaultdict(int)
    with ThreadPoolExecutor(max_workers=max(1, len(per_conc))) as pool:
        futures = [pool.submit(_scrape_concurrent, fetcher, cfg.concurrenten[c], ms, btw)
                   for c, ms in per_conc.items()]
        for f in futures:
            for r in f.result():
                db.voeg_observatie_toe(**r)
                teller[r["status"]] += 1
    db.commit()
    return dict(teller)
