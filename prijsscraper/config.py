"""Inlezen van configuratie: concurrenten, producten, matches en strategie."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Concurrent:
    id: str
    naam: str
    type: str
    gewicht: float
    prijs_incl_btw: bool
    basis_url: str
    min_interval_sec: float | None = None
    selectors: dict = field(default_factory=dict)
    bereikbaar: bool = True
    notitie: str = ""
    catalogus: dict = field(default_factory=dict)


@dataclass
class Product:
    sku: str
    naam: str
    categorie: str
    eenheid: str
    inkoopprijs_excl_btw: float
    verkoopprijs_incl_btw: float
    rol: str
    afzet_per_week: float
    ean: str = ""


@dataclass
class Match:
    sku: str
    concurrent: str
    url: str
    omrekenfactor: float = 1.0
    opmerking: str = ""


@dataclass
class Config:
    instellingen: dict
    concurrenten: dict[str, Concurrent]
    producten: dict[str, Product]
    matches: list[Match]
    strategie: dict


def _float(v, default=0.0) -> float:
    if v is None or str(v).strip() == "":
        return default
    return float(str(v).replace(",", "."))


def laad_concurrenten(pad: Path) -> tuple[dict, dict[str, Concurrent]]:
    data = yaml.safe_load(Path(pad).read_text(encoding="utf-8"))
    res = {}
    for c in data["concurrenten"]:
        res[c["id"]] = Concurrent(
            id=c["id"],
            naam=c.get("naam", c["id"]),
            type=c.get("type", "online"),
            gewicht=float(c.get("gewicht", 1.0)),
            prijs_incl_btw=bool(c.get("prijs_incl_btw", True)),
            basis_url=c.get("basis_url", ""),
            min_interval_sec=c.get("min_interval_sec"),
            selectors=c.get("selectors") or {},
            bereikbaar=bool(c.get("bereikbaar", True)),
            notitie=c.get("notitie", ""),
            catalogus=c.get("catalogus") or {},
        )
    return data.get("instellingen", {}), res


def laad_producten(pad: Path) -> dict[str, Product]:
    res = {}
    with open(pad, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("sku"):
                continue
            res[r["sku"]] = Product(
                sku=r["sku"].strip(),
                naam=r["naam"].strip(),
                categorie=(r.get("categorie") or "").strip(),
                eenheid=(r.get("eenheid") or "stuk").strip(),
                inkoopprijs_excl_btw=_float(r["inkoopprijs_excl_btw"]),
                verkoopprijs_incl_btw=_float(r["verkoopprijs_incl_btw"]),
                rol=(r.get("rol") or "kern").strip(),
                afzet_per_week=_float(r.get("afzet_per_week"), 1.0),
                ean=(r.get("ean") or "").strip(),
            )
    return res


def laad_matches(pad: Path) -> list[Match]:
    res = []
    with open(pad, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("sku") or not r.get("url"):
                continue
            res.append(Match(
                sku=r["sku"].strip(),
                concurrent=r["concurrent"].strip(),
                url=r["url"].strip(),
                omrekenfactor=_float(r.get("omrekenfactor"), 1.0),
                opmerking=(r.get("opmerking") or "").strip(),
            ))
    return res


def laad_config(map_: str | Path = "config") -> Config:
    map_ = Path(map_)
    inst, conc = laad_concurrenten(map_ / "concurrenten.yaml")
    strategie = yaml.safe_load((map_ / "strategie.yaml").read_text(encoding="utf-8"))
    return Config(
        instellingen=inst,
        concurrenten=conc,
        producten=laad_producten(map_ / "producten.csv"),
        matches=laad_matches(map_ / "matches.csv"),
        strategie=strategie,
    )
