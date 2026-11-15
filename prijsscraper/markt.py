"""Assortimenten van concurrenten verzamelen (voor het automatisch koppelen van producten).

Bronnen per concurrent (config: `catalogus` in concurrenten.yaml):
  shopify   /products.json  – volledig assortiment incl. alle varianten en prijzen in weinig requests
  sitemap   product-URL's uit de sitemap (gefilterd op houtwoorden), daarna per pagina de prijs
            uitlezen met dezelfde extractor als de dagelijkse scraper
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from dataclasses import dataclass, asdict

from .scraper.extract import extraheer
from .scraper.fetch import Fetcher

log = logging.getLogger(__name__)

HOUT_URL = re.compile(
    r"douglas|lariks|vuren|grenen|eiken|bangkirai|azobe|ipe|meranti|thermo|cedar|ceder|accoya|frake|steiger|osb|"
    r"underlayment|betonplex|multiplex|mdf|hardhout|plank|paal|palen|balk|regel|rabat|siding|vlonder|schutting|"
    r"tuinscherm|scherm|dakbeschot|potdeksel|rhombus|plaat|lat-|latten|ligger|keruing|cumaru|robinia|kastanje", re.I)


@dataclass
class Aanbod:
    concurrent: str
    titel: str
    url: str
    prijs_ruw: float
    prijs_incl: float
    op_voorraad: bool | None = None
    adviesprijs: float | None = None


def _incl(p: float, incl: bool, btw: float) -> float:
    return round(p if incl else p * (1 + btw), 4)


def shopify(fetcher: Fetcher, conc, btw: float, max_paginas: int = 60) -> list[Aanbod]:
    basis = conc.basis_url.rstrip("/")
    res = []
    for pagina in range(1, max_paginas + 1):
        data = json.loads(fetcher.haal(f"{basis}/products.json?limit=250&page={pagina}", conc.min_interval_sec or 2))
        producten = data.get("products", [])
        if not producten:
            break
        for p in producten:
            varianten = p.get("variants", [])
            for v in varianten:
                try:
                    prijs = float(v.get("price") or 0)
                except ValueError:
                    continue
                if prijs <= 0:
                    continue
                vt = v.get("title") or ""
                titel = p["title"] if vt in ("", "Default Title") else f"{p['title']} {vt}"
                van = float(v["compare_at_price"]) if v.get("compare_at_price") else None
                res.append(Aanbod(conc.id, titel, f"{basis}/products/{p['handle']}?variant={v['id']}",
                                  prijs, _incl(prijs, conc.prijs_incl_btw, btw), v.get("available"),
                                  _incl(van, conc.prijs_incl_btw, btw) if van and van > prijs else None))
    return res


def _sitemap_lijst(fetcher: Fetcher, url: str, diepte: int = 0) -> list[str]:
    try:
        raw = fetcher.haal(url, 1.5)
    except Exception as e:  # noqa: BLE001
        log.warning("sitemap %s: %s", url, e)
        return []
    if raw[:2] == "\x1f\x8b":
        raw = gzip.decompress(raw.encode("latin1")).decode("utf-8", "replace")
    locs = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^\]<\s]+)", raw)
    if "<sitemapindex" in raw and diepte < 2:
        uit = []
        for l in locs:
            if re.search(r"product|catalog|item|sitemap\d|pages?-|/nl", l, re.I) or len(locs) <= 6:
                uit += _sitemap_lijst(fetcher, l, diepte + 1)
        return uit
    return locs


def sitemap(fetcher: Fetcher, conc, btw: float, max_paginas: int = 900) -> list[Aanbod]:
    cat = conc.catalogus or {}
    urls = []
    for s in cat.get("sitemaps") or [conc.basis_url.rstrip("/") + "/sitemap.xml"]:
        urls += _sitemap_lijst(fetcher, s)
    patroon = re.compile(cat["url_filter"], re.I) if cat.get("url_filter") else None
    urls = [u for u in dict.fromkeys(urls)
            if HOUT_URL.search(u) and (not cat.get("cijfer_vereist", True) or re.search(r"\d", u.rsplit("/", 1)[-1] or u))
            and (not patroon or patroon.search(u))]
    log.info("%s: %d kandidaat-pagina's (max %d)", conc.id, len(urls), max_paginas)
    res = []
    for u in urls[:max_paginas]:
        try:
            html = fetcher.haal(u, conc.min_interval_sec or 1.5)
        except Exception as e:  # noqa: BLE001
            log.debug("%s: %s", u, e)
            continue
        ex = extraheer(html, conc.selectors, u)
        if not ex.prijs or ex.betrouwbaarheid == "laag":
            continue
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S) or re.search(r"<title>(.*?)</title>", html, re.S)
        titel = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else u.rsplit("/", 1)[-1]
        res.append(Aanbod(conc.id, titel, u, ex.prijs, _incl(ex.prijs, conc.prijs_incl_btw, btw), ex.op_voorraad,
                          _incl(ex.van_prijs, conc.prijs_incl_btw, btw) if ex.van_prijs else None))
    return res


def verzamel(conc, btw: float, fetcher: Fetcher) -> list[Aanbod]:
    soort = (conc.catalogus or {}).get("soort")
    try:
        if soort == "shopify":
            return shopify(fetcher, conc, btw)
        if soort == "sitemap":
            return sitemap(fetcher, conc, btw, (conc.catalogus or {}).get("max_paginas", 900))
    except Exception as e:  # noqa: BLE001
        log.warning("%s: verzamelen mislukt: %s", conc.id, e)
    return []


def naar_dict(a: Aanbod) -> dict:
    return asdict(a)
