# Jadrolinija GTFS

Organises all ferry connections advertised by Jadrolinija ([source](https://www2.jadrolinija.hr/Voyager2Web/)) into a GTFS format that can be used more easily for planning trips.

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
python3 route.py "Rijeka" "Bol, Brač"
```

Find routes from Zagreb to Hvar, leaving on a specific date:

```bash
python3 route.py --date 2026-05-01 "Zagreb" "Hvar"
```

Find routes from Split to Vis after work on a Friday:

```bash
python3 route.py --date 2026-04-10 --depart-after 17:00 "Split" "Vis"
```

Show more options for a route from Zagreb to Martinšćica on Cres:

```bash
python3 route.py --date 2026-04-06 --depart-after 12:00 --results 5 "Zagreb" "Martinšćica, Cres"
```