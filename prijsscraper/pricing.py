"""Prijsengine: van concurrentprijzen naar ~20 onderbouwde prijsvoorstellen per dag.

Per artikel:
  1. Verzamel verse concurrentprijzen (per eigen eenheid, incl. btw).
  2. Filter uitschieters (verkeerde match) en weeg tijdelijke acties lager.
  3. Bepaal de marktreferentie (gewogen p25/mediaan/...) volgens de rol van het artikel.
  4. Doelprijs = referentie x (1 + index), begrensd door margebodem en plafond.
  5. Stap max. X% richting doel, afronden op psychologische prijspunten.
  6. Schat de impact op brutowinst per week (prijselasticiteit per rol).
  7. Rangschik alle kandidaten en kies de beste N, met een minimum aan verhogingen
     én verlagingen zodat zowel marge als concurrentiepositie bewaakt worden.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Config, Product
from .db import DB, utcnow


@dataclass
class ConcurrentPrijs:
    concurrent: str
    naam: str
    prijs: float
    gewicht: float
    actie: bool = False
    uitschieter: bool = False
    op_voorraad: bool | None = None
    url: str = ""
    niveau: str = "A"          # A exact | B gelijkwaardig | C marktmodel


@dataclass
class Voorstel:
    sku: str
    naam: str
    categorie: str
    rol: str
    huidige_prijs: float
    voorgestelde_prijs: float
    richting: str
    wijziging_pct: float
    doelprijs: float
    referentieprijs: float
    laagste: float
    mediaan: float
    hoogste: float
    aantal_concurrenten: int
    positie_oud: int
    positie_nieuw: int
    marge_oud: float
    marge_nieuw: float
    brutowinst_delta_week: float
    score: float
    reden: str
    marktbeweging_7d: float | None = None
    concurrenten: list[ConcurrentPrijs] = field(default_factory=list)
    id: int | None = None


@dataclass
class Signaal:
    sku: str
    soort: str          # uitschieter | onder_bodem | actie | te_weinig_data
    tekst: str


@dataclass
class Analyse:
    voorstellen: list[Voorstel]
    kandidaten: int
    geanalyseerd: int
    signalen: list[Signaal]


# Gewicht per koppelniveau: afgeleide prijzen tellen minder zwaar mee
NIVEAU_GEWICHT = {"A": 1.0, "B": 0.75, "C": 0.5}

# ------------------------------------------------------------------ hulpfuncties
def gewogen_kwantiel(waarden: list[tuple[float, float]], q: float) -> float:
    """Gewogen kwantiel met lineaire interpolatie tussen gewichtsmiddelpunten.
    waarden = [(prijs, gewicht)]. Zwaarder wegende concurrenten trekken de referentie naar zich toe."""
    w = sorted((p, g) for p, g in waarden if g > 0)
    if not w:
        raise ValueError("geen waarden")
    totaal = sum(g for _, g in w)
    punten, cum = [], 0.0
    for p, g in w:
        punten.append(((cum + g / 2) / totaal, p))
        cum += g
    if q <= punten[0][0]:
        return punten[0][1]
    if q >= punten[-1][0]:
        return punten[-1][1]
    for (q0, p0), (q1, p1) in zip(punten, punten[1:]):
        if q0 <= q <= q1:
            return p0 + (p1 - p0) * (q - q0) / (q1 - q0)
    return punten[-1][1]


REFERENTIE_Q = {"min": 0.0, "p25": 0.25, "mediaan": 0.5, "p75": 0.75}


def referentie(prijzen: list[ConcurrentPrijs], soort: str) -> float:
    geldig = [c for c in prijzen if not c.uitschieter]
    if soort == "min":
        return min(c.prijs for c in geldig if not c.actie) if any(not c.actie for c in geldig) \
            else min(c.prijs for c in geldig)
    return gewogen_kwantiel([(c.prijs, c.gewicht) for c in geldig], REFERENTIE_Q[soort])


def prijspunten(rond: float, methode: str) -> list[float]:
    basis = math.floor(rond)
    if rond < 10:
        eindes = (0.25, 0.49, 0.75, 0.95, 0.99)
    elif rond < 100:
        eindes = (0.49, 0.95, 0.99)
    else:
        eindes = (0.95,)
    return sorted({round(b + e, 2) for b in (basis - 1, basis, basis + 1) for e in eindes if b + e > 0})


def rond_af(prijs: float, methode: str = "psychologisch", richting: str | None = None) -> float:
    """Psychologische prijspunten. Onder €10: .25/.49/.75/.95/.99, onder €100: .49/.95/.99,
    daarboven hele euro's -0,05 (bijv. 129,95).

    richting='omlaag' -> hoogste prijspunt <= prijs; 'omhoog' -> laagste prijspunt >= prijs;
    None -> dichtstbijzijnde. De engine rondt altijd af richting de huidige prijs, zodat
    afronding nooit een grotere stap oplevert dan bedoeld."""
    if methode == "geen":
        return round(prijs, 4)
    if methode == "cent" or prijs < 2:
        # kleine prijzen (bouten, schroeven per stuk): prijspunten liggen te ver uit elkaar -> centen
        if richting == "omlaag":
            return math.floor(prijs * 100 + 1e-6) / 100
        if richting == "omhoog":
            return math.ceil(prijs * 100 - 1e-6) / 100
        return round(prijs, 2)
    k = prijspunten(prijs, methode)
    if richting == "omlaag":
        return max((x for x in k if x <= prijs + 1e-9), default=k[0])
    if richting == "omhoog":
        return min((x for x in k if x >= prijs - 1e-9), default=k[-1])
    return min(k, key=lambda x: (abs(x - prijs), x))


def marge(prijs_incl: float, inkoop_excl: float, btw: float) -> float:
    ex = prijs_incl / (1 + btw)
    return (ex - inkoop_excl) / ex if ex else 0.0


def bodemprijs(p: Product, min_marge: float, btw: float) -> float:
    return p.inkoopprijs_excl_btw / (1 - min_marge) * (1 + btw)


def _eur(x: float) -> str:
    return f"€ {x:.2f}".replace(".", ",")


def positie(prijs: float, concurrenten: list[float]) -> int:
    """1 = goedkoopste."""
    return 1 + sum(1 for c in concurrenten if c < prijs - 0.005)


# ------------------------------------------------------------------ kern
class PrijsEngine:
    def __init__(self, cfg: Config, db: DB, nu: datetime | None = None):
        self.cfg = cfg
        self.db = db
        self.s = cfg.strategie
        self.alg = self.s["algemeen"]
        self.kw = self.s["kwaliteit"]
        self.nu = nu or utcnow()

    def _concurrentprijzen(self, sku: str, referentie_tijd: datetime | None = None) -> list[ConcurrentPrijs]:
        ref = referentie_tijd or self.nu
        obs = self.db.laatste_observaties(sku, self.alg["max_leeftijd_uur"], ref)
        res = []
        for o in obs:
            c = self.cfg.concurrenten.get(o["concurrent"])
            if not c or not o["prijs_incl"]:
                continue
            if o["op_voorraad"] == 0 and not self.kw.get("niet_op_voorraad_meetellen", False):
                continue
            niveau = o.get("niveau") or "A"
            cp = ConcurrentPrijs(concurrent=c.id, naam=c.naam, prijs=o["prijs_incl"],
                                 gewicht=c.gewicht * NIVEAU_GEWICHT.get(niveau, 0.5),
                                 op_voorraad=None if o["op_voorraad"] is None else bool(o["op_voorraad"]),
                                 url=o["url"] or "", niveau=niveau)
            # Actiedetectie: doorgestreepte prijs of flink onder eigen 30-dagen mediaan
            drempel = self.kw["actie_drempel"]
            if o["adviesprijs"] and o["prijs_incl"] < o["adviesprijs"] * (1 - drempel / 2):
                cp.actie = True
            else:
                hist = [p for _, p in self.db.historie(sku, c.id, 30, ref)]
                if len(hist) >= 5 and cp.prijs < statistics.median(hist) * (1 - drempel):
                    cp.actie = True
            if cp.actie:
                cp.gewicht *= self.kw["actie_gewicht"]
            res.append(cp)
        if res:
            med = statistics.median(c.prijs for c in res)
            for c in res:
                if abs(c.prijs / med - 1) > self.kw["max_afwijking_mediaan"]:
                    c.uitschieter = True
        return res

    def analyseer_product(self, p: Product, signalen: list[Signaal]) -> Voorstel | None:
        rol = self.s["rollen"].get(p.rol) or self.s["rollen"]["kern"]
        btw = self.alg["btw"]
        prijzen = self._concurrentprijzen(p.sku)

        for c in prijzen:
            if c.uitschieter:
                signalen.append(Signaal(p.sku, "uitschieter",
                                        f"{c.naam} {_eur(c.prijs)} wijkt sterk af van de markt – controleer de match ({c.url})"))
        geldig = [c for c in prijzen if not c.uitschieter]
        if len(geldig) < self.alg["min_concurrenten"]:
            signalen.append(Signaal(p.sku, "te_weinig_data",
                                    f"slechts {len(geldig)} geldige concurrentprijs/prijzen"))
            return None

        ref = referentie(geldig, rol["referentie"])
        doel = ref * (1 + rol["index"])
        bodem = bodemprijs(p, rol["min_marge"], btw)
        plafond = max(c.prijs for c in geldig) * self.s.get("plafond_factor_max_concurrent", 1.05)
        if doel < bodem:
            goedkoper = [c for c in geldig if c.prijs < bodem]
            if goedkoper:
                signalen.append(Signaal(p.sku, "onder_bodem",
                    f"markt ({', '.join(f'{c.naam} {_eur(c.prijs)}' for c in goedkoper[:3])}) zit onder "
                    f"onze margebodem {_eur(bodem)} – inkoop heroverwegen of bewust niet volgen"))
            doel = bodem
        doel = min(doel, max(plafond, bodem))

        huidig = p.verkoopprijs_incl_btw
        max_stap = huidig * self.alg["max_stap_pct"]
        # Alleen modelprijzen (niveau C) of minder dan min_concurrenten exacte/gelijkwaardige bronnen:
        # voorzichtiger bijsturen (halve stap)
        sterk = sum(1 for c in geldig if c.niveau in ("A", "B"))
        if sterk < self.alg["min_concurrenten"]:
            max_stap /= 2
        nieuw = huidig + max(-max_stap, min(max_stap, doel - huidig))
        afronding = self.alg.get("afronding", "psychologisch")
        # afronden richting de huidige prijs (conservatief: nooit groter dan max_stap)
        nieuw = rond_af(nieuw, afronding, "omlaag" if nieuw > huidig else "omhoog")
        if nieuw < bodem:  # ... maar nooit onder de margebodem
            nieuw = rond_af(bodem, afronding, "omhoog")

        delta = nieuw - huidig
        if abs(delta) < max(self.alg["min_wijziging_eur"], huidig * self.alg["min_wijziging_pct"]):
            return None
        richting = "omhoog" if delta > 0 else "omlaag"

        # afkoelperiode
        laatst = self.db.laatste_wijziging(p.sku)
        if laatst and (self.nu.date() - datetime.fromisoformat(laatst).date()).days < self.alg["afkoelperiode_dagen"]:
            return None

        # impact: constante-elasticiteitsmodel op de weekafzet
        q = p.afzet_per_week
        e = rol["elasticiteit"]
        q_nieuw = q * (nieuw / huidig) ** e
        bw_oud = q * (huidig / (1 + btw) - p.inkoopprijs_excl_btw)
        bw_nieuw = q_nieuw * (nieuw / (1 + btw) - p.inkoopprijs_excl_btw)
        bw_delta = bw_nieuw - bw_oud

        conc_prijzen = [c.prijs for c in geldig]
        if richting == "omhoog":
            if bw_delta <= 0:
                return None
            score = bw_delta
        else:
            # Verlaging: brutowinst-effect + strategische waarde van het herstellen van de
            # prijspositie (omzet die we op dit moment riskeren door te duur te zijn).
            overprijs = max(0.0, huidig / max(doel, 0.01) - 1)
            omzet_ex = q * huidig / (1 + btw)
            score = bw_delta + rol["strategisch"] * omzet_ex * overprijs * abs(e) * self.alg.get("strategische_weging", 0.3)
            if score <= 0:
                return None

        # marktbeweging afgelopen 7 dagen
        beweging = None
        oud = [c for c in self._concurrentprijzen(p.sku, self.nu - timedelta(days=7)) if not c.uitschieter]
        if len(oud) >= self.alg["min_concurrenten"]:
            ref_oud = referentie(oud, rol["referentie"])
            beweging = ref / ref_oud - 1 if ref_oud else None

        reden = self._reden(p, richting, huidig, nieuw, ref, rol, geldig, beweging, bodem, doel)
        return Voorstel(
            sku=p.sku, naam=p.naam, categorie=p.categorie, rol=p.rol,
            huidige_prijs=round(huidig, 2), voorgestelde_prijs=round(nieuw, 2), richting=richting,
            wijziging_pct=round(delta / huidig, 4), doelprijs=round(doel, 2), referentieprijs=round(ref, 2),
            laagste=round(min(conc_prijzen), 2), mediaan=round(statistics.median(conc_prijzen), 2),
            hoogste=round(max(conc_prijzen), 2), aantal_concurrenten=len(geldig),
            positie_oud=positie(huidig, conc_prijzen), positie_nieuw=positie(nieuw, conc_prijzen),
            marge_oud=round(marge(huidig, p.inkoopprijs_excl_btw, btw), 4),
            marge_nieuw=round(marge(nieuw, p.inkoopprijs_excl_btw, btw), 4),
            brutowinst_delta_week=round(bw_delta, 2), score=round(score, 2), reden=reden,
            marktbeweging_7d=None if beweging is None else round(beweging, 4),
            concurrenten=sorted(prijzen, key=lambda c: c.prijs),
        )

    def _reden(self, p, richting, huidig, nieuw, ref, rol, geldig, beweging, bodem, doel) -> str:
        delen = []
        n = len(geldig)
        goedkoper = sum(1 for c in geldig if c.prijs < huidig - 0.005)
        ref_naam = {"min": "laagste prijs", "p25": "goedkoopste kwart", "mediaan": "marktmediaan",
                    "p75": "duurste kwart"}[rol["referentie"]]
        if beweging is not None and abs(beweging) >= 0.02:
            delen.append(f"markt {'gestegen' if beweging > 0 else 'gedaald'} met {abs(beweging):.0%} in 7 dagen")
        if richting == "omhoog":
            delen.append(f"we zijn nu {'de goedkoopste' if goedkoper == 0 else f'nr. {goedkoper + 1} van {n + 1}'}; "
                         f"{ref_naam} {_eur(ref)} laat ruimte voor marge")
        else:
            delen.append(f"{goedkoper} van {n} concurrenten goedkoper; {ref_naam} {_eur(ref)}")
        afgeleid = sum(1 for c in geldig if c.niveau != "A")
        if afgeleid:
            delen.append(f"{afgeleid} van {n} prijzen afgeleid (gelijkwaardig product of marktmodel)")
        acties = [c.naam for c in geldig if c.actie]
        if acties:
            delen.append(f"tijdelijke actie bij {', '.join(acties)} (telt half mee)")
        if abs(doel - nieuw) > 0.5 and abs(nieuw - huidig) >= huidig * self.alg["max_stap_pct"] * 0.9:
            delen.append(f"stapsgewijs richting doel {_eur(doel)}")
        if abs(nieuw - bodem) < 0.30:
            delen.append("op margebodem")
        return "; ".join(delen)

    def run(self) -> Analyse:
        signalen: list[Signaal] = []
        kandidaten = []
        for p in self.cfg.producten.values():
            v = self.analyseer_product(p, signalen)
            if v:
                kandidaten.append(v)
        return Analyse(voorstellen=selecteer(kandidaten, self.alg), kandidaten=len(kandidaten),
                       geanalyseerd=len(self.cfg.producten), signalen=signalen)


def selecteer(kandidaten: list[Voorstel], alg: dict) -> list[Voorstel]:
    """Kies de N beste voorstellen; garandeer een minimumaandeel omhoog en omlaag."""
    n = alg["voorstellen_per_dag"]
    op = sorted(kandidaten, key=lambda v: v.score, reverse=True)
    omhoog = [v for v in op if v.richting == "omhoog"]
    omlaag = [v for v in op if v.richting == "omlaag"]
    gekozen = omhoog[: int(round(n * alg.get("min_aandeel_omhoog", 0)))] + \
              omlaag[: int(round(n * alg.get("min_aandeel_omlaag", 0)))]
    ids = {id(v) for v in gekozen}
    for v in op:
        if len(gekozen) >= n:
            break
        if id(v) not in ids:
            gekozen.append(v)
            ids.add(id(v))
    return sorted(gekozen[:n], key=lambda v: v.score, reverse=True)
