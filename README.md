![image](static/cres.jpg)

# Trajektolini

Organises all ferry connections advertised by [Jadrolinija](https://www.jadrolinija.hr/) into a [GTFS format](https://gtfs.org/) that can be used more easily for planning trips. The generated GTFS feed is used together with Open Street Maps for combining road and ferry connections in this [demo trip planner](https://trajektolini.metakermit.com/). _Note: just for demonstration purposes, the routing logic is experimental._

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Generating the GTFS feed

```bash
python3 gtfs.py
```

On first run this geocodes all ports via OpenStreetMap Nominatim and saves them to `ports.json` for review. Re-run to regenerate the feed after reviewing `ports.json`. The output is written to `gtfs/jadrolinija_gtfs.zip`.

To verify the generated feed:

```bash
python3 verify_gtfs.py
```

## Tests

```bash
pytest
```

The test suite covers the time helpers and route-finding logic for key city pairs (Zagreb → Hvar, Split → Vis, Rijeka → Cres). A versioned GTFS fixture is checked in under `tests/fixtures/` so the tests can run in CI without generating the feed first.

## Web app

`app.py` is a FastAPI web interface for the route planner.

### Running locally

```bash
uvicorn app:app --reload
```

Then open [http://localhost:8000](http://localhost:8000). The app loads the GTFS feed from `gtfs/jadrolinija_gtfs.zip` on startup — generate it first if you haven't already (see above).

## Route planner

`route.py` finds the best driving + ferry combinations to reach an island destination, combining road routing (OSRM) with the generated GTFS feed.

### Usage

```
python3 route.py [--date YYYY-MM-DD] [--depart-after HH:MM] [--results N] "origin" "destination"
```

| Option | Default | Description |
|---|---|---|
| `--date` | today | Travel date |
| `--depart-after` | `00:00` | Earliest departure time from origin |
| `--results` | `3` | Number of options to show |

### Examples

Find routes from Rijeka to Bol on Brač, leaving today:

```bash
python3 route.py "Rijeka" "Bol"
```
```
============================================================
  Rijeka  →  Bol
============================================================

Option 1  —  total ~6h 37min
  🚗  Drive to port     4h 36min  (413 km)  →  Split
  ⛴️   Ferry            05:00 → 05:50  (50min)  [06 Apr]  SPLIT-SUPETAR
  🚗  Drive to dest     47min  (35 km)  from Supetar

Option 2  —  total ~10h 35min
  🚗  Drive to port     5h 08min  (460 km)  →  Makarska
  ⛴️   Ferry            09:00 → 10:00  (1h 00min)  [06 Apr]  MAKARSKA-SUMARTIN
  🚗  Drive to dest     35min  (26 km)  from Sumartin

Option 3  —  total ~17h 41min
  🚗  Drive to port     4h 36min  (413 km)  →  Split
  ⛴️   Ferry            16:30 → 17:35  (1h 05min)  [06 Apr]  JELSA-BOL-SPLIT
  🚗  Drive to dest     6min  (2 km)  from Bol
```

Find routes from Zagreb to Hvar, leaving on a specific date:

```bash
python3 route.py --date 2026-05-01 "Zagreb" "Hvar"
```
```
============================================================
  Zagreb  →  Hvar
============================================================

Option 1  —  total ~10h 25min
  🚗  Drive to port     5h 38min  (485 km)  →  Drvenik
  ⛴️   Ferry            08:15 → 08:45  (30min)  [01 May]  DRVENIK-SUĆURAJ
  🚗  Drive to dest     1h 40min  (64 km)  from Sućuraj

Option 2  —  total ~10h 26min
  🚗  Drive to port     4h 29min  (409 km)  →  Split
  ⛴️   Ferry            08:30 → 10:20  (1h 50min)  [01 May]  SPLIT-STARI GRAD
  🚗  Drive to dest     6min  (3 km)  from Stari Grad

Option 3  —  total ~18h 21min
  🚗  Drive to port     4h 29min  (409 km)  →  Split
  ⛴️   Ferry            16:30 → 18:00  (1h 30min)  [01 May]  JELSA-BOL-SPLIT
  🚗  Drive to dest     21min  (15 km)  from Jelsa
```

Find routes from Split to Vis after work on a Friday:

```bash
python3 route.py --date 2026-04-10 --depart-after 17:00 "Split" "Vis"
```
```
============================================================
  Split  →  Vis
============================================================

Option 1  —  total ~6h 10min
  🚗  Drive to port     4min  (2 km)  →  Split
  ⛴️   Ferry            11:00 → 13:20  (2h 20min)  [11 Apr]  VIS-SPLIT
  🚗  Drive to dest     3h 45min  (7 km)  from Vis
```

Show more options for a route from Zagreb to Martinšćica on Cres:

```bash
python3 route.py --date 2026-04-06 --depart-after 12:00 --results 5 "Zagreb" "Martinšćica, Cres"
```
```
============================================================
  Zagreb  →  Martinšćica, Cres
============================================================

Option 1  —  total ~4h 26min
  🚗  Drive to port     2h 00min  (160 km)  →  Rijeka
  ⛴️   Ferry            14:30 → 15:50  (1h 20min)  [06 Apr]  M.LOŠINJ-UNIJE-CRES-RIJEKA
  🚗  Drive to dest     36min  (29 km)  from Cres

Option 2  —  total ~4h 32min
  🚗  Drive to port     2h 27min  (183 km)  →  Valbiska
  ⛴️   Ferry            15:15 → 15:40  (25min)  [06 Apr]  VALBISKA-MERAG
  🚗  Drive to dest     52min  (40 km)  from Merag

Option 3  —  total ~4h 35min
  🚗  Drive to port     2h 00min  (160 km)  →  Rijeka
  ⛴️   Ferry            14:30 → 16:35  (2h 05min)  [06 Apr]  M.LOŠINJ-UNIJE-CRES-RIJEKA
  🚗  Drive to dest     0min  (0 km)  from Martinšćica

Option 4  —  total ~5h 11min
  🚗  Drive to port     3h 00min  (204 km)  →  Brestova
  ⛴️   Ferry            15:45 → 16:05  (20min)  [06 Apr]  BRESTOVA-POROZINA
  🚗  Drive to dest     1h 06min  (52 km)  from Porozina
```
