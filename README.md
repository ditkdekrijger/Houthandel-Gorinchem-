# Prijsmonitor Houthandel Gorinchem

Vergelijkt de eigen verkoopprijzen elke dag met de top-10 concurrenten en levert
**± 20 onderbouwde prijsvoorstellen per dag**, zowel verhogingen (marge pakken waar de markt
ruimte laat) als verlagingen (prijspositie herstellen waar we uit de markt lopen).

```
concurrent-sites ─▶ scraper ─▶ prijshistorie (SQLite) ─▶ prijsengine ─▶ top-20 voorstellen
                                                                       ├─ rapport.html (akkoord aanvinken + export)
                                                                       └─ voorstellen.csv (import kassa/webshop)
```

## Snel starten

```bash
pip install -r requirements.txt
python -m prijsscraper demo          # proefrun op gesimuleerde data (84 artikelen, 10 concurrenten, 30 dagen)
open data/demo/output/*/rapport.html
```

Live gebruik:

```bash
python -m prijsscraper dagelijks     # scrape + analyse + rapport in output/JJJJ-MM-DD/
```

## Webomgeving

```bash
python -m prijsscraper web                       # http://127.0.0.1:8080
python -m prijsscraper web --export rapport.html # zelfstandige, deelbare versie
```

| Scherm | Wat |
|---|---|
| **Vandaag** | De voorstellen van vandaag. Per voorstel akkoord of afwijzen, met onderbouwing, de prijzen van de concurrenten en een grafiek van 30 dagen. Bovenaan staat **Exporteer akkoord**. |
| **Artikelen** | Het hele assortiment met een prijsindex t.o.v. de markt (boven 100 = duurder), marge en trend. Klik op een artikel voor details. |
| **Concurrenten** | De status per concurrent (actief, geblokkeerd of storing), hun prijsindex en hoeveel scrapes lukten. |
| **Signalen** | Wat de engine niet zelf oplost: markt onder onze margebodem, verdachte koppelingen of te weinig data. |

In de servermodus worden beoordelingen in de database opgeslagen, zodat `doorvoeren` ze oppakt.
In de geëxporteerde versie blijven ze in de browser. De vormgeving volgt de beeldtaal van de
HoutUnie-groep (Jost/Barlow, steen, inkt en maple), zonder hun logo.

Voorbeelden: `docs/omgeving-voorbeeld.html` (gesimuleerd) en `docs/omgeving-live-run-1.html` (echte run 1).

## Inrichting (eenmalig)

| Bestand | Wat |
|---|---|
| `config/producten.csv` | Eigen assortiment: inkoopprijs (excl. btw), verkoopprijs (incl. btw), **rol** en afzet per week. Export uit kassa/ERP. |
| `config/concurrenten.yaml` | De top-10 concurrenten met gewicht (relevantie) en of hun prijzen incl./excl. btw zijn. **Lijst is een voorstel: valideren met verkoop.** |
| `config/matches.csv` | Per artikel de productpagina-URL bij elke concurrent + `omrekenfactor` (bijv. 250 cm ↔ 300 cm = 1,2; prijs per 4 m ↔ per m1 = 0,25). |
| `config/strategie.yaml` | Spelregels: aantal voorstellen, margebodems, max. stap, afronding, positionering per rol. |

**Matches aanleggen** is het meeste werk en bepaalt de kwaliteit. Begin met de ±100 best verkopende
artikelen. Controleer een URL met:

```bash
python -m prijsscraper test-url "https://www.voorbeeld.nl/product/123" --concurrent gamma
```

De scraper leest automatisch JSON-LD (schema.org), microdata en meta-tags, wat vrijwel alle grote
webshops gebruiken. Werkt dat niet voor een site, zet dan CSS-selectors in `concurrenten.yaml`.

### Artikelrollen

| Rol | Voor | Positionering (standaard) |
|---|---|---|
| `trekker` | Artikelen waar klanten op vergelijken (steigerhout, OSB, vurenhout) | net onder het goedkoopste kwart, min. 12% marge |
| `kern` | Normaal assortiment | 2% onder de marktmediaan, min. 20% marge |
| `marge` | Specialistisch, service, lastig vergelijkbaar (hardhout, gevelbekleding) | 4% boven de mediaan, min. 30% marge |

## Hoe de voorstellen tot stand komen

1. **Verse concurrentprijzen** (≤ 48 uur), omgerekend naar onze eenheid en incl. btw. Niet-op-voorraad telt niet mee.
2. **Data-kwaliteit**: prijzen > 45% naast de mediaan zijn vrijwel altijd een verkeerde match → genegeerd en gemeld.
   **Tijdelijke acties** (doorgestreepte prijs of > 15% onder de eigen 30-dagen-mediaan) tellen half mee,
   zodat we niet achter elke weekendactie aan rennen.
3. **Marktreferentie** per rol (gewogen naar relevantie van de concurrent) × index = doelprijs.
4. **Begrenzing**: nooit onder de margebodem, nooit > 5% boven de duurste concurrent,
   max. 8% per stap, niet vaker dan 1× per 3 dagen per artikel, wijzigingen < 1,5% zijn ruis.
5. **Psychologische afronding** (…,49 / …,95 / …,99), altijd richting de huidige prijs.
6. **Impact** op brutowinst per week via prijselasticiteit per rol. Bij verlagingen telt ook de
   omzet mee die we riskeren door te duur te zijn (instelbaar met `strategische_weging`).
