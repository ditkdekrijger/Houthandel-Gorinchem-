"""Prijs-extractie uit productpagina's.

Volgorde (van meest naar minst betrouwbaar):
  1. CSS-selectors uit config (als ingesteld voor de concurrent)
  2. JSON-LD (schema.org Product/Offer) – gebruiken vrijwel alle grote webshops voor Google Shopping
  3. Microdata (itemprop="price")
  4. Meta-tags (product:price:amount / og:price:amount)
  5. Heuristiek op elementen met 'price/prijs' in de class (lage betrouwbaarheid)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup



@dataclass
class Extractie:
    prijs: float | None
    van_prijs: float | None = None       # doorgestreepte reguliere prijs (actie-indicatie)
    op_voorraad: bool | None = None
    methode: str = ""
    betrouwbaarheid: str = "hoog"        # hoog | middel | laag


def parse_prijs(tekst) -> float | None:
    """Zet NL/EN prijsnotaties om naar float: '€ 1.234,56', '1,234.56', '12,95', '12.95', '12,-'."""
    if tekst is None:
        return None
    if isinstance(tekst, (int, float)):
        return float(tekst) if tekst > 0 else None
    t = str(tekst).replace("\xa0", " ").replace("\u202f", " ")
    m = re.search(r"\d(?:[\d.,]|\s(?=\d{3}\b))*", t)
    if not m:
        return None
    s = m.group(0).replace(" ", "").rstrip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        # het laatst voorkomende scheidingsteken is het decimaalteken
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        delen = s.split(",")
        s = s.replace(",", ".") if len(delen) == 2 and len(delen[1]) in (1, 2) else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[1]) == 3):
        s = s.replace(".", "")  # duizendtallen: 1.234 / 1.234.567
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _beschikbaarheid(v) -> bool | None:
    if not v:
        return None
    v = str(v).lower()
    # eerst de negatieve varianten: 'niet op voorraad' bevat ook 'op voorraad'
    if any(k in v for k in ("outofstock", "out_of_stock", "out of stock", "soldout", "discontinued",
                            "niet leverbaar", "niet op voorraad", "uitverkocht", "tijdelijk niet")):
        return False
    if any(k in v for k in ("instock", "in_stock", "in stock", "limitedavailability", "onlineonly",
                            "op voorraad", "leverbaar")):
        return True
    return None


# ---------------------------------------------------------------- JSON-LD
def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _is_type(d: dict, *types) -> bool:
    t = d.get("@type")
    t = t if isinstance(t, list) else [t]
    return any(str(x).lower() in types for x in t if x)


def _variant_id(url: str | None) -> str | None:
    m = re.search(r"[?&]variant=(\w+)", url or "")
    return m.group(1) if m else None


def uit_jsonld(soup: BeautifulSoup, url: str | None = None) -> Extractie | None:
    """Leest schema.org Product/ProductGroup. Bij een ProductGroup (bijv. hout in meerdere
    lengtes) wordt de variant uit '?variant=...' in de URL gekozen; zonder variant-id wordt
    niets teruggegeven als er meerdere varianten met verschillende prijzen zijn (te riskant)."""
    vid = _variant_id(url)
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            # sommige sites zetten meerdere objecten of trailing komma's: probeer te herstellen
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.strip()))
            except Exception:
                continue
        if not vid and url:
            # variantpagina met eigen URL (bijv. per maat een aparte pagina): kies die variant
            schoon = url.split("#")[0].rstrip("/")
            for g in _walk(data):
                if _is_type(g, "productgroup"):
                    for v in g.get("hasVariant") or []:
                        o = (v.get("offers") or {}) if isinstance(v, dict) else {}
                        if isinstance(o, dict) and str(o.get("url", "")).split("#")[0].rstrip("/") == schoon \
                                and o.get("price") is not None:
                            vid = schoon
        if not vid:
            for g in _walk(data):
                if _is_type(g, "productgroup"):
                    vp = {str((v.get("offers") or {}).get("price")) for v in g.get("hasVariant") or []
                          if isinstance(v, dict)}
                    if len(vp) > 1:
                        # meerdere lengtes/maten met eigen prijs: zonder variant-id niet gokken
                        return Extractie(prijs=None, methode="variant_vereist", betrouwbaarheid="laag")
        for d in _walk(data):
            if not _is_type(d, "product", "productgroup"):
                continue
            offers = d.get("offers")
            varianten = [v for v in (d.get("hasVariant") or []) if isinstance(v, dict)]
            if not offers and varianten:
                if vid:
                    gekozen = [v for v in varianten if vid in json.dumps(v)]
                    if not gekozen:
                        continue
                    offers = gekozen[0].get("offers")
                else:
                    prijzen = {str((v.get("offers") or {}).get("price")) for v in varianten}
                    if len(prijzen) > 1:
                        continue  # meerdere lengtes/maten met eigen prijs: variant-id verplicht
                    offers = varianten[0].get("offers")
            if not offers:
                continue
            offer_lijst = offers if isinstance(offers, list) else [offers]
            for off in offer_lijst:
                if not isinstance(off, dict):
                    continue
                prijs = parse_prijs(off.get("price") or off.get("lowPrice"))
                van = None
                spec = off.get("priceSpecification")
                for s in (spec if isinstance(spec, list) else [spec] if spec else []):
                    if not isinstance(s, dict):
                        continue
                    pt = str(s.get("priceType", "")).lower()
                    if "strikethrough" in pt or "listprice" in pt or "srp" in pt:
                        van = parse_prijs(s.get("price"))
                    elif prijs is None:
                        prijs = parse_prijs(s.get("price"))
                if prijs:
                    return Extractie(prijs=prijs, van_prijs=van if van and van > prijs else None,
                                     op_voorraad=_beschikbaarheid(off.get("availability")),
                                     methode="json-ld")
    return None


# ---------------------------------------------------------------- microdata / meta
def uit_microdata(soup: BeautifulSoup) -> Extractie | None:
    el = soup.find(attrs={"itemprop": "price"})
    if not el:
        return None
    prijs = parse_prijs(el.get("content") or el.get_text(" ", strip=True))
    if not prijs:
        return None
    av = soup.find(attrs={"itemprop": "availability"})
    voorraad = _beschikbaarheid((av.get("href") or av.get("content") or av.get_text()) if av else None)
    return Extractie(prijs=prijs, op_voorraad=voorraad, methode="microdata")


def uit_meta(soup: BeautifulSoup) -> Extractie | None:
    for prop in ("product:price:amount", "og:price:amount", "product:sale_price:amount"):
        el = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if el and parse_prijs(el.get("content")):
            av = soup.find("meta", attrs={"property": "product:availability"})
            return Extractie(prijs=parse_prijs(el.get("content")),
                             op_voorraad=_beschikbaarheid(av.get("content") if av else None),
                             methode="meta", betrouwbaarheid="middel")
    return None


def uit_selectors(soup: BeautifulSoup, selectors: dict) -> Extractie | None:
    if not selectors or not selectors.get("prijs"):
        return None
    el = soup.select_one(selectors["prijs"])
    if not el:
        return None
    prijs = parse_prijs(el.get("content") or el.get_text(" ", strip=True))
    if not prijs:
        return None
    van = None
    if selectors.get("van_prijs"):
        v = soup.select_one(selectors["van_prijs"])
        van = parse_prijs(v.get_text(" ", strip=True)) if v else None
    voorraad = None
    if selectors.get("voorraad"):
        v = soup.select_one(selectors["voorraad"])
        voorraad = _beschikbaarheid(v.get_text(" ", strip=True)) if v else None
    return Extractie(prijs=prijs, van_prijs=van if van and van > prijs else None,
                     op_voorraad=voorraad, methode="css")


def uit_heuristiek(soup: BeautifulSoup) -> Extractie | None:
    kandidaten = soup.find_all(class_=re.compile(r"(^|[-_ ])(price|prijs)", re.I))
    for el in kandidaten:
        if el.find_parent(["del", "s"]) or el.name in ("del", "s"):
            continue
        klasse = " ".join(el.get("class", [])).lower()
        if any(k in klasse for k in ("old", "was", "strike", "van", "regular", "per-m", "unit")):
            continue
        p = parse_prijs(el.get_text(" ", strip=True))
        if p:
            return Extractie(prijs=p, methode="heuristiek", betrouwbaarheid="laag")
    return None


def extraheer(html: str, selectors: dict | None = None, url: str | None = None) -> Extractie:
    soup = BeautifulSoup(html, "html.parser")
    stappen = [lambda: uit_selectors(soup, selectors or {}), lambda: uit_jsonld(soup, url)]
    if not _variant_id(url):
        # bij een specifieke variant geen gok-methodes: die pakken mogelijk een andere lengte
        stappen += [lambda: uit_microdata(soup), lambda: uit_meta(soup), lambda: uit_heuristiek(soup)]
    for fn in stappen:
        res = fn()
        if res and (res.prijs or res.methode == "variant_vereist"):
            return res
    return Extractie(prijs=None, methode="geen", betrouwbaarheid="laag")
