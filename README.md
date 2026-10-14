# wa-property

A geospatial sourcing pipeline that finds **property development opportunities in Perth, Western Australia**. It surfaces clusters of neighbouring land parcels that are *all* for sale at the same time, which are candidates for amalgamating into one larger development site.

It matches live sale listings against Landgate's cadastre (the official map of land parcels). Then it uses PostGIS to find listed lots that share a real boundary, and estimates the extra dwellings the combined site would allow under WA's R-Code density rules.

> **Status:** working prototype. It's a sourcing pipeline, not a SaaS. It covers ~200 Perth-metro suburbs, 410k+ cadastre parcels and ~7k active listings.

## How it works

```
reiwa.com.au ──scrape──► Listing ──match_lots──► cadastre.Lot ◄──join_rcodes── R-code zones
                            │                        ▲                              ▲
                          delist               import_cadastre             import_rcode_zones
                    (reap gone listings)        (Landgate SLIP)              (Landgate SLIP)
                                                     │
                                                     ▼
                                   /leads/ — clusters of touching, all-listed lots
```

1. **Scrape.** A polite, cached HTTP client (jittered 3–8s delay, on-disk cache) collects reiwa.com.au listings suburb by suburb. Each run records integrity signals (whether it reached the final page, how many search and detail pages failed) in a `ScrapeRun` row.
2. **Delist.** Listings missing from a *completed* run get a miss counter. They are deactivated only after two clean misses, and a run that saw suspiciously few listings is skipped. A single flaky scrape therefore can't mass-deactivate the dataset.
3. **Import cadastre and zoning.** Lot polygons and R-code zones are bulk-imported from Landgate SLIP's authenticated ArcGIS services:
   - The token is refreshed automatically when it expires.
   - Imports are idempotent upserts keyed on Landgate ID.
   - Parcels made of several polygons are merged into one MultiPolygon.
   - Areas are computed in GDA94 / MGA Zone 50 for accuracy.
4. **Spatial join.** Each lot inherits its R-code from the zone polygon it falls in.
5. **Match listings to lots.** This is address-first:
   - The listing's address is parsed and normalised, then looked up in an in-memory index of cadastre addresses.
   - Unit-letter addresses ("31B") fall back to the parent lot when that's unambiguous.
   - When several lots share an address, a point-in-polygon test on the geocoded point decides.
   - Each match records its method and a confidence score.
   - This matches ~84% of matchable (non-strata) listings. A pure geocode/point-in-polygon approach managed ~3%.
6. **Leads.** `/leads/` runs one PostGIS query per request:
   - **Neighbours:** two lots count as neighbours only if they share a boundary edge of at least 1 m (`ST_Touches` plus the length of the shared edge). Lots that only meet at a corner or face each other across a street don't count.
   - **Clusters:** neighbouring lots are grouped with union-find.
   - **Sorting:** clusters are ordered by how recently their newest member was listed.
   - **Annotations:** each cluster shows its total area, its R-codes and whether it is a back-to-back (through-block) site. It also shows dwelling yield before and after amalgamation, based on the R-Codes average site areas (SPP 7.3).

## Stack

- Python 3.13, Django 5 with `django.contrib.gis`
- PostgreSQL + PostGIS
- httpx, selectolax (HTML parsing), python-dotenv
- Leaflet for the spot-check maps

## Setup

```bash
# System dependencies (macOS)
brew install postgresql postgis gdal

createdb wa_property
psql -d wa_property -c 'CREATE EXTENSION postgis;'

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in DB credentials and SLIP login
python manage.py migrate
```

Importing cadastre and zoning data needs a free [Landgate SLIP](https://www.landgate.wa.gov.au/) account (`SLIP_USERNAME` / `SLIP_PASSWORD`).

## Running the pipeline

Each step is an independent, re-runnable management command:

```bash
python manage.py import_cadastre --metro      # Landgate lot polygons (one-off)
python manage.py import_rcode_zones           # R-code zone polygons (one-off)
python manage.py import_zones                 # zones & reserves (one-off)
python manage.py join_rcodes                  # spatial join Lot × R-code zone

python manage.py scrape --suburbs claremont,bayswater
python manage.py delist                       # deactivate listings gone from completed scrapes
python manage.py enrich                       # Nominatim geocoding (point-in-polygon fallback)
python manage.py match_lots                   # listing → cadastre lot

python manage.py runserver
```

Then open:
- `/leads/` for the output.
- `/`, `/rcodes/` and `/lots/` for the Leaflet spot-check maps.

## Project layout

| App | Responsibility |
|---|---|
| `apps.listings` | reiwa scraper, scrape orchestration, delist stage, address parser, listing→lot matcher |
| `apps.cadastre` | Lot / R-code zone / zone models, Landgate SLIP clients, import and spatial-join commands, map views |
| `apps.zoning` | Nominatim geocoder and the R-Codes site-area table used for yield maths |
| `apps.dashboard` | The `/leads/` cluster view |

## Data and usage

This repository contains **code only**. No scraped listing data or Landgate datasets are included. If you run it:
- Follow reiwa.com.au's terms of use and keep request rates low.
- Use Landgate data within the terms of your SLIP licence.

Cluster output is a sourcing aid, not planning or financial advice.

## License

[MIT](LICENSE)
