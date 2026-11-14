"""Simulatiemodus: realistische dataset (assortiment + 30 dagen concurrentieprijzen).

Hiermee is de hele keten (analyse -> voorstellen -> rapport) te testen zonder live te
scrapen. De gegenereerde bestanden staan in data/demo/ en raken config/ niet.
"""
from __future__ import annotations

import csv
import random
import shutil
from datetime import timedelta
from pathlib import Path

from .db import DB, utcnow

ASSORTIMENT = [
    # categorie, rol, basis-inkoop, varianten
    ("steigerhout", "trekker", 6.0, ["nieuw 32x200 mm 250 cm", "nieuw 32x200 mm 300 cm", "nieuw 32x200 mm 400 cm",
                                     "gebruikt 32x200 mm 250 cm", "gebruikt 32x200 mm 300 cm", "gebruikt 32x200 mm 400 cm",
                                     "geschuurd 28x195 mm 250 cm", "balk 70x70 mm 250 cm"]),
    ("plaatmateriaal", "trekker", 14.0, ["OSB/3 12 mm 244x122", "OSB/3 18 mm 244x122", "OSB/3 22 mm 244x59 mes/groef"]),
    ("plaatmateriaal", "kern", 22.0, ["Underlayment 12 mm", "Underlayment 18 mm", "Multiplex berken 12 mm",
                                      "Multiplex berken 18 mm", "Betonplex 15 mm", "Betonplex 18 mm", "MDF 12 mm",
                                      "MDF 18 mm", "MDF vochtwerend 18 mm", "Spaanplaat wit 18 mm", "Hardboard 3 mm",
                                      "Gipsplaat 12,5 mm 260x60", "Tempex 50 mm"]),
    ("constructiehout", "trekker", 3.0, ["vuren 44x69 mm 300 cm", "vuren 44x44 mm 300 cm", "vuren 18x44 mm 300 cm"]),
    ("constructiehout", "kern", 9.0, ["vuren C24 44x119 mm 400 cm", "vuren C24 69x169 mm 400 cm",
                                      "vuren C24 44x169 mm 500 cm", "vuren C24 69x219 mm 500 cm",
                                      "vuren 38x89 mm 360 cm", "vuren 22x100 mm 400 cm", "vuren rabat 18x195 mm 400 cm"]),
    ("tuinhout", "kern", 7.0, ["Douglas plank 22x200 mm 400 cm", "Douglas plank 28x195 mm 400 cm",
                               "Douglas paal 7x7 cm 270 cm", "Douglas paal 9x9 cm 300 cm", "Douglas balk 7x15 cm 400 cm",
                               "Lariks plank 22x150 mm 400 cm", "Grenen geimpregneerd plank 22x145 mm 400 cm",
                               "Grenen paal 9x9 cm 300 cm", "Schutting grenen 180x180 cm", "Tuinscherm Douglas 180x180 cm",
                               "Grenen tuinpaal rond 10 cm 250 cm", "Grenen damwand 32x145 mm 250 cm"]),
    ("vlonderhout", "marge", 13.0, ["Bangkirai 25x145 mm 305 cm", "Bangkirai 21x145 mm 305 cm", "Bangkirai balk 45x70 mm 305 cm",
                                    "Ipe 21x145 mm 305 cm", "Thermo-essen 20x140 mm 300 cm", "Composiet antraciet 23x146 mm 400 cm",
                                    "Composiet grijs 23x146 mm 400 cm", "Grenen vlonder 28x145 mm 400 cm",
                                    "Douglas vlonder 28x145 mm 400 cm"]),
    ("gevelbekleding", "marge", 12.0, ["Zweeds rabat Thermowood 18x165 mm 420 cm", "Channelsiding Thermowood 18x140 mm 420 cm",
                                       "Western Red Cedar rabat 18x140 mm 365 cm", "Lariks rhombus 20x70 mm 400 cm",
                                       "Douglas Zweeds rabat zwart 18x195 mm 400 cm", "Vuren Zweeds rabat zwart 18x195 mm 420 cm",
                                       "Accoya channelsiding 20x140 mm 420 cm", "Fraké rabat 18x150 mm 400 cm"]),
    ("hardhout", "marge", 18.0, ["Meranti 44x69 mm 300 cm", "Meranti 56x69 mm 300 cm", "Eiken balk 70x70 mm 250 cm",
                                 "Eiken plank 25x200 mm 300 cm", "Azobé balk 70x70 mm 300 cm", "Azobé paal 8x8 cm 300 cm",
                                 "Iroko plank 25x150 mm 300 cm", "Bankirai paal 9x9 cm 300 cm"]),
    ("houtbehandeling", "kern", 9.0, ["Beits zwart 2,5 l", "Beits grijs 2,5 l", "Hardhoutolie 2,5 l", "Terrasolie 5 l",
                                      "Grondverf wit 2,5 l", "Houtlijm D3 750 ml"]),
    ("bevestiging", "kern", 6.0, ["Vlonderschroeven RVS 5x50 (200 st)", "Constructieschroeven 6x120 (100 st)",
                                  "Spaanplaatschroeven 4x40 (500 st)", "Paalanker 71 mm", "Paalpunt 71 mm",
                                  "Balkdrager 46x116 mm (25 st)", "Hoekanker 90x90 mm (25 st)"]),
]

CONCURRENTEN = ["gamma", "karwei", "praxis", "hornbach", "bouwmaat", "houthandelonline",
                "gadero", "onlinetuinhout", "opmaatzagen", "bouwbestel"]
# structureel prijsniveau t.o.v. markt
NIVEAU = {"gamma": 1.06, "karwei": 1.08, "praxis": 1.05, "hornbach": 0.97, "bouwmaat": 0.95,
          "houthandelonline": 0.96, "gadero": 1.00, "onlinetuinhout": 0.98, "opmaatzagen": 1.04, "bouwbestel": 0.99}
