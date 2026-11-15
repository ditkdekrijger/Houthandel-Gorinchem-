from prijsscraper.marktmodel import (_hout_kandidaat, bevestiging, bevestiging_spec, bouwpakketten,
                                     categorie_index, hout_en_platen, pakket)
from prijsscraper.matcher import kenmerken


def _k(t):
    return kenmerken(t)


def test_zelfde_doorsnede_andere_lengte_is_gelijkwaardig():
    r = _hout_kandidaat(_k("Douglas balk 50 x 150 mm 400 cm"), _k("Douglas balk 50x150 mm 250 cm"))
    assert r[0] == "B" and abs(r[2] - 1.6) < 1e-6


def test_model_alleen_binnen_zelfde_type_en_kleine_afstand():
    assert _hout_kandidaat(_k("Douglas balk 70 x 200 mm 300 cm"), _k("Douglas balk 63 x 185 mm 300 cm"))[0] == "C"
    assert _hout_kandidaat(_k("Douglas paal 70 x 70 mm 300 cm"), _k("Douglas regel 45 x 70 mm 300 cm")) is None
    assert _hout_kandidaat(_k("Douglas balk 50 x 100 mm 300 cm"), _k("Douglas balk 100 x 200 mm 300 cm")) is None


def test_geen_hout_model_voor_meubels():
    eigen = [({"sku": "T", "naam": "Eiken tafelblad 240 x 100 rustiek", "prijs": 600}, _k("Eiken tafelblad 240 x 100 rustiek"))]
    aanbod = [({"concurrent": "x", "titel": "Eiken balk 100x250 bezaagd", "url": "u", "prijs_incl": 380}, _k("Eiken balk 100x250 bezaagd"))]
    assert hout_en_platen(eigen, aanbod, set()) == []


def test_pakket_en_muurvariant():
    assert pakket("Overkapping doe-het-zelf 600 x 300 Premium") == ("overkapping", 18.0)
    assert pakket("DHZ Douglas muuroverkapping 600x300 cm")[0] == "overkapping-muur"
    assert pakket("Douglas plank 22 x 200 mm") is None


def test_bouwpakket_basisprijs_per_m2():
    eigen = [({"sku": "O", "naam": "Douglas overkapping 600 x 300 cm", "prijs": 3000}, _k("Douglas overkapping 600 x 300 cm"))]
    aanbod = [{"concurrent": "x", "titel": f"Douglas overkapping 600x300cm optie {i}", "url": f"u{i}", "prijs_incl": p}
              for i, p in enumerate([3200, 3600, 4000, 5000])]
    r = bouwpakketten(eigen, aanbod, set())
    assert len(r) == 1 and r[0]["niveau"] == "C"
    assert round(r[0]["prijs_incl"] * r[0]["omrekenfactor"]) == 3600  # p25 van 4 configuraties


def test_bevestiging_per_stuk():
    assert bevestiging_spec("Houtdraadbout M8 x 90-130mm 100 st. per doos 8x100mm") == ("houtdraadbout", 8.0, 100.0, False, 100)
    assert bevestiging_spec("Flenskopschroef per doos 8 0 x 180 mm") is None  # aantal onbekend
    eigen = [({"sku": "B", "naam": "Houtdraadbout M8 x 90-130mm 100 st. per doos 8x100mm", "prijs": 30.0}, None)]
    aanbod = [{"concurrent": "h", "titel": "Deltafix houtdraadbout verzinkt 8x100mm 10st", "url": "u", "prijs_incl": 3.5}]
    r = bevestiging(eigen, aanbod, set())
    assert r[0]["niveau"] == "C" and r[0]["omrekenfactor"] == 10


def test_categorie_index_valt_terug_op_bredere_groep():
    prod = [{"sku": str(i), "naam": "x", "categorie": "Hout > Douglas", "rol": "kern", "prijs": 10} for i in range(6)]
    prod.append({"sku": "zonder", "naam": "y", "categorie": "Hout > Eiken", "rol": "kern", "prijs": 18})
    r = categorie_index(prod, {str(i): 0.9 for i in range(6)})
    assert r == [{"sku": "zonder", "niveau": "D", "marktprijs": 20.0, "bronnen": 6, "betrouwbaarheid": 0.2,
                  "toelichting": "index 90 van 6 gekoppelde artikelen in Hout"}]


def test_categorie_index_eerst_zelfde_producttype():
    prod = [{"sku": f"s{i}", "naam": f"RVS schroef 5 0 x {40 + i * 10} mm", "categorie": "Bevestiging", "rol": "kern", "prijs": 10} for i in range(5)]
    prod.append({"sku": "x", "naam": "RVS torx schroeven 5 0 x 80 mm", "categorie": "Schuttingen > Tuinschermen", "rol": "kern", "prijs": 16})
    prod += [{"sku": f"t{i}", "naam": "Tuinscherm", "categorie": "Schuttingen > Tuinschermen", "rol": "kern", "prijs": 50} for i in range(5)]
    idx = {f"s{i}": 0.8 for i in range(5)} | {f"t{i}": 1.2 for i in range(5)}
    r = categorie_index(prod, idx)
    assert r[0]["marktprijs"] == 20.0 and "zelfde type" in r[0]["toelichting"]


def test_tafelblad_per_m2():
    assert pakket("Eiken tafelblad recht RUSTIEK 160 x 100") == ("tafelblad", 1.6)
    assert pakket("Eiken boomstamtafelblad 220 x 100 rustiek geborsteld")[0] == "tafelblad"
