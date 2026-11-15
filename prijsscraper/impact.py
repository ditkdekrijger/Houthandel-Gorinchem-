"""Indicatie van het effect van structureel prijsmanagement op omzet en brutowinst.

Werkwijze (per gekoppeld artikel met concurrentprijzen):
  1. Doelprijs volgens de strategie (rol, referentie, index), begrensd door margebodem en
     een maximale jaarlijkse aanpassing.
  2. Volume-effect via een constante prijselasticiteit per rol. Omdat klanten vergelijken,
     is de elasticiteit hoger naarmate we verder boven de markt zitten (en lager eronder).
  3. Brutowinst = afzet x (prijs excl. btw - inkoop).
  4. Optellen over de gekoppelde artikelen; de rest van het assortiment blijft gelijk.
  5. Opschalen naar de werkelijke omzet en marge (gekoppeld aandeel x jaaromzet).

Drie scenario's verschillen in hoeveel van de voorstellen wordt doorgevoerd, hoe
prijsgevoelig klanten zijn en hoe ver prijzen per jaar mogen bewegen.
Alle uitkomsten zijn indicaties: afzet en inkoop per artikel zijn geschat.
"""
from __future__ import annotations

import statistics

from .config import Config
from .db import DB
from .pricing import NIVEAU_GEWICHT, REFERENTIE_Q, bodemprijs, gewogen_kwantiel

SCENARIO = {
    "voorzichtig": {"adoptie": 0.5, "elasticiteit_x": 1.3, "max_jaar": 0.06},
    "realistisch": {"adoptie": 0.75, "elasticiteit_x": 1.0, "max_jaar": 0.10},
    "ambitieus": {"adoptie": 1.0, "elasticiteit_x": 0.85, "max_jaar": 0.15},
}


def _elasticiteit(basis: float, index: float, x: float) -> float:
    # boven de markt reageren klanten sterker; ver eronder minder
    factor = 1 + max(-0.4, min(1.0, (index - 1) * 4))
    return basis * x * factor


# Welk deel van de voorgestelde aanpassing we per niveau verwachten door te voeren:
# hoe zekerder de marktprijs, hoe groter het deel.
NIVEAU_ADOPTIE = {"A": 1.0, "B": 0.85, "C": 0.6, "D": 0.35}


