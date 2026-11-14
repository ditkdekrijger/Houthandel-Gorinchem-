from pathlib import Path

import pytest

from prijsscraper.scraper.extract import extraheer, parse_prijs

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("tekst,verwacht", [
    ("€ 12,95", 12.95), ("12.95", 12.95), ("€ 1.234,56", 1234.56), ("1,234.56", 1234.56),
    ("12,-", 12.0), ("€ 1.299,-", 1299.0), ("Nu voor € 7,5", 7.5), ("1.234", 1234.0),
    ("EUR 0,99", 0.99), (19.9, 19.9), ("", None), ("gratis", None), ("0,00", None),
])
def test_parse_prijs(tekst, verwacht):
    assert parse_prijs(tekst) == verwacht


def test_jsonld_heeft_voorrang_en_leest_van_prijs():
    ex = extraheer((FIX / "jsonld.html").read_text())
    assert ex.prijs == 11.49 and ex.van_prijs == 13.99 and ex.op_voorraad is True
    assert ex.methode == "json-ld"


def test_microdata():
    ex = extraheer((FIX / "microdata.html").read_text())
    assert ex.prijs == 27.5 and ex.op_voorraad is False and ex.methode == "microdata"


def test_meta():
    ex = extraheer((FIX / "meta.html").read_text())
    assert ex.prijs == 1234.5 and ex.op_voorraad is True


def test_css_selectors_gaan_voor():
    sel = {"prijs": ".price-final", "van_prijs": ".was", "voorraad": ".stock-status"}
    ex = extraheer((FIX / "css.html").read_text(), sel)
    assert (ex.prijs, ex.van_prijs, ex.op_voorraad, ex.methode) == (34.0, 39.95, True, "css")


def test_heuristiek_slaat_oude_prijs_over():
    ex = extraheer((FIX / "heuristiek.html").read_text())
    assert ex.prijs == 17.95 and ex.betrouwbaarheid == "laag"


def test_geen_prijs():
    assert extraheer("<html><body>Niets</body></html>").prijs is None


@pytest.mark.parametrize("tekst,verwacht", [
    ("Op voorraad", True), ("Niet op voorraad", False), ("Niet leverbaar", False),
    ("https://schema.org/InStock", True), ("https://schema.org/OutOfStock", False), ("bel ons", None),
])
def test_beschikbaarheid(tekst, verwacht):
    from prijsscraper.scraper.extract import _beschikbaarheid
    assert _beschikbaarheid(tekst) is verwacht


def test_shopify_variant_uit_url():
    html = (FIX / "shopify_varianten.html").read_text()
    basis = "https://www.bouwmaat.nl/products/vuren-geschaafd-c24-44x69mm-fsc-mix-70-f10019"
    assert extraheer(html, None, basis + "?variant=56807926923590").prijs == 4.95   # 300 cm
    assert extraheer(html, None, basis + "?variant=56807733788998").prijs == 5.94   # 360 cm
    # zonder variant-id geen gok bij meerdere prijzen
    assert extraheer(html, None, basis).prijs is None
