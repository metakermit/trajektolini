#!/usr/bin/env python3
"""Generate a GTFS Schedule feed from Jadrolinija ferry data."""

import csv
import json
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

from scrape import get_session, get_departure_points, get_destination_points, search_voyages

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "jadrolinija-gtfs/0.1 (github.com/jadrolinija)"

# How many days ahead to scrape for voyages
SCRAPE_DAYS = 90

# Max results per SearchVoyages call — high enough to cover the full window
VOYAGE_MAX_RESULTS = 300

# GTFS output directory
OUTPUT_DIR = Path("gtfs")

# Country code -> full name for Nominatim queries
COUNTRY_NAMES = {
    "HR": "Croatia",
    "IT": "Italy",
    "ME": "Montenegro",
    "BA": "Bosnia and Herzegovina",
    "GR": "Greece",
    "AL": "Albania",
}


# ---------------------------------------------------------------------------
# Port geocoding
# ---------------------------------------------------------------------------

def _clean_name(name: str) -> str:
    """Strip parenthetical qualifiers, e.g. 'ZADAR (GAŽENICA)' -> 'ZADAR'."""
    return re.sub(r'\s*\([^)]*\)', '', name).strip()


def geocode_port(code: str, name: str, country_code: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a port, or None if not found."""
    country = COUNTRY_NAMES.get(country_code, country_code)
    base = _clean_name(name).title()
    queries = [
        f"ferry terminal {name} {country}",
        f"{base} ferry port {country}",
        f"{base} {country}",
    ]
    for q in queries:
        r = requests.get(NOMINATIM_URL,
                         params={"q": q, "format": "json", "limit": 1},
                         headers={"User-Agent": NOMINATIM_UA})
        results = r.json()
        time.sleep(1.1)  # Nominatim rate limit: 1 req/s
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    return None


def resolve_port_coordinates(ports: list[dict]) -> dict[str, tuple[float, float]]:
    coords: dict[str, tuple[float, float]] = {}
    for i, p in enumerate(ports, 1):
        code = p["Code"]
        name = p["Desc"]
        cc = p.get("CountryCode", "")
        print(f"  [{i}/{len(ports)}] {code} ({name})...", end=" ", flush=True)
        result = geocode_port(code, name, cc)
        if result:
            coords[code] = result
            print(f"{result[0]:.4f}, {result[1]:.4f}")
        else:
            print("NOT FOUND — will be skipped in GTFS")
    return coords


# ---------------------------------------------------------------------------
# Step 2: Scrape voyages
# ---------------------------------------------------------------------------

def scrape_all_voyages(session, today: str, cutoff: str) -> list[dict]:
    """
    Fetch all voyages across all (departure, destination) pairs starting from
    today, filtered to those departing before cutoff (inclusive).
    Returns a flat list of raw voyage dicts from the API.
    """
    dep_points = get_departure_points(session, today)
    print(f"  {len(dep_points)} departure points found.")

    all_voyages: list[dict] = []
    seen_voyage_ids: set = set()
    pair_count = 0

    for i, dep in enumerate(dep_points, 1):
        dep_code = dep["Code"]
        dest_points = get_destination_points(session, dep, today)
        pair_count += len(dest_points)

        for dest in dest_points:
            voyages = search_voyages(session, dep, dest, today, VOYAGE_MAX_RESULTS)

            for v in voyages:
                dep_time = v.get("DepartureTime", "")
                if not dep_time:
                    continue
                # Filter to the scrape window
                dep_date = dep_time[:10]
                if dep_date > cutoff:
                    continue
                vid = v.get("ID")
                if vid in seen_voyage_ids:
                    continue
                seen_voyage_ids.add(vid)
                all_voyages.append(v)

        print(f"  [{i}/{len(dep_points)}] {dep_code}: "
              f"{len(dest_points)} destinations, {len(all_voyages)} voyages so far")

    print(f"  Scraped {len(all_voyages)} unique voyages across {pair_count} route pairs.")
    return all_voyages


# ---------------------------------------------------------------------------
# Step 3: Write GTFS files
# ---------------------------------------------------------------------------

def _gtfs_time(iso: str, trip_date: str) -> str:
    """
    Convert ISO datetime string to GTFS HH:MM:SS, relative to trip_date (YYYY-MM-DD).
    For overnight trips, hours beyond midnight are expressed as 24+ (e.g. 31:00:00).
    """
    day_offset = (date.fromisoformat(iso[:10]) - date.fromisoformat(trip_date)).days
    hours = int(iso[11:13]) + day_offset * 24
    return f"{hours:02d}{iso[13:19]}"


def write_agency(writer_fn):
    writer_fn("agency.txt", [
        ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "agency_phone"],
        ["JDLN", "Jadrolinija", "https://www.jadrolinija.hr", "Europe/Zagreb", "hr", "+385 51 666 111"],
    ])


def write_stops(writer_fn, port_data: list[dict]):
    rows = [["stop_id", "stop_name", "stop_lat", "stop_lon"]]
    for p in port_data:
        if p["lat"] is None:
            continue
        rows.append([p["code"], p["name"].title(), p["lat"], p["lon"]])
    writer_fn("stops.txt", rows)
    return len(rows) - 1


def write_routes(writer_fn, voyages: list[dict]) -> dict[str, dict]:
    """Write routes.txt. Returns dict of route_id -> line info."""
    seen: dict[str, dict] = {}
    for v in voyages:
        line = v.get("Line") or {}
        route_id = line.get("LineCode", "")
        if not route_id or route_id in seen:
            continue
        seen[route_id] = {
            "route_id": route_id,
            "route_long_name": line.get("LineName", ""),
            "route_type": "4",  # Ferry
        }

    rows = [["route_id", "agency_id", "route_long_name", "route_type"]]
    for r in sorted(seen.values(), key=lambda x: x["route_id"]):
        rows.append([r["route_id"], "JDLN", r["route_long_name"], r["route_type"]])

    writer_fn("routes.txt", rows)
    return seen


def write_trips_and_times(writer_fn, legs: list[dict], valid_stop_ids: set):
    """
    Write trips.txt, stop_times.txt, and calendar_dates.txt.

    Each API result is a leg (one port-to-port segment of a voyage). Legs sharing
    the same VoyageId belong to the same physical trip. We group them and reconstruct
    the full stop sequence using DeparturePortSequence / DestinationPortSequence.
    """
    # Group legs by VoyageId
    by_voyage: dict[int, list[dict]] = {}
    for leg in legs:
        vid = leg["VoyageId"]
        by_voyage.setdefault(vid, []).append(leg)

    trip_rows = [["route_id", "service_id", "trip_id", "trip_headsign"]]
    stop_time_rows = [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]]
    cal_rows = [["service_id", "date", "exception_type"]]

    skipped_trips = 0

    for vid, voyage_legs in by_voyage.items():
        # Reconstruct port stops: seq_num -> {port_code, arrival_time, departure_time}
        stops: dict[int, dict] = {}
        line = (voyage_legs[0].get("Line") or {})
        route_id = line.get("LineCode", "")

        for leg in voyage_legs:
            dep_seq = leg["DeparturePortSequence"]
            dest_seq = leg["DestinationPortSequence"]
            dep_code = leg["DeparturePort"]["PortCode"]
            dest_code = leg["DestinationPort"]["PortCode"]

            if dep_seq not in stops:
                stops[dep_seq] = {"port_code": dep_code, "arrival_time": None, "departure_time": None}
            stops[dep_seq]["departure_time"] = leg["DepartureTime"]

            if dest_seq not in stops:
                stops[dest_seq] = {"port_code": dest_code, "arrival_time": None, "departure_time": None}
            stops[dest_seq]["arrival_time"] = leg["ArrivalTime"]

        # Skip trip if any stop port has no coordinates
        port_codes = {s["port_code"] for s in stops.values()}
        if not port_codes.issubset(valid_stop_ids):
            skipped_trips += 1
            continue

        trip_id = str(vid)
        service_id = trip_id
        sorted_stops = sorted(stops.items())  # by seq_num

        # Determine departure date from the first stop's departure time
        first_dep_time = sorted_stops[0][1]["departure_time"] or ""
        dep_date = first_dep_time[:10].replace("-", "")

        # Headsign is the name of the last stop
        last_port_code = sorted_stops[-1][1]["port_code"]
        last_leg = next(
            (l for l in voyage_legs if l["DestinationPort"]["PortCode"] == last_port_code), None
        )
        headsign = (last_leg["DestinationPort"]["PortName"].title() if last_leg else last_port_code)

        trip_rows.append([route_id, service_id, trip_id, headsign])
        cal_rows.append([service_id, dep_date, "1"])

        for gtfs_seq, (_, stop) in enumerate(sorted_stops, 1):
            arr = stop["arrival_time"] or stop["departure_time"]
            dep = stop["departure_time"] or stop["arrival_time"]
            trip_start = first_dep_time[:10]
            stop_time_rows.append([
                trip_id, _gtfs_time(arr, trip_start), _gtfs_time(dep, trip_start),
                stop["port_code"], str(gtfs_seq),
            ])

    writer_fn("trips.txt", trip_rows)
    writer_fn("stop_times.txt", stop_time_rows)
    writer_fn("calendar_dates.txt", cal_rows)

    trip_count = len(trip_rows) - 1
    if skipped_trips:
        print(f"  Skipped {skipped_trips} trips with unresolved port coordinates.")
    return trip_count


def write_feed_info(writer_fn, today: str, cutoff: str):
    writer_fn("feed_info.txt", [
        ["feed_publisher_name", "feed_publisher_url", "feed_lang",
         "feed_start_date", "feed_end_date", "feed_version"],
        ["Jadrolinija", "https://www.jadrolinija.hr", "hr",
         today.replace("-", ""), cutoff.replace("-", ""), today.replace("-", "")],
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=SCRAPE_DAYS)).isoformat()

    # ------------------------------------------------------------------
    # Load ports.json (produced by Step 1 — run once, edit as needed)
    # ------------------------------------------------------------------
    ports_path = Path("ports.json")
    if not ports_path.exists():
        print("ports.json not found. Running Step 1 first...\n")
        print("=== Step 1: Discover ports ===")
        print("Authenticating...", end=" ", flush=True)
        session = get_session()
        print("OK")

        print("Fetching all departure points...")
        raw_ports = get_departure_points(session, today)
        print(f"Found {len(raw_ports)} departure points.")

        print("\nResolving port coordinates via OpenStreetMap Nominatim...")
        coords = resolve_port_coordinates(raw_ports)
        print(f"\nResolved {len(coords)}/{len(raw_ports)} ports.")

        port_data = []
        for p in raw_ports:
            code = p["Code"]
            entry = {
                "code": code,
                "name": p["Desc"],
                "country_code": p.get("CountryCode", ""),
                "island": p.get("IslandName") or "",
            }
            if code in coords:
                entry["lat"], entry["lon"] = coords[code]
            else:
                entry["lat"] = entry["lon"] = None
            port_data.append(entry)

        with open(ports_path, "w", encoding="utf-8") as f:
            json.dump(port_data, f, indent=2, ensure_ascii=False)
        print(f"\nPort data saved to {ports_path}")
        print("Review it (especially ports with null lat/lon) and re-run to generate GTFS.\n")
        return
    else:
        with open(ports_path, encoding="utf-8") as f:
            port_data = json.load(f)
        print(f"Loaded {len(port_data)} ports from {ports_path}")

    valid_stop_ids = {p["code"] for p in port_data if p["lat"] is not None}
    print(f"{len(valid_stop_ids)} ports have coordinates and will appear in stops.txt")

    # ------------------------------------------------------------------
    # Step 2: Scrape voyages
    # ------------------------------------------------------------------
    print(f"\n=== Step 2: Scrape voyages ({today} → {cutoff}) ===")
    print("Authenticating...", end=" ", flush=True)
    session = get_session()
    print("OK\n")

    voyages = scrape_all_voyages(session, today, cutoff)

    # ------------------------------------------------------------------
    # Step 3: Write GTFS files
    # ------------------------------------------------------------------
    print(f"\n=== Step 3: Write GTFS files ===")

    csv_files: dict[str, list] = {}

    def collect(filename, rows):
        csv_files[filename] = rows

    write_agency(collect)
    n_stops = write_stops(collect, port_data)
    write_routes(collect, voyages)
    n_trips = write_trips_and_times(collect, voyages, valid_stop_ids)
    write_feed_info(collect, today, cutoff)

    # Write individual CSV files and a zip
    for filename, rows in csv_files.items():
        path = OUTPUT_DIR / filename
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)

    zip_path = OUTPUT_DIR / "jadrolinija_gtfs.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in csv_files:
            zf.write(OUTPUT_DIR / filename, filename)

    print(f"  stops.txt       — {n_stops} stops")
    print(f"  routes.txt      — {len(csv_files.get('routes.txt', [[]])) - 1} routes")
    print(f"  trips.txt       — {n_trips} trips")
    print(f"  stop_times.txt  — {n_trips * 2} stop times")
    print(f"  calendar_dates.txt, agency.txt, feed_info.txt")
    print(f"\nGTFS feed written to {zip_path}")


if __name__ == "__main__":
    main()
