"""Commandoregel.

  python -m prijsscraper dagelijks            scrape + voorstellen + rapport (voor cron)
  python -m prijsscraper scrape               alleen concurrentprijzen ophalen
  python -m prijsscraper voorstellen          alleen analyse + rapport
  python -m prijsscraper test-url URL         prijs-extractie testen bij het aanleggen van matches
  python -m prijsscraper akkoord 12 13 14     voorstellen goedkeuren (of: --csv export.csv)
  python -m prijsscraper afwijzen 15
  python -m prijsscraper doorvoeren           goedgekeurde prijzen in config/producten.csv zetten
  python -m prijsscraper demo                 volledige proefrun op gesimuleerde data
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import logging
import re
import sys
from datetime import date
from pathlib import Path

from .config import laad_config
from .db import DB, nu


def _paden(args):
    cfgmap = Path(args.config)
    db = Path(args.db) if args.db else Path("data/prijzen.db")
    return cfgmap, db


def cmd_scrape(args):
    from .scraper.runner import scrape
    cfgmap, dbpad = _paden(args)
    cfg = laad_config(cfgmap)
    db = DB(dbpad)
    res = scrape(cfg, db, alleen=args.alleen.split(",") if args.alleen else None)
    print("Scrape klaar:", ", ".join(f"{k}={v}" for k, v in sorted(res.items())) or "geen matches")
    db.close()


def cmd_voorstellen(args, start: str | None = None):
    from .pricing import PrijsEngine
    from .report import schrijf_csv, schrijf_html
    cfgmap, dbpad = _paden(args)
    cfg = laad_config(cfgmap)
    db = DB(dbpad)
    vandaag = date.today()
    db.verwijder_open_voorstellen(vandaag.isoformat())   # herhaalde run op dezelfde dag = vervangen
    analyse = PrijsEngine(cfg, db).run()
    for v in analyse.voorstellen:
        v.id = db.bewaar_voorstel(vandaag.isoformat(), v)
    db.bewaar_signalen(vandaag.isoformat(), analyse.signalen)
    db.commit()
    uit = Path(args.uitvoer) / vandaag.isoformat()
    schrijf_csv(analyse, uit / "voorstellen.csv")
    status = db.scrape_status(start) if start else None
    schrijf_html(analyse, uit / "rapport.html", vandaag, status)
    omh = sum(v.richting == "omhoog" for v in analyse.voorstellen)
    print(f"{len(analyse.voorstellen)} voorstellen ({omh} omhoog, {len(analyse.voorstellen) - omh} omlaag) "
          f"uit {analyse.kandidaten} kandidaten; {len(analyse.signalen)} signalen.")
    print(f"Rapport: {uit / 'rapport.html'}")
    db.close()
    return analyse


def cmd_dagelijks(args):
    start = nu()
    cmd_scrape(args)
    cmd_voorstellen(args, start)


def cmd_test_url(args):
    from .scraper.extract import extraheer
    from .scraper.fetch import Fetcher
    cfgmap, _ = _paden(args)
    cfg = laad_config(cfgmap)
    sel = cfg.concurrenten[args.concurrent].selectors if args.concurrent else {}
    f = Fetcher(cfg.instellingen.get("user_agent", "Prijsmonitor/1.0"), standaard_interval=0)
    ex = extraheer(f.haal(args.url), sel, args.url)
    print(f"prijs={ex.prijs} van_prijs={ex.van_prijs} voorraad={ex.op_voorraad} "
          f"methode={ex.methode} betrouwbaarheid={ex.betrouwbaarheid}")


def _ids(args) -> list[int]:
    ids = [int(i) for i in args.ids]
    if args.csv:
        with open(args.csv, encoding="utf-8-sig") as f:
            ids += [int(r["voorstel_id"]) for r in csv.DictReader(f, delimiter=";") if r.get("voorstel_id")]
    return ids


def cmd_status(args, status: str):
    _, dbpad = _paden(args)
    db = DB(dbpad)
    ids = _ids(args)
    for i in ids:
        db.zet_status(i, status)
    db.commit()
    print(f"{len(ids)} voorstel(len) op '{status}' gezet.")


def cmd_doorvoeren(args):
    cfgmap, dbpad = _paden(args)
    db = DB(dbpad)
    akkoord = {v["sku"]: v for v in db.voorstellen(status="akkoord")}
    pad = cfgmap / "producten.csv"
    with open(pad, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        velden, rijen = rd.fieldnames, list(rd)
    n = 0
    for r in rijen:
        v = akkoord.get(r["sku"])
        if v:
            r["verkoopprijs_incl_btw"] = f"{v['voorgestelde_prijs']:.2f}"
            db.zet_status(v["id"], "doorgevoerd")
            db.registreer_eigen_prijs(r["sku"], v["voorgestelde_prijs"], f"voorstel {v['id']}")
            n += 1
    with open(pad, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=velden)
        w.writeheader()
        w.writerows(rijen)
    db.commit()
    print(f"{n} prijzen doorgevoerd in {pad}. Vergeet niet ze ook in kassa/webshop te zetten "
          f"(of koppel een export, zie README).")


def cmd_importeer(args):
    from .catalogus import importeer
    res = importeer(args.url, Path(args.uit), args.omzet, args.marge, interval=args.interval)
    print(f"{res['artikelen']} artikelen uit {res['paginas']} productpagina's -> {args.uit}")


def cmd_aanbod(args):
    """Assortimenten van alle concurrenten met een catalogusbron verzamelen."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    from .markt import naar_dict, verzamel
    from .scraper.fetch import Fetcher
    cfgmap, _ = _paden(args)
    cfg = laad_config(cfgmap)
    btw = cfg.strategie["algemeen"]["btw"]
    fetcher = Fetcher(cfg.instellingen.get("user_agent", "Prijsmonitor/1.0"),
                      standaard_interval=cfg.instellingen.get("standaard_min_interval_sec", 2))
    from . import markt
    namen = [p.naam for p in cfg.producten.values()]
    markt.ASSORTIMENT_URL = markt.assortiment_patroon(namen)
    dbpad = Path(args.db) if getattr(args, "db", None) else None
    if dbpad and dbpad.exists():
        refs = DB(dbpad).marktreferenties()
        open_ = [p.naam for p in cfg.producten.values() if refs.get(p.sku, {}).get("niveau") in (None, "D")]
        markt.PRIORITEIT_URL = markt.assortiment_patroon(open_, min_n=2)
    if args.aanvullen and Path(args.uit).exists():
        markt.BEKENDE_URLS = {json.loads(r)["url"] for r in open(args.uit, encoding="utf-8") if r.strip()}
    bronnen = [c for c in cfg.concurrenten.values()
               if c.catalogus.get("soort") and c.bereikbaar and (not args.alleen or c.id in args.alleen.split(","))]
    uit = Path(args.uit)
    uit.parent.mkdir(parents=True, exist_ok=True)
    totaal = 0
    with ThreadPoolExecutor(max_workers=max(1, len(bronnen))) as pool, open(uit, "a" if args.aanvullen else "w") as f:
        for c, res in zip(bronnen, pool.map(lambda c: verzamel(c, btw, fetcher), bronnen)):
            for a in res:
                f.write(json.dumps(naar_dict(a), ensure_ascii=False) + "\n")
            totaal += len(res)
            print(f"{c.naam}: {len(res)} producten/varianten", flush=True)
    print(f"Totaal {totaal} -> {uit}")


