"""Eigen assortiment inlezen uit de WooCommerce-webshop van Houthandel Gorinchem.

Leest de product-sitemap, haalt elke productpagina op en zet elk artikel (en elke
lengte-/maatvariant) als regel in producten.csv.

Wat NIET online staat en dus geschat wordt (kolom `bron_schatting`):
  - inkoopprijs: uit de opgegeven gemiddelde brutomarge
  - afzet per week: omzet evenredig verdeeld over de artikelen
Vervang deze schattingen zo snel mogelijk door een export uit het ERP.
"""
from __future__ import annotations

import csv
import html as htmlmod
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .scraper.fetch import Fetcher

log = logging.getLogger(__name__)

TREKKER = re.compile(r"steiger|osb|underlayment|vuren|grenen|douglas.*(plank|paal|balk|regel)|schutting|tuinscherm|tuinplank|tuinpaal|beschoei", re.I)
MARGE = re.compile(r"hardhout|bangkirai|azob|ipe|eiken|cedar|thermo|accoya|frak|meranti|overkapping|veranda|tuinhuis|blokhut|kapschuur|gevel|rabat|siding|maatwerk|op maat|composiet", re.I)


def bepaal_rol(naam: str, categorie: str) -> str:
    t = f"{naam} {categorie}"
    if MARGE.search(t):
        return "marge"
    if TREKKER.search(t):
        return "trekker"
    return "kern"


def _sitemap_urls(fetcher: Fetcher, basis: str) -> list[str]:
    """Alle product-sitemaps (Yoast splitst per 1000: product-sitemap.xml, product-sitemap2.xml, ...)."""
    urls: list[str] = []
    for n in range(1, 30):
        naam = "product-sitemap.xml" if n == 1 else f"product-sitemap{n}.xml"
        try:
            xml = fetcher.haal(basis.rstrip("/") + "/" + naam, 1)
        except Exception:  # noqa: BLE001
            break
        gevonden = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(https?://[^\]<\s]+/product/[^\]<\s]+)", xml)
        if not gevonden:
            break
        urls += gevonden
    return sorted(set(urls))


def _variaties(html: str) -> list[dict]:
    """WooCommerce-variaties: attribuut data-product_variations of JS `var variations = [...]`."""
    m = re.search(r'data-product_variations="([^"]+)"', html)
    if m and m.group(1) != "false":
        try:
            return json.loads(htmlmod.unescape(m.group(1)))
        except (json.JSONDecodeError, ValueError):
            pass
    m = re.search(r"var\s+variations\s*=\s*\[", html)
    if m:
        try:
            data, _ = json.JSONDecoder().raw_decode(html, m.end() - 1)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def parse_productpagina(url: str, html: str) -> list[dict]:
    """Eén pagina -> één of meer artikelregels (variaties apart)."""
    prod, cats = None, []
    for m in re.finditer(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", html, re.S):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else data
        for d in items if isinstance(items, list) else []:
            if not isinstance(d, dict):
                continue
            if d.get("@type") == "BreadcrumbList":
                cats = []
                for e in d.get("itemListElement", []):
                    it = e.get("item") if isinstance(e, dict) else None
                    cats.append((it.get("name") if isinstance(it, dict) else None) or (e.get("name") if isinstance(e, dict) else None))
            if d.get("@type") == "Product" and d.get("offers") and prod is None:
                prod = d
    if not prod:
        return []
    naam = htmlmod.unescape(prod.get("name", "")).strip()
    categorie = " > ".join(c for c in cats[1:-1] if c) if len(cats) > 2 else ""
    offers = prod["offers"] if isinstance(prod["offers"], dict) else (prod["offers"] or [{}])[0]
    basis_sku = str(prod.get("sku") or "").strip()
    regels = []

    variaties = [v for v in _variaties(html) if isinstance(v, dict)]
    if variaties:
        for v in variaties:
            if not v.get("display_price"):
                continue
            attr = " ".join(str(x).replace("-", " ") for x in (v.get("attributes") or {}).values() if x)
            vsku = str(v.get("sku") or "").strip()
            regels.append({
                "sku": vsku if vsku and vsku != basis_sku else f"{basis_sku or 'HG'}-{v['variation_id']}",
                "naam": f"{naam} {attr}".strip(),
                "categorie": categorie,
                "verkoopprijs_incl_btw": float(v["display_price"]),
                "van_prijs": (float(v.get("display_regular_price") or 0)
                              if float(v.get("display_regular_price") or 0) > float(v["display_price"]) else None),
                "op_voorraad": bool(v.get("is_in_stock", True)),
                "url": f"{url}?variation_id={v['variation_id']}",
            })
    else:
        try:
            prijs = float(str(offers.get("price") or offers.get("lowPrice") or 0).replace(",", "."))
        except ValueError:
            prijs = 0
        if prijs > 0:
            regels.append({
                "sku": basis_sku or re.sub(r"\W+", "-", url.rstrip("/").split("/")[-1])[:40],
                "naam": naam, "categorie": categorie, "verkoopprijs_incl_btw": prijs, "van_prijs": None,
                "op_voorraad": "InStock" in str(offers.get("availability", "")), "url": url,
            })
    return regels


def importeer(basis: str, uit: Path, omzet_jaar: float, brutomarge: float, btw: float = 0.21,
              workers: int = 2, interval: float = 1.0, limiet: int | None = None) -> dict:
    fetcher = Fetcher("HoutGorinchemPrijsmonitor/1.0 (eigen assortiment)", standaard_interval=interval)
    urls = _sitemap_urls(fetcher, basis)
    if limiet:
        urls = urls[:limiet]
    log.info("%d productpagina's gevonden", len(urls))

    def een(url):
        try:
            return parse_productpagina(url, fetcher.haal(url, interval))
        except Exception as e:  # noqa: BLE001
            log.warning("Overgeslagen %s: %s", url, e)
            return []

    regels, gezien = [], set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, res in enumerate(pool.map(een, urls), 1):
            for r in res:
                if r["sku"] in gezien:
                    r["sku"] = f"{r['sku']}-{len(gezien)}"
                gezien.add(r["sku"])
                regels.append(r)
            if i % 100 == 0:
                log.info("%d/%d pagina's, %d artikelen", i, len(urls), len(regels))

    # schattingen
    per_artikel_week = omzet_jaar / 52 / max(1, len(regels))
    for r in regels:
        ex = r["verkoopprijs_incl_btw"] / (1 + btw)
        r["inkoopprijs_excl_btw"] = round(ex * (1 - brutomarge), 2)
        r["afzet_per_week"] = round(per_artikel_week / ex, 2) if ex else 0
        r["rol"] = bepaal_rol(r["naam"], r["categorie"])
        r["eenheid"] = "stuk"
        r["ean"] = ""
        r["bron_schatting"] = f"inkoop+afzet geschat ({brutomarge:.0%} marge, €{omzet_jaar:,.0f}/jr)"

    uit.parent.mkdir(parents=True, exist_ok=True)
    velden = ["sku", "naam", "categorie", "eenheid", "inkoopprijs_excl_btw", "verkoopprijs_incl_btw", "rol",
              "afzet_per_week", "ean", "url", "van_prijs", "op_voorraad", "bron_schatting"]
    with open(uit, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=velden, extrasaction="ignore")
        w.writeheader()
        w.writerows(regels)
    return {"paginas": len(urls), "artikelen": len(regels)}