# welke concurrenten voeren welke categorie
VOERT = {
    "steigerhout": CONCURRENTEN,
    "plaatmateriaal": ["gamma", "karwei", "praxis", "hornbach", "bouwmaat", "houthandelonline", "opmaatzagen", "bouwbestel"],
    "constructiehout": ["gamma", "karwei", "praxis", "hornbach", "bouwmaat", "houthandelonline", "bouwbestel"],
    "tuinhout": ["gamma", "karwei", "praxis", "hornbach", "houthandelonline", "gadero", "onlinetuinhout"],
    "vlonderhout": ["hornbach", "houthandelonline", "gadero", "onlinetuinhout", "praxis", "gamma"],
    "gevelbekleding": ["houthandelonline", "gadero", "onlinetuinhout", "hornbach", "bouwmaat"],
    "hardhout": ["houthandelonline", "gadero", "bouwmaat", "hornbach", "opmaatzagen"],
    "houtbehandeling": ["gamma", "karwei", "praxis", "hornbach", "houthandelonline", "gadero"],
    "bevestiging": ["gamma", "karwei", "praxis", "hornbach", "bouwmaat", "houthandelonline", "gadero"],
}


def genereer(doelmap: Path = Path("data/demo"), dagen: int = 30, seed: int = 7) -> Path:
    rnd = random.Random(seed)
    if doelmap.exists():
        shutil.rmtree(doelmap)
    cfgmap = doelmap / "config"
    cfgmap.mkdir(parents=True)
    # in de demo zijn alle concurrenten 'bereikbaar' (gesimuleerd)
    yaml_tekst = Path("config/concurrenten.yaml").read_text(encoding="utf-8")
    yaml_tekst = "\n".join(r for r in yaml_tekst.splitlines()
                           if not r.strip().startswith(("bereikbaar:", "notitie:")))
    (cfgmap / "concurrenten.yaml").write_text(yaml_tekst + "\n", encoding="utf-8")
    shutil.copy("config/strategie.yaml", cfgmap / "strategie.yaml")

    producten, matches = [], []
    nr = 1000
    for cat, rol, basis, varianten in ASSORTIMENT:
        for naam in varianten:
            nr += 1
            sku = f"HG-{nr}"
            inkoop = round(basis * rnd.uniform(0.6, 2.2), 2)
            markt = inkoop * 1.21 * {"trekker": 1.55, "kern": 1.75, "marge": 2.0}[rol]
            # eigen prijs wijkt bewust af van de markt: sommige te goedkoop, sommige te duur
            eigen = round(markt * rnd.uniform(0.86, 1.14), 2)
            afzet = round(rnd.uniform(*{"trekker": (40, 140), "kern": (8, 45), "marge": (5, 30)}[rol]))
            producten.append(dict(sku=sku, naam=f"{cat.capitalize()} {naam}", categorie=cat, eenheid="stuk",
                                  inkoopprijs_excl_btw=inkoop, verkoopprijs_incl_btw=eigen, rol=rol,
                                  afzet_per_week=afzet, ean="", _markt=markt))
            for c in VOERT[cat]:
                if rnd.random() < 0.8:
                    matches.append(dict(sku=sku, concurrent=c,
                                        url=f"https://demo.invalid/{c}/{sku.lower()}", omrekenfactor=1, opmerking="demo"))

    with open(cfgmap / "producten.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in producten[0] if not k.startswith("_")], extrasaction="ignore")
        w.writeheader()
        w.writerows(producten)
    with open(cfgmap / "matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(matches[0]))
        w.writeheader()
        w.writerows(matches)

    # prijshistorie simuleren: random walk + marktschokken per categorie + acties + af en toe foute match
    db = DB(doelmap / "prijzen.db")
    nu = utcnow().replace(hour=5, minute=0, second=0, microsecond=0)
    prodmap = {p["sku"]: p for p in producten}
    cat_schok = {cat: [1.0] * (dagen + 1) for cat, *_ in ASSORTIMENT}
    for cat in cat_schok:
        niveau = 1.0
        for d in range(dagen + 1):
            if rnd.random() < 0.06:  # inkoopschok (bijv. vurenhout +6%)
                niveau *= rnd.choice([0.95, 1.04, 1.06, 1.08])
            cat_schok[cat][d] = niveau
    for m in matches:
        p = prodmap[m["sku"]]
        c = m["concurrent"]
        prijs = p["_markt"] * NIVEAU[c] * rnd.uniform(0.94, 1.06)
        actie_tot = -1
        fout_match = rnd.random() < 0.015
        for d in range(dagen + 1):
            t = nu - timedelta(days=dagen - d)
            if rnd.random() < 0.05:
                prijs *= rnd.uniform(0.96, 1.05)
            schok = cat_schok[p["categorie"]][d]
            if actie_tot < d and rnd.random() < 0.02:
                actie_tot = d + rnd.randint(3, 10)
            actie = d <= actie_tot
            noteer = prijs * schok * (0.78 if actie else 1)
            if fout_match:
                noteer *= 2.4  # verkeerde variant gekoppeld (bijv. pak i.p.v. stuk)
            db.voeg_observatie_toe(sku=p["sku"], concurrent=c, url=m["url"], prijs_ruw=round(noteer, 2),
                                   prijs_incl=round(noteer, 2),
                                   adviesprijs=round(prijs * schok, 2) if actie and rnd.random() < 0.6 else None,
                                   op_voorraad=rnd.random() > 0.03, status="ok", melding="demo",
                                   tijdstip=t.isoformat())
    db.commit()
    db.close()
    return doelmap