def cmd_koppel(args):
    """Elk eigen artikel een marktreferentie geven (niveaus A-D, zie marktmodel.py); schrijft
    matches.csv (A) en koppelingen-afgeleid.csv (B/C), zet de prijzen als observaties in de database
    en bewaart per artikel de marktreferentie."""
    import json
    import statistics
    from .marktmodel import (bevestiging, bouwpakketten, breedteprofielen, categorie_index, hout_en_platen,
                              merkartikelen)
    from .matcher import kenmerken, koppel
    cfgmap, dbpad = _paden(args)
    cfg = laad_config(cfgmap)
    aanbod = [json.loads(r) for r in open(args.aanbod, encoding="utf-8") if r.strip()]
    afwijkend = re.compile(r"b-keus|tweede keus|2e keus|bundel|restpartij|outlet|pakket van|set van \d|per pallet|cadeaubon", re.I)
    aanbod = [a for a in aanbod if a.get("prijs_incl") and a["concurrent"] in cfg.concurrenten
              and not afwijkend.search(a["titel"])]
    uitgesloten = re.compile(r"b-keus|pakket \d|cadeaubon|sale|^sample", re.I)
    eigen = [{"sku": p.sku, "naam": p.naam, "prijs": p.verkoopprijs_incl_btw} for p in cfg.producten.values()
             if not uitgesloten.search(p.naam)]

    # A: exact
    exact = koppel(eigen, aanbod, args.drempel)
    for m in exact:
        m["niveau"] = "A"
    gekoppeld = {(m["sku"], m["concurrent"]) for m in exact}
    # B/C: afgeleid
    ek = [(e, kenmerken(e["naam"])) for e in eigen]
    ak = [(a, kenmerken(a["titel"])) for a in aanbod]
    afgeleid = hout_en_platen(ek, ak, gekoppeld)
    gekoppeld |= {(m["sku"], m["concurrent"]) for m in afgeleid}
    afgeleid += bouwpakketten(ek, aanbod, gekoppeld)
    gekoppeld |= {(m["sku"], m["concurrent"]) for m in afgeleid}
    for stap in (lambda: bevestiging(ek, aanbod, gekoppeld), lambda: breedteprofielen(ek, ak, gekoppeld),
                 lambda: merkartikelen(ek, ak, gekoppeld)):
        nieuw = stap()
        afgeleid += nieuw
        gekoppeld |= {(m["sku"], m["concurrent"]) for m in nieuw}

    with open(cfgmap / "matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "concurrent", "url", "omrekenfactor", "opmerking"])
        for m in sorted(exact, key=lambda m: (m["sku"], m["concurrent"])):
            w.writerow([m["sku"], m["concurrent"], m["url"], m["omrekenfactor"],
                        f"auto {m['betrouwbaarheid']:.2f}: {m['titel_concurrent'][:90]}"])
    with open(cfgmap / "koppelingen-afgeleid.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "concurrent", "niveau", "betrouwbaarheid", "omrekenfactor", "prijs_concurrent",
                    "prijs_omgerekend", "titel_concurrent", "url"])
        for m in sorted(afgeleid, key=lambda m: (m["sku"], m["concurrent"])):
            w.writerow([m["sku"], m["concurrent"], m["niveau"], m["betrouwbaarheid"], m["omrekenfactor"],
                        m["prijs_incl"], round(m["prijs_incl"] * m["omrekenfactor"], 2), m["titel_concurrent"][:140],
                        m["url"]])

    db = DB(dbpad)
    alles = exact + afgeleid
    for m in alles:
        db.voeg_observatie_toe(sku=m["sku"], concurrent=m["concurrent"], url=m["url"],
                               prijs_ruw=m["prijs_incl"], prijs_incl=round(m["prijs_incl"] * m["omrekenfactor"], 4),
                               adviesprijs=round(m["adviesprijs"] * m["omrekenfactor"], 4) if m.get("adviesprijs") else None,
                               op_voorraad=m.get("op_voorraad"), status="ok", niveau=m["niveau"],
                               melding=f"catalogus/{m['niveau']} {m['betrouwbaarheid']:.2f}: {m['titel_concurrent'][:80]}")

    # marktreferentie per artikel (A-C) + categorie-index (D) voor de rest
    per_sku = defaultdict(list)
    for m in alles:
        per_sku[m["sku"]].append(m)
    prijs = {e["sku"]: e["prijs"] for e in eigen}
    referenties, index = [], {}
    for sku, lijst in per_sku.items():
        beste = min(m["niveau"] for m in lijst)
        prijzen = [m["prijs_incl"] * m["omrekenfactor"] for m in lijst]
        mp = statistics.median(prijzen)
        index[sku] = prijs[sku] / mp
        referenties.append({"sku": sku, "niveau": beste, "marktprijs": round(mp, 2), "bronnen": len(lijst),
                            "betrouwbaarheid": round(max(m["betrouwbaarheid"] for m in lijst), 3),
                            "toelichting": f"{len(lijst)} concurrentprijzen ({', '.join(sorted({m['niveau'] for m in lijst}))})"})
    # ook B-keus/sale/samples krijgen een (indicatieve) referentie: via producttype van de naam zonder voorvoegsel
    producten = [{"sku": p.sku, "naam": re.sub(r"(?i)^(sample|b-keus)\s*", "", p.naam), "categorie": p.categorie,
                  "rol": p.rol, "prijs": p.verkoopprijs_incl_btw} for p in cfg.producten.values()]
    referenties += categorie_index(producten, index)
    db.bewaar_marktreferenties(referenties)
    db.commit()
    db.close()

    tel = Counter(r["niveau"] for r in referenties)
    print(f"{len(exact)} exacte en {len(afgeleid)} afgeleide koppelingen "
          f"({Counter(m['niveau'] for m in afgeleid)})")
    print(f"Marktreferentie voor {len(referenties)} van {len(cfg.producten)} artikelen: "
          + ", ".join(f"{n}={tel.get(n, 0)}" for n in "ABCD"))


