import pytest

from prijsscraper.catalogus import bepaal_rol, parse_productpagina
from prijsscraper.matcher import kenmerken, koppel, vergelijk


@pytest.mark.parametrize("titel,soort,doorsnede,lengte", [
    ("Douglas plank 22x200 mm 400 cm", "douglas", (22, 200), 400),
    ("Kantplank Douglas bezaagd 2,2 x 20 x 400 cm", "douglas", (22, 200), 400),
    ("Douglas Gedroogd 22x200mm 70% PEFC 400 centimeter", "douglas", (22, 200), 400),
    ("Steigerhout Gekramd C18 32x200x5000mm", "steigerhout", (32, 200), 500),
    ("OSB-3 Constructieplaat TG2 2440x1220x18mm", "osb", (18, 1220, 2440), None),
    ("OSB/3 plaat 18 mm 244x122 cm", "osb", (18, 1220, 2440), None),
    ("Bangkirai vlonderplank 25x145 mm 305 cm", "bangkirai", (25, 145), 305),
])
def test_kenmerken(titel, soort, doorsnede, lengte):
    k = kenmerken(titel)
    assert k.soort == soort and k.doorsnede == tuple(float(x) for x in doorsnede) and k.lengte == lengte


def test_zelfde_product_andere_notatie():
    r = vergelijk(kenmerken("Douglas plank 22x200 mm 400 cm"), kenmerken("Kantplank Douglas bezaagd 2,2 x 20 x 400 cm"))
    assert r and r[0] >= 0.85 and r[1] == 1.0


def test_andere_lengte_wordt_omgerekend():
    r = vergelijk(kenmerken("Douglas plank 22x200 mm 300 cm"), kenmerken("Douglas plank 22x200 mm 400 cm"))
    assert r and r[1] == 0.75


@pytest.mark.parametrize("a,b", [
    ("Douglas plank 22x200 mm 400 cm", "Lariks plank 22x200 mm 400 cm"),          # andere houtsoort
    ("Douglas plank 22x200 mm 400 cm", "Douglas plank 28x195 mm 400 cm"),         # andere doorsnede
    ("Grenen plank geïmpregneerd 22x145 mm 400 cm", "Grenen plank 22x145 mm 400 cm"),  # behandeling
    ("Douglas plank 22x200 mm 200 cm", "Douglas plank 22x200 mm 500 cm"),          # lengte te ver uiteen
])
def test_geen_koppeling(a, b):
    assert vergelijk(kenmerken(a), kenmerken(b)) is None


def test_koppel_wijst_onlogische_prijs_af():
    eigen = [{"sku": "X", "naam": "Douglas plank 22x200 mm 400 cm", "prijs": 20.0}]
    aanbod = [{"concurrent": "a", "titel": "Douglas plank 22 x 200 mm 400 cm", "url": "u1", "prijs_incl": 19.0},
              {"concurrent": "b", "titel": "Douglas plank 22x200mm 400cm (pak van 5)", "url": "u2", "prijs_incl": 95.0}]
    m = koppel(eigen, aanbod)
    assert [x["concurrent"] for x in m] == ["a"]


def test_rol():
    assert bepaal_rol("Steigerhout nieuw 32x200", "") == "trekker"
    assert bepaal_rol("Bangkirai vlonderplank", "") == "marge"
    assert bepaal_rol("Slotbout M10", "Bevestiging") == "kern"


def test_parse_woocommerce_variaties():
    html = '''<script type="application/ld+json">{"@type":"Product","name":"Douglas plank 22x200","sku":"1",
      "offers":{"@type":"Offer","price":"10.00","availability":"https://schema.org/InStock"}}</script>
      <form data-product_variations="[{&quot;attributes&quot;:{&quot;attribute_pa_lengte&quot;:&quot;300-cm&quot;},
      &quot;display_price&quot;:14.95,&quot;display_regular_price&quot;:14.95,&quot;sku&quot;:&quot;1-300&quot;,
      &quot;variation_id&quot;:7,&quot;is_in_stock&quot;:true}]"></form>'''
    r = parse_productpagina("https://x/product/p/", html)
    assert r == [{"sku": "1-300", "naam": "Douglas plank 22x200 300 cm", "categorie": "", "verkoopprijs_incl_btw": 14.95,
                  "van_prijs": 14.95, "op_voorraad": True, "url": "https://x/product/p/?variation_id=7"}]
