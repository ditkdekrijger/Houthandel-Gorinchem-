"""Automatisch koppelen van eigen artikelen aan producten van concurrenten.

Uit elke productnaam worden kenmerken gehaald: houtsoort, producttype, behandeling,
doorsnede (mm) en lengte (cm). Een koppeling vereist:
  - dezelfde houtsoortgroep en een verwant producttype
  - dezelfde doorsnede (±2 mm per maat, volgorde maakt niet uit)
  - bij verschillende lengtes: omrekenen per strekkende meter (omrekenfactor), alleen
    als beide lengtes bekend zijn en ze niet meer dan 40% verschillen
Voor niet-houtartikelen (bevestiging, beits, ...) geldt: zelfde getallen + sterke
woordovereenkomst. Elke koppeling krijgt een betrouwbaarheid 0-1; alleen >= drempel
wordt gebruikt. Prijsverschillen > factor 1,67 worden als waarschijnlijk foute koppeling
(andere eenheid/verpakking) afgewezen.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

SOORTEN = [  # (groep, patroon) – volgorde: specifiek eerst
    ("composiet", r"composiet|wpc\b"), ("aluminium", r"aluminium"),
    ("thermo", r"thermo|thermowood|thermogarant"),
    ("accoya", r"accoya"), ("cedar", r"cedar|ceder"), ("frake", r"frak[eé]"),
    ("bangkirai", r"bangkirai|bankirai"), ("azobe", r"azob[eé]"), ("ipe", r"\bip[eé]\b"),
    ("meranti", r"meranti"), ("eiken", r"eiken|eikenhout"), ("keruing", r"keruing"), ("cumaru", r"cumaru"),
    ("robinia", r"robinia"), ("kastanje", r"kastanje"),
    ("lariks", r"lariks(?!.{0,3}douglas)"), ("douglas", r"douglas"),
    ("steigerhout", r"steiger"), ("osb", r"\bosb"), ("underlayment", r"underlayment"),
    ("betonplex", r"betonplex"), ("multiplex", r"multiplex"), ("mdf", r"\bmdf"), ("spaanplaat", r"spaanplaat"),
    ("vuren", r"vuren|vurenhout|spruce"), ("grenen", r"grenen|pine|pinus"),
    ("hardhout", r"hardhout|tropisch"),
]
TYPES = [
    ("vlonder", r"vlonder|terras(plank|deel)|loopdek"),
    ("rabat", r"rabat|siding|potdeksel|rhombus|halfhout|gevelbekleding|ijsselrabat|blokhutprofiel"),
    ("scherm", r"scherm|schutting(?!plank|paal)|tuindeur|poort"),
    ("plaat", r"plaat|platen|underlayment|betonplex|multiplex|\bosb|\bmdf"),
    ("paal", r"paal|palen|pallisade|piket"),
    ("balk", r"balk|ligger|gording|regel|ribben?\b|\blat\b|latten|kantplank(?=.*balk)"),
    ("plank", r"plank|steigerhout|delen\b|dakbeschot|vloerdeel|kantplank"),
]
BEHANDELING = [
    ("geimpregneerd", r"impregne|geïmpregneerd|gewolmaniseerd|groen gei|bruin gei"),
    ("zwart", r"zwart|gedompeld|antraciet"),
    ("geschaafd", r"geschaafd|glad\b|ges\b"),
    ("bezaagd", r"bezaagd|fijnbezaagd|ruw|onbehandeld"),
]
STOP = set("de het een en met voor van in op per stuk stuks mm cm m x tot incl excl btw nieuw hout houten "
           "kopen beste online prijs lengte afmeting kwaliteit fsc pefc mix".split())


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return t.replace("×", "x")


@dataclass
class Kenmerken:
    soort: str | None = None
    type: str | None = None
    behandeling: set = field(default_factory=set)
    doorsnede: tuple | None = None      # mm, gesorteerd
    lengte: float | None = None          # cm
    getallen: frozenset = frozenset()
    woorden: frozenset = frozenset()
    planken: int | None = None           # tuinschermen: aantal planken


def _mm(waarde: float, eenheid: str | None, context_max: float) -> float:
    if eenheid == "mm":
        return waarde
    if eenheid == "cm":
        return waarde * 10
    if eenheid == "m":
        return waarde * 1000
    # zonder eenheid: kleine getallen (<= 30 en een decimaal of de grootste <= 30) zijn cm
    return waarde * 10 if context_max <= 30 else waarde


def kenmerken(titel: str) -> Kenmerken:
    t = _norm(titel)
    k = Kenmerken()
    for g, p in SOORTEN:
        if re.search(p, t):
            k.soort = g
            break
    for g, p in TYPES:
        if re.search(p, t):
            k.type = g
            break
    k.behandeling = {g for g, p in BEHANDELING if re.search(p, t)}

    t2 = re.sub(r"(\d),(\d)", r"\1.\2", t)
    t2 = re.sub(r"centimeters?\b", "cm", t2)
    t2 = re.sub(r"millimeters?\b", "mm", t2)
    t2 = re.sub(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", r"\2", t2)  # rabat 1.2/2.7 -> dikste maat
    # lengte: '300 cm', '300cm', 'lengte 300', '4000 mm' (alleen als laatste maat), '3 m', '3.00 m'
    lengte = None
    m = re.search(r"(?:lengte|lang|l=)\s*(?:ca\.?\s*)?(\d+(?:\.\d+)?)\s*(mm|cm|m)?\b", t2)
    if m:
        lengte = float(m.group(1)) * {"mm": .1, "m": 100}.get(m.group(2), 1)
    # 'a x b x c eenheid'
    m3 = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?", t2)
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm)?\s*x\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?", t2)
    if m3 and k.type == "plaat":
        a, b, c = (float(m3.group(i)) for i in (1, 2, 3))
        f = {"cm": 10, "m": 1000}.get(m3.group(4), 1)
        k.doorsnede = tuple(sorted((a * f, b * f, c * f)))
    elif m3:
        a, b, c = (float(m3.group(i)) for i in (1, 2, 3))
        e = m3.group(4)
        if e == "mm" or (e is None and c >= 1000):
            k.doorsnede = tuple(sorted((a, b)))
            lengte = lengte or c / 10
        elif e == "cm" and max(a, b) <= 30:
            k.doorsnede = tuple(sorted((a * 10, b * 10)))
            lengte = lengte or c
        elif e == "cm":
            k.doorsnede = tuple(sorted((a * 10, b * 10)))
            lengte = lengte or c
        else:
            k.doorsnede = tuple(sorted((a, b)))
            lengte = lengte or (c / 10 if c > 600 else c)
    elif m2:
        a, b = float(m2.group(1)), float(m2.group(3))
        e = m2.group(4) or m2.group(2)
        mx = max(a, b)
        k.doorsnede = tuple(sorted((_mm(a, e, mx), _mm(b, e, mx))))
        if k.type == "plaat" and max(k.doorsnede) > 500:  # plaatformaat 244x122 -> geen doorsnede
            plaat = k.doorsnede
            dikte = re.search(r"(\d+(?:\.\d+)?)\s*mm", t2.replace(m2.group(0), ""))
            k.doorsnede = (float(dikte.group(1)), *plaat) if dikte else plaat
    if lengte is None:
        rest = t2
        for mm_ in (m3, m2):
            if mm_:
                rest = rest.replace(mm_.group(0), " ")
                break
        m = re.search(r"(?<![\dx.])(\d{3,4})\s*(cm|mm)\b(?!\s*x)", rest)
        if m:
            lengte = float(m.group(1)) * (.1 if m.group(2) == "mm" else 1)
        else:
            m = re.search(r"(?<![\dx.])(\d(?:\.\d{1,2})?)\s*m(?:eter|1)?\b", rest)
            if m and 1 <= float(m.group(1)) <= 8:
                lengte = float(m.group(1)) * 100
    k.lengte = lengte if lengte and 50 <= lengte <= 1000 else None
    m = re.search(r"(\d{1,2})\s*-?\s*planks?\b", t)
    k.planken = int(m.group(1)) if m else None
    k.getallen = frozenset(re.findall(r"\d+(?:\.\d+)?", t2))
    k.woorden = frozenset(w for w in re.findall(r"[a-z]{3,}", t) if w not in STOP)
    return k


SCHERM_ALGEMEEN = set("""tuinscherm scherm schutting schuttingen tuinpoort poort poortdeur tuindeur douglas grenen vuren
    hardhout lariks geimpregneerd groen zwart bruin geschaafd fijnbezaagd bezaagd planks planken rvs geschroefd
    verloop toog toogscherm recht dicht frame verzinkt compleet afbouwscherm enkelzijdig dubbelzijdig bekleed
    gespoten carpgarant red class wood hout houten stalen deur poortdeuren tuinpoorten combi""".split())

VERWANT = {("plank", "vlonder"), ("plank", "rabat"), ("balk", "paal"), ("plank", "balk")}


def _types_ok(a, b) -> bool:
    return a == b or (a, b) in VERWANT or (b, a) in VERWANT


def vergelijk(eigen: Kenmerken, ander: Kenmerken) -> tuple[float, float] | None:
    """-> (betrouwbaarheid, omrekenfactor) of None."""
    if eigen.soort or ander.soort:
        # houtproduct: strenge regels
        if eigen.soort != ander.soort or not eigen.doorsnede or not ander.doorsnede:
            return None
        if eigen.type and ander.type and not _types_ok(eigen.type, ander.type):
            return None
        if len(eigen.doorsnede) != len(ander.doorsnede):
            return None
        # dunne maat (planken): max 1,5 mm verschil; overige maten: max(2 mm, 4%)
        if any(abs(x - y) > (1.5 if x < 40 else max(2.0, 0.04 * x)) for x, y in zip(eigen.doorsnede, ander.doorsnede)):
            return None
        score = 0.7
        if eigen.type == ander.type:
            score += 0.1
        gedeeld = eigen.behandeling & ander.behandeling
        tegen = ("geimpregneerd" in eigen.behandeling) != ("geimpregneerd" in ander.behandeling) or \
                ("zwart" in eigen.behandeling) != ("zwart" in ander.behandeling)
        if tegen:
            return None
        score += 0.05 * len(gedeeld)
        if eigen.type == "scherm" or ander.type == "scherm":
            # schermen/poorten zijn merk-/ontwerpgebonden: zelfde aantal planken vereist voor een volle score
            # zelfde aantal planken of dezelfde modelnaam (bv. 'Oostende'), anders geen koppeling
            model = (eigen.woorden & ander.woorden) - SCHERM_ALGEMEEN
            if not ((eigen.planken and eigen.planken == ander.planken) or model):
                return None
        score = round(score, 3)
        if eigen.type == "scherm" and ander.type == "scherm":
            return (score, 1.0)  # afmeting (b x h) zit al in de doorsnede
        if eigen.type == "plaat":
            return (min(score + 0.1, 1.0), 1.0)
        if eigen.lengte and ander.lengte:
            factor = eigen.lengte / ander.lengte
            if abs(eigen.lengte - ander.lengte) < 1:
                return (min(round(score + 0.15, 3), 1.0), 1.0)
            if 0.7 <= factor <= 1.43:
                return (round(score - 0.05, 3), round(factor, 4))
            return None
        if not eigen.lengte and not ander.lengte:
            return (round(score - 0.1, 3), 1.0)
        return None
    # overige artikelen: zelfde getallen + woordoverlap
    if not eigen.getallen or eigen.getallen != ander.getallen:
        return None
    if not eigen.woorden or not ander.woorden:
        return None
    j = len(eigen.woorden & ander.woorden) / len(eigen.woorden | ander.woorden)
    return (round(0.5 + j * 0.5, 3), 1.0) if j >= 0.5 else None


def koppel(eigen: list[dict], aanbod: list[dict], drempel: float = 0.7) -> list[dict]:
    """eigen: [{'sku','naam','prijs'}], aanbod: [{'concurrent','titel','url','prijs_incl',...}]"""
    ek = [(e, kenmerken(e["naam"])) for e in eigen]
    index = defaultdict(list)          # (soort, eerste maat) -> aanbod
    rest = defaultdict(list)            # concurrent -> niet-hout
    for a in aanbod:
        k = kenmerken(a["titel"])
        if k.soort and k.doorsnede:
            index[(k.soort, round(k.doorsnede[0]))].append((a, k))
        elif not k.soort:
            rest[a["concurrent"]].append((a, k))
    beste: dict[tuple, dict] = {}
    for e, k in ek:
        if k.soort and k.doorsnede:
            kand = [x for d in (-2, -1, 0, 1, 2) for x in index.get((k.soort, round(k.doorsnede[0]) + d), [])]
        elif not k.soort and k.getallen:
            kand = [x for lijst in rest.values() for x in lijst if x[1].getallen == k.getallen]
        else:
            kand = []
        for a, ak in kand:
            r = vergelijk(k, ak)
            if not r or r[0] < drempel:
                continue
            score, factor = r
            prijs_eenheid = a["prijs_incl"] * factor
            ratio = prijs_eenheid / e["prijs"] if e["prijs"] else 0
            if not 0.6 <= ratio <= 1.67:  # groter verschil = vrijwel altijd andere verpakking/uitvoering
                continue
            sleutel = (e["sku"], a["concurrent"])
            # voorkeur: hoogste score, dan kleinste lengteverschil
            rang = (score, -abs(1 - factor))
            if sleutel not in beste or rang > beste[sleutel]["_rang"]:
                beste[sleutel] = {"sku": e["sku"], "concurrent": a["concurrent"], "url": a["url"],
                                  "omrekenfactor": factor, "betrouwbaarheid": round(score, 3),
                                  "titel_concurrent": a["titel"], "prijs_incl": a["prijs_incl"],
                                  "op_voorraad": a.get("op_voorraad"), "adviesprijs": a.get("adviesprijs"),
                                  "_rang": rang}
    for v in beste.values():
        v.pop("_rang")
    return list(beste.values())
