"""Marktreferentie voor ÉLK artikel, op het best haalbare niveau.

Niet elk artikel heeft bij concurrenten een exact gelijk product. In plaats van die artikelen
'blind' te laten, krijgt elk artikel een marktprijs met een niveau en een betrouwbaarheid:

  A  exact          zelfde product en maat (matcher.koppel)                      betrouwbaarheid 0,75-1
  B  gelijkwaardig  zelfde doorsnede/plaat in een andere lengte of maat, omgerekend
                    per strekkende meter / m2; of merkartikel met dezelfde specificaties
                    (zeldzame woorden + gelijke getallen)                          0,55-0,75
  C  marktmodel     prijs afgeleid uit het dichtstbijzijnde vergelijkbare aanbod:
                    hout per m3 (doorsnede en lengte geschaald), bouwpakketten
                    (overkapping, tuinhuis, ...) per m2                            0,3-0,55
  D  categorie      geen vergelijkbaar product: prijsindex van de gekoppelde
                    artikelen in dezelfde categorie (anders: rol, anders: totaal)  0,15-0,25

A, B en C worden per concurrent als observatie opgeslagen (kolom `niveau`) en wegen in de
prijsengine minder zwaar naarmate het niveau lager is. D is alleen voor inzicht en de
impactberekening; daar worden nooit prijsvoorstellen op gebaseerd.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict

from .matcher import _norm, _types_ok, kenmerken

# ---------------------------------------------------------------- bouwpakketten
PAKKET = [
    ("overkapping", r"overkapping|veranda|terrasoverkapping|carport|lounge ?overkapping"),
    ("tuinhuis", r"tuinhuis|blokhut|kapschuur|buitenverblijf|tuinkamer|schuur(?!deur)|chalet|poolhouse"),
    ("sauna", r"sauna"),
    ("tafelblad", r"tafelblad|boomstamblad|blad\b|deens ovaal|ovaal \d"),
    ("tafel", r"eettafel|boomstamtafel|tuintafel|picknicktafel|tafel\b"),
]
MIN_M2 = {"tafelblad": 0.5, "tafel": 0.5}


def pakket(titel: str) -> tuple[str, float] | None:
    """('overkapping', m2) voor bouwpakketten met een maat in de titel. Muur-/aanbouwvarianten
    zijn een eigen groep (veel goedkoper per m2 dan vrijstaand)."""
    t = _norm(titel)
    soort = next((g for g, p in PAKKET if re.search(p, t)), None)
    if not soort:
        return None
    if re.search(r"muur|aanbouw|lean|wandmontage", t):
        soort += "-muur"
    t2 = re.sub(r"(\d),(\d)", r"\1.\2", t)
    m = re.search(r"(\d{3,4}(?:\.\d)?)\s*x\s*(\d{3,4}(?:\.\d)?)\s*(cm|mm)?", t2)
    if m:
        f = 0.1 if m.group(3) == "mm" else 1.0
        a, b = float(m.group(1)) * f, float(m.group(2)) * f
    else:
        m = re.search(r"\b(\d{1,2}(?:\.\d)?)\s*x\s*(\d{1,2}(?:\.\d)?)\s*(m|meter)?\b", t2)
        if not m:
            return None
        a, b = float(m.group(1)) * 100, float(m.group(2)) * 100
    m2 = a * b / 10000
    basis = soort.split("-")[0]
    return (soort, m2) if MIN_M2.get(basis, 2) <= m2 <= 120 else None


# ---------------------------------------------------------------- hulpfuncties
def _conflict(a, b) -> bool:
    return ("geimpregneerd" in a.behandeling) != ("geimpregneerd" in b.behandeling) or \
           ("zwart" in a.behandeling) != ("zwart" in b.behandeling)


def _ok_prijs(voorspeld: float, eigen: float, marge: tuple[float, float]) -> bool:
    return eigen > 0 and marge[0] <= voorspeld / eigen <= marge[1]


def _idf(docs: list[frozenset]) -> dict[str, float]:
    df = defaultdict(int)
    for d in docs:
        for w in d:
            df[w] += 1
    n = len(docs) or 1
    return {w: math.log(n / (1 + c)) for w, c in df.items()}


# ---------------------------------------------------------------- B/C: hout en platen
MEUBEL = re.compile(r"tafel|blad\b|bank\b|poot|kast|meubel|stoel|kruk|plantenbak|bloembak|sauna|kozijn|raam|deur", re.I)


def _hout_kandidaat(k, ak) -> tuple[str, float, float] | None:
    """-> (niveau, betrouwbaarheid, schaalfactor) waarmee de concurrentprijs naar ons artikel
    wordt omgerekend, of None."""
    if k.soort != ak.soort or not ak.doorsnede or len(ak.doorsnede) != len(k.doorsnede):
        return None
    # producttype moet bij beide bekend en verwant zijn (geen plank tegen tafelblad)
    if not (k.type and ak.type and _types_ok(k.type, ak.type)):
        return None
    if _conflict(k, ak):
        return None
    if k.type == "plaat" or len(k.doorsnede) == 3:
        # plaat: zelfde dikte (±1 mm), schalen naar oppervlak
        if len(k.doorsnede) != 3 or abs(k.doorsnede[0] - ak.doorsnede[0]) > 1:
            return None
        opp, aopp = k.doorsnede[1] * k.doorsnede[2], ak.doorsnede[1] * ak.doorsnede[2]
        f = opp / aopp
        if not 0.33 <= f <= 3:
            return None
        if abs(f - 1) < 0.03:
            return ("B", 0.75, 1.0)
        return ("B", 0.6, f)
    if max(k.doorsnede) > 400 or max(ak.doorsnede) > 400:
        return None  # geen balk/plank/paal (schermen, panelen): niet via het hout-model
    # afstand in doorsnede (log-schaal): dikte en breedte
    d = abs(math.log(k.doorsnede[0] / ak.doorsnede[0])) + abs(math.log(k.doorsnede[1] / ak.doorsnede[1]))
    if d > 0.3 or (d >= 0.06 and k.type != ak.type):  # model alleen binnen hetzelfde producttype
        return None
    if k.lengte and ak.lengte:
        lf = k.lengte / ak.lengte
        if not 0.4 <= lf <= 2.5:
            return None
    elif not k.lengte and not ak.lengte:
        lf = 1.0
    else:
        return None
    oppf = (k.doorsnede[0] * k.doorsnede[1]) / (ak.doorsnede[0] * ak.doorsnede[1])
    schaal = lf * oppf ** 0.9
    if d < 0.06:  # zelfde doorsnede, andere lengte
        return ("B", round(0.72 - 0.1 * abs(math.log(lf)), 3), schaal)
    return ("C", round(0.55 - 0.6 * d, 3), schaal)


def hout_en_platen(eigen, aanbod_k, al_gekoppeld) -> list[dict]:
    per_soort = defaultdict(list)
    for a, ak in aanbod_k:
        if ak.soort and ak.doorsnede and not MEUBEL.search(a["titel"]):
            per_soort[ak.soort].append((a, ak))
    uit = []
    for e, k in eigen:
        if not (k.soort and k.doorsnede) or pakket(e["naam"]) or MEUBEL.search(e["naam"]):
            continue
        beste: dict[str, tuple] = {}
        for a, ak in per_soort.get(k.soort, []):
            if (e["sku"], a["concurrent"]) in al_gekoppeld:
                continue
            r = _hout_kandidaat(k, ak)
            if not r or r[1] < 0.3:
                continue
            voorspeld = a["prijs_incl"] * r[2]
            if not _ok_prijs(voorspeld, e["prijs"], (0.55, 1.8)):
                continue
            if a["concurrent"] not in beste or r[1] > beste[a["concurrent"]][1][1]:
                beste[a["concurrent"]] = (a, r, voorspeld)
        for c, (a, r, voorspeld) in beste.items():
            uit.append({"sku": e["sku"], "concurrent": c, "url": a["url"], "niveau": r[0],
                        "betrouwbaarheid": r[1], "omrekenfactor": round(r[2], 4),
                        "prijs_incl": a["prijs_incl"], "titel_concurrent": a["titel"],
                        "op_voorraad": a.get("op_voorraad"), "adviesprijs": a.get("adviesprijs")})
    return uit


# ---------------------------------------------------------------- C: bouwpakketten per m2
def bouwpakketten(eigen, aanbod, al_gekoppeld) -> list[dict]:
    per = defaultdict(list)  # (pakketsoort, houtsoort) -> [(a, m2)]
    for a in aanbod:
        p = pakket(a["titel"])
        if p:
            hs = kenmerken(a["titel"]).soort or "onbekend"
            per[(p[0], hs)].append((a, p[1]))
    uit = []
    for e, k in eigen:
        p = pakket(e["naam"])
        if not p:
            continue
        hs = (k.soort or "douglas") if p[0] == "overkapping" else (k.soort or "onbekend")
        bronnen = per.get((p[0], hs)) or per.get((p[0], "onbekend"), [])
        per_conc = defaultdict(list)
        for a, m2 in bronnen:
            if 0.75 <= p[1] / m2 <= 1.33 and (e["sku"], a["concurrent"]) not in al_gekoppeld:
                per_conc[a["concurrent"]].append((a, m2))
        for c, lijst in per_conc.items():
            if len(lijst) < 3:
                continue
            # basisuitvoering: goedkoopste kwart per m2 (opties als deuren, wanden en dakbedekking
            # drijven de duurdere configuraties op)
            pm2s = sorted(a["prijs_incl"] / m2 for a, m2 in lijst)
            pm2 = pm2s[len(pm2s) // 4]
            voorspeld = pm2 * p[1]
            if not _ok_prijs(voorspeld, e["prijs"], (0.5, 2.0)):
                continue
            a, m2 = min(lijst, key=lambda x: abs(x[1] - p[1]))
            uit.append({"sku": e["sku"], "concurrent": c, "url": a["url"], "niveau": "C",
                        "betrouwbaarheid": round(0.35 + min(0.1, 0.02 * len(lijst)), 3),
                        "omrekenfactor": round(voorspeld / a["prijs_incl"], 4), "prijs_incl": a["prijs_incl"],
                        "titel_concurrent": f"{len(lijst)}x {p[0]} ±{p[1]:.0f} m², basis (p25) € {pm2:.0f}/m² (o.a. {a['titel'][:60]})",
                        "op_voorraad": None, "adviesprijs": None})
    return uit


# ---------------------------------------------------------------- B: merkartikelen / overige
def merkartikelen(eigen, aanbod_k, al_gekoppeld) -> list[dict]:
    # alle aanbod zonder bruikbare maatvoering (niet-hout, of hout zonder doorsnede: deuren, ramen, beits)
    rest = [(a, ak) for a, ak in aanbod_k if ak.woorden and not (ak.soort and ak.doorsnede)]
    idf = _idf([ak.woorden for _, ak in rest] + [k.woorden for _, k in eigen])
    inv = defaultdict(list)
    for i, (a, ak) in enumerate(rest):
        for w in ak.woorden:
            if idf.get(w, 0) > 4.0:  # alleen onderscheidende woorden indexeren
                inv[w].append(i)
    uit = []
    for e, k in eigen:
        if not k.woorden or (k.soort and k.doorsnede) or pakket(e["naam"]):
            continue
        zeldzaam = sorted(k.woorden, key=lambda w: -idf.get(w, 0))[:3]
        kand = {i for w in zeldzaam for i in inv.get(w, [])}
        norm_e = math.sqrt(sum(idf.get(w, 0) ** 2 for w in k.woorden)) or 1
        beste = {}
        for i in kand:
            a, ak = rest[i]
            if (e["sku"], a["concurrent"]) in al_gekoppeld or (k.soort or ak.soort) and k.soort != ak.soort:
                continue
            # getallen: gelijk, of al onze getallen komen voor en er is hooguit één extra (bv. '2st')
            if k.getallen != ak.getallen and not (k.getallen and k.getallen <= ak.getallen
                                                  and len(ak.getallen - k.getallen) <= 1):
                continue
            gedeeld = k.woorden & ak.woorden
            cos = sum(idf.get(w, 0) ** 2 for w in gedeeld) / (
                norm_e * (math.sqrt(sum(idf.get(w, 0) ** 2 for w in ak.woorden)) or 1))
            if cos < 0.5:
                continue
            score = round(0.35 + 0.4 * cos - (0.05 if k.getallen != ak.getallen else 0), 3)
            if not _ok_prijs(a["prijs_incl"], e["prijs"], (0.6, 1.67)):
                continue
            if a["concurrent"] not in beste or score > beste[a["concurrent"]][1]:
                beste[a["concurrent"]] = (a, score)
        for c, (a, score) in beste.items():
            if score >= 0.55:
                uit.append({"sku": e["sku"], "concurrent": c, "url": a["url"], "niveau": "B",
                            "betrouwbaarheid": score, "omrekenfactor": 1.0, "prijs_incl": a["prijs_incl"],
                            "titel_concurrent": a["titel"], "op_voorraad": a.get("op_voorraad"),
                            "adviesprijs": a.get("adviesprijs")})
    return uit


# ---------------------------------------------------------------- D: categorie-index
def producttype(naam: str) -> str | None:
    """Grove productsoort uit de naam (los van de webshopcategorie, die soms afwijkt)."""
    b = bevestiging_spec(naam)
    if b:
        return "bevestiging:" + b[0]
    pk = pakket(naam)
    if pk:
        return "pakket:" + pk[0]
    k = kenmerken(naam)
    if k.soort and k.type:
        return f"hout:{k.soort}:{k.type}"
    return None


def categorie_index(producten: list[dict], index_per_sku: dict[str, float], min_n: int = 5) -> list[dict]:
    """producten: [{'sku','naam','categorie','rol','prijs'}]; index_per_sku: gemeten index (eigen/markt).
    Volgorde: zelfde producttype (bv. alle gekoppelde spaanplaatschroeven), dan de categorie van
    specifiek naar breed, dan de rol, dan alles."""
    groepen: dict[str, list[float]] = defaultdict(list)
    for p in producten:
        i = index_per_sku.get(p["sku"])
        if i is None:
            continue
        pt = producttype(p["naam"])
        if pt:
            groepen["type:" + pt].append(i)
        delen = [c.strip() for c in (p["categorie"] or "").split(">") if c.strip()]
        for n in range(1, len(delen) + 1):
            groepen["cat:" + " > ".join(delen[:n])].append(i)
        groepen["rol:" + p["rol"]].append(i)
        groepen["alles"].append(i)
    uit = []
    for p in producten:
        if p["sku"] in index_per_sku:
            continue
        delen = [c.strip() for c in (p["categorie"] or "").split(">") if c.strip()]
        pt = producttype(p["naam"])
        sleutels = (["type:" + pt] if pt else []) + \
            ["cat:" + " > ".join(delen[:n]) for n in range(len(delen), 0, -1)] + ["rol:" + p["rol"], "alles"]
        for diepte, s in enumerate(sleutels):
            waarden = groepen.get(s, [])
            if len(waarden) >= min_n:
                idx = statistics.median(waarden)
                uit.append({"sku": p["sku"], "niveau": "D", "marktprijs": round(p["prijs"] / idx, 2),
                            "bronnen": len(waarden),
                            "betrouwbaarheid": round(max(0.1, 0.25 - 0.05 * (diepte - (1 if pt else 0))), 3) if diepte or not pt else 0.3,
                            "toelichting": f"index {idx * 100:.0f} van {len(waarden)} gekoppelde artikelen "
                                           + (f"van hetzelfde type ({s.split(':')[-1]})" if s.startswith("type:")
                                              else f"in {s.split(':', 1)[-1]}")})
                break
    return uit


def bepaal_niveau(observaties: list[dict]) -> str | None:
    niveaus = {o.get("niveau") or "A" for o in observaties}
    for n in ("A", "B", "C"):
        if n in niveaus:
            return n
    return None


# ---------------------------------------------------------------- B/C: bevestiging per stuk
BEVESTIGING = [
    ("houtdraadbout", r"houtdraadbout|tirefond"),
    ("slotbout", r"slotbout"),
    ("vlonderschroef", r"vlonderschroef|terrasschroef"),
    ("gevelschroef", r"gevelschroef"),
    ("constructieschroef", r"flenskop|constructieschroef|tellerkop|schotelkop|zeskantschroef"),
    ("spaanplaatschroef", r"spaanplaatschroef|universele? schroef|torx schroef|schroeven\b|schroef\b"),
]


def bevestiging_spec(titel: str) -> tuple | None:
    """-> (soort, diameter, lengte, rvs, aantal) of None."""
    t = _norm(titel)
    soort = next((g for g, p in BEVESTIGING if re.search(p, t)), None)
    if not soort or re.search(r"plug|bit\b|bitje|sleutel|boor|set\b|assortiment", t):
        return None
    if "schroef" in soort and re.search(r"metaalschroef|machineschroef|\bm\d{1,2}\s*x", t):
        soort += "-metaal"  # metrische schroef is geen houtschroef van dezelfde maat
    t2 = re.sub(r"(\d) 0 x", r"\1.0 x", t)
    t2 = re.sub(r"(\d),(\d)", r"\1.\2", t2)
    maten = re.findall(r"m?(\d{1,2}(?:\.\d)?)\s*x\s*(\d{2,3})(?!\s*-)\s*(?:mm)?", t2)
    if not maten:
        return None
    d, l = float(maten[-1][0]), float(maten[-1][1])  # laatste maat = de variant
    m = re.search(r"(\d{1,4})\s*(?:st\b|st\.|stuks?\b)", t2)
    if not m and re.search(r"per doos|doos|pak\b|verpakking", t2):
        return None  # aantal per verpakking onbekend: niet per stuk te vergelijken
    aantal = int(m.group(1)) if m else 1
    return (soort, d, l, bool(re.search(r"\brvs\b|roestvast|a2\b|a4\b", t2)), max(1, aantal))


def bevestiging(eigen, aanbod, al_gekoppeld) -> list[dict]:
    index = defaultdict(list)
    for a in aanbod:
        s = bevestiging_spec(a["titel"])
        if s:
            index[s[:4]].append((a, s))
    uit = []
    for e, _ in eigen:
        s = bevestiging_spec(e["naam"])
        if not s:
            continue
        beste = {}
        for a, sa in index.get(s[:4], []):
            if (e["sku"], a["concurrent"]) in al_gekoppeld:
                continue
            factor = s[4] / sa[4]
            voorspeld = a["prijs_incl"] * factor
            if not _ok_prijs(voorspeld, e["prijs"], (0.4, 2.5)):
                continue
            # zelfde verpakking = gelijkwaardig; andere verpakking = per stuk omgerekend (staffel verschilt)
            score = 0.65 if factor == 1 else 0.45 - min(0.1, 0.03 * abs(math.log(factor)))
            if a["concurrent"] not in beste or score > beste[a["concurrent"]][1]:
                beste[a["concurrent"]] = (a, score, factor)
        for c, (a, score, factor) in beste.items():
            uit.append({"sku": e["sku"], "concurrent": c, "url": a["url"], "niveau": "B" if factor == 1 else "C",
                        "betrouwbaarheid": round(score, 3), "omrekenfactor": round(factor, 4),
                        "prijs_incl": a["prijs_incl"], "titel_concurrent": a["titel"],
                        "op_voorraad": a.get("op_voorraad"), "adviesprijs": a.get("adviesprijs")})
    return uit


# ---------------------------------------------------------------- C: profielen met alleen een breedte
def _breedte(titel: str, k) -> float | None:
    if k.doorsnede or k.type not in ("rabat", "plank", "vlonder"):
        return None
    m = re.search(r"(\d{2,3})\s*mm", re.sub(r"(\d),(\d)", r"\1.\2", _norm(titel)))
    return float(m.group(1)) if m and 60 <= float(m.group(1)) <= 300 else None


def breedteprofielen(eigen, aanbod_k, al_gekoppeld) -> list[dict]:
    per = defaultdict(list)
    for a, ak in aanbod_k:
        if ak.soort and ak.type and ak.doorsnede and len(ak.doorsnede) == 2 and ak.lengte:
            per[(ak.soort, ak.type)].append((a, ak))
    uit = []
    for e, k in eigen:
        b = _breedte(e["naam"], k)
        if not b or not k.soort or not k.lengte:
            continue
        beste = {}
        for a, ak in per.get((k.soort, k.type), []):
            if (e["sku"], a["concurrent"]) in al_gekoppeld or _conflict(k, ak):
                continue
            if abs(ak.doorsnede[1] - b) > 6:
                continue
            lf = k.lengte / ak.lengte
            if not 0.5 <= lf <= 2:
                continue
            voorspeld = a["prijs_incl"] * lf
            if not _ok_prijs(voorspeld, e["prijs"], (0.55, 1.8)):
                continue
            score = 0.42 - 0.05 * abs(math.log(lf))
            if a["concurrent"] not in beste or score > beste[a["concurrent"]][1]:
                beste[a["concurrent"]] = (a, score, lf)
        for c, (a, score, lf) in beste.items():
            uit.append({"sku": e["sku"], "concurrent": c, "url": a["url"], "niveau": "C",
                        "betrouwbaarheid": round(score, 3), "omrekenfactor": round(lf, 4),
                        "prijs_incl": a["prijs_incl"], "titel_concurrent": a["titel"],
                        "op_voorraad": a.get("op_voorraad"), "adviesprijs": a.get("adviesprijs")})
    return uit