def analyse(cfg: Config, db: DB, omzet_jaar: float, brutomarge: float, min_bronnen: int = 1,
            niveaus: str = "ABCD") -> dict:
    """Effect over het hele assortiment. Artikelen met concurrentprijzen (A-C) gebruiken die
    prijzen; overige artikelen de categorie-index (D). `niveaus` beperkt de berekening (bv. 'AB'
    voor alleen zekere koppelingen); de rest blijft dan ongewijzigd."""
    s = cfg.strategie
    btw = s["algemeen"]["btw"]
    max_uur = 24 * 30
    refs = db.marktreferenties()
    artikelen = []
    for p in cfg.producten.values():
        obs = [o for o in db.laatste_observaties(p.sku, max_uur) if o["prijs_incl"]]
        rol = s["rollen"].get(p.rol) or s["rollen"]["kern"]
        if len(obs) >= min_bronnen:
            niveau = min((o.get("niveau") or "A") for o in obs)
            prijzen = [(o["prijs_incl"], (cfg.concurrenten[o["concurrent"]].gewicht if o["concurrent"] in cfg.concurrenten else 1)
                        * NIVEAU_GEWICHT.get(o.get("niveau") or "A", 0.5)) for o in obs]
            med = statistics.median(x for x, _ in prijzen)
            binnen = [(x, g) for x, g in prijzen if abs(x / med - 1) <= s["kwaliteit"]["max_afwijking_mediaan"]]
            prijzen = binnen or prijzen  # twee ver uiteen liggende bronnen: beide houden
            med = statistics.median(x for x, _ in prijzen)
            ref = min(x for x, _ in prijzen) if rol["referentie"] == "min" else gewogen_kwantiel(prijzen, REFERENTIE_Q[rol["referentie"]])
            n = len(prijzen)
        elif p.sku in refs and refs[p.sku]["niveau"] == "D" and refs[p.sku]["marktprijs"]:
            niveau, med, n = "D", refs[p.sku]["marktprijs"], 0
            ref = med  # alleen een indexniveau bekend: geen spreiding, dus mediaan als referentie
        else:
            continue
        artikelen.append({"p": p, "rol": rol, "ref": ref, "med": med, "n": n, "niveau": niveau,
                          "index": p.verkoopprijs_incl_btw / med})

    omzet_model = sum(p.afzet_per_week * p.verkoopprijs_incl_btw / (1 + btw) * 52 for p in cfg.producten.values())
    omzet_gekoppeld = sum(a["p"].afzet_per_week * a["p"].verkoopprijs_incl_btw / (1 + btw) * 52 for a in artikelen)
    dekking = omzet_gekoppeld / omzet_model if omzet_model else 0
    gemeten = [a for a in artikelen if a["niveau"] != "D"]

    verdeling = {"<90": 0, "90-97": 0, "97-103": 0, "103-110": 0, ">110": 0}
    for a in gemeten:
        i = a["index"] * 100
        k = "<90" if i < 90 else "90-97" if i < 97 else "97-103" if i <= 103 else "103-110" if i <= 110 else ">110"
        verdeling[k] += 1

    uitkomst = {}
    for naam, sc in SCENARIO.items():
        oud_omzet = oud_bw = nieuw_omzet = nieuw_bw = 0.0
        omhoog = omlaag = 0
        per_niveau = {n: 0.0 for n in "ABCD"}
        for a in artikelen:
            p, rol = a["p"], a["rol"]
            q = p.afzet_per_week * 52
            pr = p.verkoopprijs_incl_btw
            doel = max(a["ref"] * (1 + rol["index"]), bodemprijs(p, rol["min_marge"], btw))
            stap = max(-sc["max_jaar"], min(sc["max_jaar"], doel / pr - 1))
            if abs(stap) < s["algemeen"]["min_wijziging_pct"] or a["niveau"] not in niveaus:
                stap = 0
            stap *= sc["adoptie"] * NIVEAU_ADOPTIE[a["niveau"]]
            nieuw = pr * (1 + stap)
            e = _elasticiteit(rol["elasticiteit"], a["index"], sc["elasticiteit_x"])
            q_nieuw = q * (nieuw / pr) ** e
            ex_o, ex_n = pr / (1 + btw), nieuw / (1 + btw)
            oud_omzet += q * ex_o
            oud_bw += q * (ex_o - p.inkoopprijs_excl_btw)
            nieuw_omzet += q_nieuw * ex_n
            bw_n = q_nieuw * (ex_n - p.inkoopprijs_excl_btw)
            nieuw_bw += bw_n
            per_niveau[a["niveau"]] += bw_n - q * (ex_o - p.inkoopprijs_excl_btw)
            omhoog += stap > 0
            omlaag += stap < 0
        d_omzet = (nieuw_omzet / oud_omzet - 1) if oud_omzet else 0
        d_bw = (nieuw_bw / oud_bw - 1) if oud_bw else 0
        omzet_echt = omzet_jaar * dekking
        bw_echt = omzet_echt * brutomarge
        schaal = bw_echt / oud_bw if oud_bw else 0
        uitkomst[naam] = {
            **sc, "artikelen_omhoog": omhoog, "artikelen_omlaag": omlaag,
            "omzet_delta_pct_gekoppeld": round(d_omzet, 4), "brutowinst_delta_pct_gekoppeld": round(d_bw, 4),
            "omzet_delta_eur": round(omzet_echt * d_omzet),
            "brutowinst_delta_eur": round(bw_echt * d_bw),
            "brutowinst_per_niveau_eur": {n: round(v * schaal) for n, v in per_niveau.items()},
            "marge_nieuw_totaal": round((omzet_jaar * brutomarge + bw_echt * d_bw) / (omzet_jaar + omzet_echt * d_omzet), 4),
            "brutowinst_delta_eur_volledige_dekking": round(omzet_jaar * brutomarge * d_bw),
            "omzet_delta_eur_volledige_dekking": round(omzet_jaar * d_omzet),
        }

    return {"artikelen_totaal": len(cfg.producten), "artikelen_gekoppeld": len(gemeten),
            "artikelen_2plus": sum(a["n"] >= 2 for a in gemeten), "artikelen_met_referentie": len(artikelen),
            "per_niveau": {n: sum(a["niveau"] == n for a in artikelen) for n in "ABCD"},
            "dekking_omzet": round(dekking, 4),
            "index_mediaan": round(statistics.median(a["index"] for a in gemeten), 4) if gemeten else None,
            "verdeling_index": verdeling, "scenario": uitkomst,
            "omzet_jaar": omzet_jaar, "brutomarge": brutomarge}
