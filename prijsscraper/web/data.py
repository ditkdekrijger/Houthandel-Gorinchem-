"""Bouwt de gegevens voor de webomgeving uit configuratie + database."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..config import Config
from ..db import DB, utcnow
from ..pricing import marge

DAGEN_HISTORIE = 30


def _r(x, n=2):
    return None if x is None else round(x, n)


def bouw_payload(cfg: Config, db: DB, modus: str = "live") -> dict:
    btw = cfg.strategie["algemeen"]["btw"]
    max_uur = cfg.strategie["algemeen"]["max_leeftijd_uur"]
    datum = db.laatste_datum() or date.today().isoformat()
    nu = utcnow()

    # Referentiemoment = laatste observatie (zodat oude demo-/testdata ook klopt)
    r = db.con.execute("SELECT MAX(tijdstip) t FROM observaties WHERE status='ok'").fetchone()
    ref = datetime.fromisoformat(r["t"]) if r and r["t"] else nu

    # --- historie per sku: dagelijkse min/mediaan/max over concurrenten -----------
    grens = (ref - timedelta(days=DAGEN_HISTORIE)).isoformat()
    per_dag: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for o in db.con.execute(
            "SELECT sku, concurrent, substr(tijdstip,1,10) d, prijs_incl FROM observaties "
            "WHERE status='ok' AND prijs_incl IS NOT NULL AND tijdstip>=? ORDER BY tijdstip", (grens,)):
        per_dag[o["sku"]][o["d"]][o["concurrent"]] = o["prijs_incl"]

    historie = {}
    for sku, dagen in per_dag.items():
        reeks = []
        for d in sorted(dagen):
            prijzen = sorted(dagen[d].values())
            reeks.append({"d": d, "min": _r(prijzen[0]), "med": _r(statistics.median(prijzen)),
                          "max": _r(prijzen[-1]), "n": len(prijzen),
                          "per": {k: _r(v) for k, v in dagen[d].items()}})
        historie[sku] = reeks

    # --- artikelen ------------------------------------------------------------------
    artikelen = []
    for p in cfg.producten.values():
        obs = db.laatste_observaties(p.sku, max_uur, ref + timedelta(seconds=1))
        prijzen = sorted(o["prijs_incl"] for o in obs if o["prijs_incl"])
        a = {"sku": p.sku, "naam": p.naam, "categorie": p.categorie, "rol": p.rol, "eenheid": p.eenheid,
             "prijs": _r(p.verkoopprijs_incl_btw), "inkoop": _r(p.inkoopprijs_excl_btw),
             "marge": _r(marge(p.verkoopprijs_incl_btw, p.inkoopprijs_excl_btw, btw), 4),
             "afzet": p.afzet_per_week, "n": len(prijzen),
             "concurrenten": sorted([{"id": o["concurrent"], "naam": cfg.concurrenten[o["concurrent"]].naam
                                      if o["concurrent"] in cfg.concurrenten else o["concurrent"],
                                      "prijs": _r(o["prijs_incl"]), "url": o["url"] or "",
                                      "actie": bool(o["adviesprijs"] and o["prijs_incl"] < o["adviesprijs"])}
                                     for o in obs if o["prijs_incl"]], key=lambda c: c["prijs"])}
        if prijzen:
            med = statistics.median(prijzen)
            a.update(laagste=_r(prijzen[0]), mediaan=_r(med), hoogste=_r(prijzen[-1]),
                     index=_r(p.verkoopprijs_incl_btw / med, 4),
                     positie=1 + sum(1 for x in prijzen if x < p.verkoopprijs_incl_btw - 0.005))
        artikelen.append(a)

    # --- voorstellen ------------------------------------------------------------------
    art = {a["sku"]: a for a in artikelen}
    voorstellen = []
    for v in db.voorstellen(datum=datum):
        p = cfg.producten.get(v["sku"])
        a = art.get(v["sku"], {})
        voorstellen.append({
            "id": v["id"], "sku": v["sku"], "naam": p.naam if p else v["sku"],
            "categorie": p.categorie if p else "", "rol": p.rol if p else "",
            "huidig": v["huidige_prijs"], "voorstel": v["voorgestelde_prijs"], "richting": v["richting"],
            "pct": _r(v["voorgestelde_prijs"] / v["huidige_prijs"] - 1, 4),
            "marge_oud": _r(marge(v["huidige_prijs"], p.inkoopprijs_excl_btw, btw), 4) if p else None,
            "marge_nieuw": _r(marge(v["voorgestelde_prijs"], p.inkoopprijs_excl_btw, btw), 4) if p else None,
            "bw": v["brutowinst_delta_week"], "score": v["score"], "reden": v["reden"], "status": v["status"],
            "laagste": a.get("laagste"), "mediaan": a.get("mediaan"), "hoogste": a.get("hoogste"),
            "concurrenten": a.get("concurrenten", []),
        })

    # --- concurrenten -----------------------------------------------------------------
    sinds = (ref - timedelta(hours=max_uur)).isoformat()
    stat = defaultdict(lambda: defaultdict(int))
    for rij in db.scrape_status(sinds):
        stat[rij["concurrent"]][rij["status"]] += rij["n"]
    matches = defaultdict(int)
    for m in cfg.matches:
        matches[m.concurrent] += 1
    if not cfg.matches:  # demo: matches staan in de demo-config
        for rij in db.con.execute("SELECT concurrent, COUNT(DISTINCT sku) n FROM observaties GROUP BY concurrent"):
            matches[rij["concurrent"]] = rij["n"]
    concurrenten = []
    for c in cfg.concurrenten.values():
        ok = stat[c.id].get("ok", 0)
        totaal = sum(stat[c.id].values())
        ratio = []
        for a in artikelen:
            for cp in a["concurrenten"]:
                if cp["id"] == c.id and a["prijs"]:
                    ratio.append(cp["prijs"] / a["prijs"])
        if not c.bereikbaar:
            status = "geblokkeerd"
        elif totaal and ok / totaal >= 0.8:
            status = "actief"
        elif totaal:
            status = "storing"
        else:
            status = "niet gekoppeld"
        concurrenten.append({
            "id": c.id, "naam": c.naam, "type": c.type, "gewicht": c.gewicht, "url": c.basis_url,
            "status": status, "notitie": c.notitie, "ok": ok, "mislukt": totaal - ok,
            "matches": matches.get(c.id, 0),
            "prijsindex": _r(statistics.median(ratio), 4) if ratio else None,
            "artikelen": len(ratio),
        })

    signalen = []
    for s in db.signalen(datum):
        p = cfg.producten.get(s["sku"])
        signalen.append({"sku": s["sku"], "naam": p.naam if p else s["sku"], "soort": s["soort"], "tekst": s["tekst"]})

    impact = None
    bedrijf = cfg.strategie.get("bedrijf")
    if bedrijf:
        from ..impact import analyse
        impact = analyse(cfg, db, bedrijf["omzet_jaar"], bedrijf["brutomarge"])
        # ondergrens: alleen artikelen met >= 2 concurrentprijzen (betrouwbaarder)
        streng = analyse(cfg, db, bedrijf["omzet_jaar"], bedrijf["brutomarge"], min_bronnen=2)
        impact["bandbreedte"] = {
            "laag": streng["scenario"]["realistisch"]["brutowinst_delta_eur_volledige_dekking"],
            "hoog": impact["scenario"]["realistisch"]["brutowinst_delta_eur_volledige_dekking"],
            "omzet_laag": streng["scenario"]["realistisch"]["omzet_delta_eur_volledige_dekking"],
            "omzet_hoog": impact["scenario"]["realistisch"]["omzet_delta_eur_volledige_dekking"],
        }
        idx = sorted((a for a in artikelen if a.get("index") and a["n"] >= 2), key=lambda a: a["index"])
        impact["kansen_omhoog"] = [{k: a[k] for k in ("sku", "naam", "prijs", "mediaan", "index", "n", "rol")}
                                   for a in idx if a["index"] < 0.95][:12]
        impact["kansen_omlaag"] = [{k: a[k] for k in ("sku", "naam", "prijs", "mediaan", "index", "n", "rol")}
                                   for a in reversed(idx) if a["index"] > 1.08 and a["rol"] != "marge"][:12]

    gedekt = sum(1 for a in artikelen if a["n"] >= cfg.strategie["algemeen"]["min_concurrenten"])
    return {
        "meta": {"datum": datum, "gegenereerd": nu.replace(microsecond=0).isoformat() + "Z", "modus": modus,
                 "bedrijf": "Houthandel Gorinchem", "laatste_scrape": ref.isoformat(),
                 "voorstellen_per_dag": cfg.strategie["algemeen"]["voorstellen_per_dag"]},
        "kpis": {
            "voorstellen": len(voorstellen),
            "omhoog": sum(v["richting"] == "omhoog" for v in voorstellen),
            "omlaag": sum(v["richting"] == "omlaag" for v in voorstellen),
            "bw_week": _r(sum(v["bw"] or 0 for v in voorstellen)),
            "artikelen": len(artikelen), "gedekt": gedekt,
            "concurrenten_actief": sum(c["status"] == "actief" for c in concurrenten),
            "concurrenten_totaal": len(concurrenten),
            "signalen": len(signalen),
        },
        "voorstellen": voorstellen, "artikelen": artikelen, "historie": historie,
        "concurrenten": concurrenten, "signalen": signalen, "impact": impact,
    }
