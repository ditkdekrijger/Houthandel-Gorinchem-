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