def cmd_web(args):
    from .web.data import bouw_payload
    from .web.server import render_app, serve
    cfgmap, dbpad = _paden(args)
    if args.export:
        db = DB(dbpad)
        payload = bouw_payload(laad_config(cfgmap), db, modus=args.modus)
        db.close()
        Path(args.export).parent.mkdir(parents=True, exist_ok=True)
        Path(args.export).write_text(render_app(payload), encoding="utf-8")
        print(f"Statische omgeving geschreven naar {args.export}")
        return
    serve(cfgmap, dbpad, args.host, args.poort)


def cmd_demo(args):
    from .demo import genereer
    map_ = genereer(Path(args.map))
    print(f"Demodata gegenereerd in {map_}")
    args.config = str(map_ / "config")
    args.db = str(map_ / "prijzen.db")
    args.uitvoer = str(map_ / "output")
    cmd_voorstellen(args)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="prijsscraper", description="Prijsmonitor Houthandel Gorinchem")
    ap.add_argument("--config", default="config", help="map met configuratiebestanden")
    ap.add_argument("--db", default=None, help="pad naar SQLite-database (standaard data/prijzen.db)")
    ap.add_argument("--uitvoer", default="output", help="map voor rapporten")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape"); s.add_argument("--alleen", help="komma-gescheiden concurrent-id's")
    s.set_defaults(fn=cmd_scrape)
    sub.add_parser("voorstellen").set_defaults(fn=cmd_voorstellen)
    s = sub.add_parser("dagelijks"); s.add_argument("--alleen"); s.set_defaults(fn=cmd_dagelijks)
    s = sub.add_parser("test-url"); s.add_argument("url"); s.add_argument("--concurrent")
    s.set_defaults(fn=cmd_test_url)
    for naam, st in (("akkoord", "akkoord"), ("afwijzen", "afgewezen")):
        s = sub.add_parser(naam); s.add_argument("ids", nargs="*"); s.add_argument("--csv")
        s.set_defaults(fn=lambda a, st=st: cmd_status(a, st))
    sub.add_parser("doorvoeren").set_defaults(fn=cmd_doorvoeren)
    s = sub.add_parser("importeer", help="eigen assortiment uit de WooCommerce-webshop inlezen")
    s.add_argument("--url", default="https://www.houthandelgorinchem.nl")
    s.add_argument("--uit", default="config/producten.csv")
    s.add_argument("--omzet", type=float, default=10_000_000, help="jaaromzet voor de afzetschatting")
    s.add_argument("--marge", type=float, default=0.17, help="gemiddelde brutomarge voor de inkoopschatting")
    s.add_argument("--interval", type=float, default=1.0)
    s.set_defaults(fn=cmd_importeer)
    s = sub.add_parser("aanbod", help="assortimenten van concurrenten verzamelen")
    s.add_argument("--uit", default="data/aanbod.jsonl"); s.add_argument("--alleen")
    s.add_argument("--aanvullen", action="store_true")
    s.set_defaults(fn=cmd_aanbod)
    s = sub.add_parser("koppel", help="eigen artikelen automatisch aan concurrentaanbod koppelen")
    s.add_argument("--aanbod", default="data/aanbod.jsonl"); s.add_argument("--drempel", type=float, default=0.75)
    s.set_defaults(fn=cmd_koppel)
    s = sub.add_parser("web", help="webomgeving starten of als statisch bestand exporteren")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--poort", type=int, default=8080)
    s.add_argument("--export", help="schrijf een zelfstandige HTML-versie naar dit pad")
    s.add_argument("--modus", default="live", choices=["live", "voorbeeld"])
    s.set_defaults(fn=cmd_web)
    s = sub.add_parser("demo"); s.add_argument("--map", default="data/demo"); s.set_defaults(fn=cmd_demo)

    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
