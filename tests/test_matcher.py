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
                  "van_prijs": None, "op_voorraad": True, "url": "https://x/product/p/?variation_id=7"}]


def test_parse_variaties_uit_js_variabele():
    html = '''<script type="application/ld+json">{"@type":"Product","name":"Douglas Balk 50 x 100 mm","sku":"18828",
      "offers":{"@type":"Offer","price":"11.67"}}</script>
      <script>var productID = '2650';
      var variations = [{"attributes":{"attribute_pa_lengtemaat":"300-cm"},"display_price":11.67,
        "display_regular_price":12.5,"sku":"18828-01","variation_id":2653,"is_in_stock":true},
       {"attributes":{"attribute_pa_lengtemaat":"500-cm"},"display_price":19.45,"display_regular_price":19.45,
        "sku":"18828-02","variation_id":14645,"is_in_stock":false}];</script>'''
    r = parse_productpagina("https://x/product/balk/", html)
    assert [(x["sku"], x["naam"], x["verkoopprijs_incl_btw"], x["van_prijs"], x["op_voorraad"]) for x in r] == [
        ("18828-01", "Douglas Balk 50 x 100 mm 300 cm", 11.67, 12.5, True),
        ("18828-02", "Douglas Balk 50 x 100 mm 500 cm", 19.45, None, False)]
    assert kenmerken(r[0]["naam"]).lengte == 300


def test_afronding_en_strengere_dikte():
    # 0.7 + 0.1 - 0.05 moet precies 0.75 zijn (geen float-afwijking eronder)
    r = vergelijk(kenmerken("Bangkirai vlonderplank 21 x 145 mm 244 cm"),
                  kenmerken("Vlonderplank bankirai 2,1 x 14,5 x 245 cm"))
    assert r and r[0] >= 0.75
    # 16 mm vs 18 mm is een ander product
    assert vergelijk(kenmerken("Douglas plank 18 x 145 mm 400 cm"), kenmerken("Douglas plank 16 x 140 mm 400 cm")) is None


def test_schermen_vereisen_zelfde_aantal_planken():
    a = kenmerken("Tuinscherm douglas 15-planks 180 x 180 cm")
    assert vergelijk(a, kenmerken("Tuinscherm douglas 19-planks 180 x 180 cm")) is None
    assert vergelijk(kenmerken("Tuinscherm Oostende, vuren 180 x 180 cm"),
                     kenmerken("Tuinscherm Oostende 180x180cm vuren schutting"))[0] >= 0.75
    assert vergelijk(a, kenmerken("Douglas tuinscherm 15 planks 180x180 cm"))[0] >= 0.75


def test_composiet_niet_tegen_aluminium():
    assert vergelijk(kenmerken("Composiet eindprofiel 58 x 58 mm 290 cm ceder"),
                     kenmerken("Aluminium Hoekprofiel 60 x 60 mm Red Cedar 300 cm")) is None
