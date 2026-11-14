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
import logging
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
    s = sub.add_parser("demo"); s.add_argument("--map", default="data/demo"); s.set_defaults(fn=cmd_demo)

    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