7. **Selectie**: de 20 hoogst scorende, met minimaal 25% verhogingen én 25% verlagingen.

Daarnaast toont het rapport **signalen**: concurrenten onder onze margebodem (inkoop heroverwegen),
verdachte matches en artikelen met te weinig marktdata.

## Dagelijkse workflow

1. 06:15 draait de run automatisch (GitHub Actions, `.github/workflows/prijsmonitor.yml`, of cron:
   `15 6 * * * cd /pad && python -m prijsscraper dagelijks`).
2. Inkoper/verkoper opent `rapport.html`, vinkt voorstellen uit die niet door moeten en klikt
   **Exporteer goedgekeurde prijzen**.
3. Goedkeuren in het systeem en prijzen bijwerken:
   ```bash
   python -m prijsscraper akkoord --csv goedgekeurde-prijzen-2026-11-14.csv
   python -m prijsscraper doorvoeren     # zet de prijzen in config/producten.csv
   ```
4. Zet de nieuwe prijzen in kassa/webshop (of bouw een koppeling, zie hieronder).

## Status live (run 1, 14-11-2026)

| Concurrent | Status | Toelichting |
|---|---|---|
| Bouwmaat | ✅ werkt | JSON-LD, prijzen excl. btw (wordt omgerekend). Lengtes via `?variant=<id>` in de URL. |
| Online Tuinhout | ✅ werkt | CSS-selector `#base_price`. Gebruik URL's mét lengte. |
| Gadero | ⚠️ beperkt | Alleen productpagina's met lengte in de URL (eindigend op `-prod`) hebben een prijs. |
| HORNBACH | ❌ geblokkeerd | JavaScript-botcheck. |
| GAMMA, KARWEI | ❌ geblokkeerd | HTTP 429 (botbescherming). |
| HouthandelOnline | ❌ geblokkeerd | HTTP 403. |
| Praxis, Bouwbestel | ❌ geblokkeerd | Weigeren onze (eerlijke) bot-identificatie. |
| OPMAATZAGEN.nl | ⚠️ niet vergelijkbaar | Toont "vanaf"-prijs voor zaagwerk op maat, geen prijs per hele plaat. |

Geblokkeerde sites worden **niet** omzeild. Alternatieven: een prijsdata-leverancier,
handmatige steekproeven, of de site om toestemming vragen.

Let op: een prijs die alleen via de heuristiek gevonden wordt krijgt status `onzeker` en telt
**niet** mee. In run 1 pakte de heuristiek bij Online Tuinhout de prijs van een gerelateerd product.

## Volledig assortiment + impact (15-11-2026)

```bash
python3 -m prijsscraper --config data/live/config importeer --uit data/live/config/producten.csv --omzet 10000000 --marge 0.17
python3 -m prijsscraper --config data/live/config aanbod --uit data/live/aanbod.jsonl      # catalogi concurrenten
python3 -m prijsscraper --config data/live/config --db data/live/prijzen.db koppel --aanbod data/live/aanbod.jsonl
python3 -m prijsscraper --config data/live/config --db data/live/prijzen.db --uitvoer data/live/output voorstellen
python3 -m prijsscraper --config data/live/config --db data/live/prijzen.db web --export docs/omgeving-live.html
```

| | |
|---|---|
| Eigen artikelen (incl. lengtevarianten) | 2.623 uit 1.287 productpagina's |
| Concurrentproducten ingeladen | 139.020 van 12 webshops |
| Automatisch gekoppeld | 276 artikelen (134 met 2+ concurrenten), ca. 10,5% van de omzet |
| Mediane prijsindex | 91 (Gorinchem ca. 9% onder de markt) |
| Voorstellen run | 20 (15 omhoog, 5 omlaag) |
| Indicatie extra brutowinst (realistisch, hele assortiment) | € 58k – € 207k per jaar |

Inkoopprijzen en afzet per artikel zijn **geschat** (17% marge, € 10 mln omzet gelijk verdeeld).
Met een ERP-export (inkoop, afzet per artikel) worden voorstellen en impact exact.

## Volgende stappen

- **Koppeling kassa/webshop**: `producten.csv` automatisch vullen vanuit het ERP en goedgekeurde
  prijzen terugschrijven (API van WooCommerce/Lightspeed/Shopify/ERP).
- **Echte afzetdata** gebruiken om de elasticiteit per categorie te schatten i.p.v. de standaardwaarden.
- **Match-assistent**: automatisch kandidaat-URL's zoeken op EAN/naam via de zoekfunctie van concurrenten.
- **Juridisch**: alleen publiek zichtbare prijzen, robots.txt wordt gerespecteerd en we scrapen
  beleefd (1 request per 4 s per site). Check per site de gebruiksvoorwaarden.

## Ontwikkeling

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

```
prijsscraper/
  scraper/fetch.py     beleefde HTTP-client (robots.txt, rate-limit, retries)
  scraper/extract.py   prijs-extractie (JSON-LD, microdata, meta, CSS, heuristiek)
  scraper/runner.py    scrape-run, parallel per concurrent
  pricing.py           prijsengine en selectie
  report.py            HTML-rapport en CSV
  db.py                SQLite-opslag
  demo.py              simulatiedata
  cli.py               commando's
```
