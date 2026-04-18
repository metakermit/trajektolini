#!/usr/bin/env python3
"""
Multimodal route planner: driving + Jadrolinija ferry.

Usage:
    python route.py "Rijeka" "Bol, Brač"
    python route.py "Zagreb" "Hvar" --date 2026-04-10
    python route.py "Split" "Vis" --results 5
"""

import argparse
import csv
import io
import json
import math
import os
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

GTFS_ZIP = Path(os.environ.get("GTFS_ZIP_PATH", "gtfs/jadrolinija_gtfs.zip"))
PORTS_JSON = Path(os.environ.get("PORTS_JSON_PATH", "ports.json"))
PHOTON = "https://photon.komoot.io/api/"
OSRM = "https://router.project-osrm.org/route/v1/driving"
HEADERS = {"User-Agent": "jadrolinija-route/0.1"}

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


class RouteError(Exception):
    pass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass
class FerryTrip:
    trip_id: str
    route_name: str
    dep_stop: str
    arr_stop: str
    dep_time: str   # HH:MM:SS (may be 24h+)
    arr_time: str
    dep_date: str   # YYYYMMDD


@dataclass
class Route:
    drive_to_port: dict        # {duration_s, distance_m, port: Stop}
    ferry: FerryTrip
    drive_from_port: dict      # {duration_s, distance_m}
    dep_port: Stop
    arr_port: Stop
    total_seconds: int


# ---------------------------------------------------------------------------
# GTFS loader
# ---------------------------------------------------------------------------

def load_gtfs(path: Path) -> dict:
    """Load stops, trips and a departure index from the GTFS zip."""
    data = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            with zf.open(name) as f:
                data[name] = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))

    # Load island membership from ports.json
    ports_json = PORTS_JSON
    stop_island: dict[str, str] = {}  # stop_id -> island name (empty = mainland)
    if ports_json.exists():
        with open(ports_json, encoding="utf-8") as f:
            for p in json.load(f):
                stop_island[p["code"]] = (p.get("island") or "").strip()

    stops: dict[str, Stop] = {}
    for row in data["stops.txt"]:
        stops[row["stop_id"]] = Stop(
            stop_id=row["stop_id"],
            name=row["stop_name"],
            lat=float(row["stop_lat"]),
            lon=float(row["stop_lon"]),
        )

    # route_id -> route_long_name
    route_names = {r["route_id"]: r["route_long_name"] for r in data["routes.txt"]}

    # trip_id -> route_id
    trip_route = {r["trip_id"]: r["route_id"] for r in data["trips.txt"]}

    # trip_id -> service date (YYYYMMDD)
    trip_date = {r["service_id"]: r["date"] for r in data["calendar_dates.txt"]}

    # Build stop_times grouped by trip_id, sorted by stop_sequence
    trip_stops: dict[str, list[dict]] = {}
    for row in data["stop_times.txt"]:
        trip_stops.setdefault(row["trip_id"], []).append(row)
    for tid in trip_stops:
        trip_stops[tid].sort(key=lambda r: int(r["stop_sequence"]))

    # Build a departure index: (dep_stop_id, arr_stop_id) -> list[FerryTrip]
    departures: dict[tuple, list[FerryTrip]] = {}
    for trip_id, stop_rows in trip_stops.items():
        route_id = trip_route.get(trip_id, "")
        rname = route_names.get(route_id, route_id)
        svc_date = trip_date.get(trip_id, "")

        for i, dep_row in enumerate(stop_rows):
            for arr_row in stop_rows[i + 1:]:
                if dep_row["stop_id"] == arr_row["stop_id"]:
                    continue  # skip self-loops
                key = (dep_row["stop_id"], arr_row["stop_id"])
                departures.setdefault(key, []).append(FerryTrip(
                    trip_id=trip_id,
                    route_name=rname,
                    dep_stop=dep_row["stop_id"],
                    arr_stop=arr_row["stop_id"],
                    dep_time=dep_row["departure_time"],
                    arr_time=arr_row["arrival_time"],
                    dep_date=svc_date,
                ))

    # Sort each list by date then departure time
    for trips in departures.values():
        trips.sort(key=lambda t: (t.dep_date, t.dep_time))

    return {"stops": stops, "departures": departures, "stop_island": stop_island}


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def get_island_from_osm(lat: float, lon: float) -> str | None:
    """Return the name of the island containing (lat, lon), or None if not on an island.

    Tries multiple Overpass endpoints before falling back to a nearest-port heuristic.
    """
    query = (
        f"[out:json];"
        f"is_in({lat},{lon})->.a;"
        f"rel(pivot.a)[\"place\"=\"island\"];"
        f"out tags;"
    )
    for endpoint in _OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                r = requests.post(endpoint, data={"data": query}, headers=HEADERS, timeout=20)
                if r.status_code == 429 or not r.text.strip().startswith("{"):
                    time.sleep(5 * (attempt + 1))
                    continue
                elements = r.json().get("elements", [])
                if elements:
                    return elements[0].get("tags", {}).get("name")
                return None  # on mainland
            except requests.exceptions.RequestException:
                time.sleep(5 * (attempt + 1))
    return _island_from_nearest_port(lat, lon)


def _island_from_nearest_port(lat: float, lon: float) -> str | None:
    """Fallback: infer island membership from the nearest Jadrolinija port in ports.json."""
    if not PORTS_JSON.exists():
        raise RouteError("Overpass API unavailable. Please try again shortly.")
    with open(PORTS_JSON, encoding="utf-8") as f:
        ports = json.load(f)
    if not ports:
        raise RouteError("Overpass API unavailable. Please try again shortly.")
    nearest = min(ports, key=lambda p: _haversine_km(lat, lon, p["lat"], p["lon"]))
    if _haversine_km(lat, lon, nearest["lat"], nearest["lon"]) > 50:
        raise RouteError(
            "Overpass API unavailable and destination is too far from any known port. "
            "Please try again shortly."
        )
    return (nearest.get("island") or "").strip() or None


def geocode(query: str) -> tuple[float, float, str]:
    """Return (lat, lon, display_name) for a free-text query."""
    for attempt in range(3):
        try:
            r = requests.get(PHOTON, params={"q": query, "limit": 1},
                             headers=HEADERS, timeout=10)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            features = r.json().get("features", [])
            if not features:
                raise RouteError(f"Could not geocode: '{query}'")
            feat = features[0]
            lon, lat = feat["geometry"]["coordinates"]
            props = feat["properties"]
            name = ", ".join(filter(None, [props.get("name"), props.get("city"), props.get("country")]))
            return float(lat), float(lon), name
        except RouteError:
            raise
        except requests.exceptions.RequestException:
            time.sleep(5 * (attempt + 1))
    raise RouteError("Geocoding service unavailable. Please try again in a moment.")


# ---------------------------------------------------------------------------
# OSRM driving
# ---------------------------------------------------------------------------

def drive(origin: tuple[float, float], destination: tuple[float, float]) -> dict | None:
    """Return {duration_s, distance_m} for a driving leg, or None on failure."""
    url = f"{OSRM}/{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
    try:
        r = requests.get(url, params={"overview": "false"}, headers=HEADERS, timeout=10)
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if data.get("code") != "Ok":
        return None
    leg = data["routes"][0]
    return {"duration_s": leg["duration"], "distance_m": leg["distance"]}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def gtfs_time_to_seconds(t: str) -> int:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def seconds_to_hhmm(s: int) -> str:
    s = int(s)
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}min" if h else f"{m}min"


def gtfs_display_time(t: str) -> str:
    """Convert GTFS time (may be 24h+) to human-readable HH:MM."""
    parts = t.split(":")
    h = int(parts[0]) % 24
    return f"{h:02d}:{parts[1]}"


def arrival_datetime(dep_date: str, dep_time_gtfs: str, duration_s: float) -> datetime:
    """Given a trip departure date+time and drive duration, return the arrival datetime."""
    base = datetime.strptime(dep_date, "%Y%m%d")
    dep_secs = gtfs_time_to_seconds(dep_time_gtfs)
    return base + timedelta(seconds=dep_secs + duration_s)


# ---------------------------------------------------------------------------
# Port proximity
# ---------------------------------------------------------------------------

def nearest_stops(lat: float, lon: float, stops: dict[str, Stop],
                  max_km: float = 30.0) -> list[tuple[float, Stop]]:
    """Return stops within max_km, sorted by distance."""
    result = []
    for stop in stops.values():
        d = _haversine_km(lat, lon, stop.lat, stop.lon)
        if d <= max_km:
            result.append((d, stop))
    result.sort()
    return result


# ---------------------------------------------------------------------------
# Route finder
# ---------------------------------------------------------------------------

def find_routes(
    origin: tuple[float, float],
    destination: tuple[float, float],
    gtfs: dict,
    travel_date: date,
    depart_after: int = 0,        # earliest departure from origin, in seconds from midnight
    max_results: int = 3,
    max_drive_to_port_km: float = 500.0,
    max_drive_from_port_km: float = 100.0,
    osm_island: str | None = None,        # destination island (pre-computed); skips Overpass call
    origin_island: str | None = None,     # origin island (pre-computed); skips Overpass call
) -> list[Route]:

    stops = gtfs["stops"]
    departures = gtfs["departures"]
    date_str = travel_date.strftime("%Y%m%d")
    stop_island = gtfs.get("stop_island", {})

    # Normalize Unicode ligatures (e.g. OSM uses 'ǌ' for 'nj') before comparing.
    def normalize(s: str) -> str:
        return s.lower().replace("ǌ", "nj").replace("ǈ", "lj").replace("ǋ", "nj")

    def resolve_island(osm_name: str) -> str:
        """Map an OSM island name to the canonical name used in ports.json."""
        osm_norm = normalize(osm_name)
        return next(
            (isl for isl in stop_island.values() if isl and normalize(isl) == osm_norm),
            ""
        )

    def pick_ferry(dep_port_id: str, arr_port_id: str, arrive_at_port_secs: int) -> FerryTrip | None:
        """Return the first viable ferry from dep_port to arr_port given arrival time."""
        for ferry in departures.get((dep_port_id, arr_port_id), []):
            if ferry.dep_date < date_str:
                continue
            if ferry.dep_date == date_str:
                if gtfs_time_to_seconds(ferry.dep_time) < arrive_at_port_secs + 900:
                    continue
            return ferry
        return None

    def build_candidates(island_port_ids: set, island_coords: tuple,
                         mainland_coords: tuple, island_is_origin: bool) -> list[Route]:
        """
        Build route candidates for one ferry direction.
        island_coords / mainland_coords are the actual origin/destination coords.
        """
        # Drive from island location to each island port.
        # When OSRM can't route (e.g. Cres has no road to mainland so the
        # island network may be unreachable by the public OSRM server), fall
        # back to a straight-line haversine estimate at 40 km/h average.
        island_drives: dict[str, dict] = {}
        for stop_id in island_port_ids:
            port = stops[stop_id]
            result = drive(island_coords, (port.lat, port.lon))
            if result is None:
                km = _haversine_km(island_coords[0], island_coords[1], port.lat, port.lon)
                dist_m = km * 1.4 * 1000
                result = {
                    "duration_s": dist_m / 1000 / 40 * 3600,
                    "distance_m": dist_m,
                    "approximate": True,
                }
            island_drives[stop_id] = result

        candidates: list[Route] = []

        for island_port_id, island_drive in island_drives.items():
            island_port = stops[island_port_id]

            # Find mainland ports connected to this island port by ferry
            if island_is_origin:
                # island port departs, mainland port arrives
                connected_mainland = {
                    arr_id: stops[arr_id]
                    for (dep_id, arr_id) in departures
                    if dep_id == island_port_id and arr_id in stops
                }
            else:
                # mainland port departs, island port arrives
                connected_mainland = {
                    dep_id: stops[dep_id]
                    for (dep_id, arr_id) in departures
                    if arr_id == island_port_id and dep_id in stops
                }

            for mainland_port_id, mainland_port in connected_mainland.items():
                # Mainland ports must actually be on the mainland
                if stop_island.get(mainland_port_id):
                    continue

                if island_is_origin:
                    dep_port, arr_port = island_port, mainland_port
                    dep_drive = island_drive
                    arr_drive = drive((mainland_port.lat, mainland_port.lon), mainland_coords)
                    # island leg is short; mainland→destination leg may be long
                    if arr_drive is None or arr_drive["distance_m"] > max_drive_to_port_km * 1000:
                        continue
                    arrive_at_dep_port = int(depart_after + dep_drive["duration_s"])
                    chosen_ferry = pick_ferry(dep_port.stop_id, arr_port.stop_id, arrive_at_dep_port)
                else:
                    dep_port, arr_port = mainland_port, island_port
                    dep_drive = drive(mainland_coords, (mainland_port.lat, mainland_port.lon))
                    if dep_drive is None or dep_drive["distance_m"] > max_drive_to_port_km * 1000:
                        continue
                    arr_drive = island_drive
                    arrive_at_dep_port = int(depart_after + dep_drive["duration_s"])
                    chosen_ferry = pick_ferry(dep_port.stop_id, arr_port.stop_id, arrive_at_dep_port)

                if chosen_ferry is None:
                    continue

                ferry_dep_secs = gtfs_time_to_seconds(chosen_ferry.dep_time)
                ferry_arr_secs = gtfs_time_to_seconds(chosen_ferry.arr_time)
                ferry_duration = ferry_arr_secs - ferry_dep_secs
                wait = max(0, ferry_dep_secs - arrive_at_dep_port)
                total = int(dep_drive["duration_s"] + wait + ferry_duration + arr_drive["duration_s"])

                candidates.append(Route(
                    drive_to_port=dep_drive,
                    ferry=chosen_ferry,
                    drive_from_port=arr_drive,
                    dep_port=dep_port,
                    arr_port=arr_port,
                    total_seconds=total,
                ))

        return candidates

    # ------------------------------------------------------------------
    # Determine which end is on an island and route accordingly.
    # ------------------------------------------------------------------

    # Try destination island first (mainland → island, the common case).
    # Skip if origin_island is already provided — caller knows direction is island → mainland.
    if osm_island is None and origin_island is None:
        print("  Looking up destination island via OSM...", end=" ", flush=True)
        osm_island = get_island_from_osm(destination[0], destination[1])

    if osm_island:
        dest_island = resolve_island(osm_island)
        if not dest_island:
            direct = drive(origin, destination)
            if direct is not None:
                raise RouteError(
                    f"{osm_island} island is not served by Jadrolinija ferry from this direction, "
                    f"but it appears to be reachable by road "
                    f"({direct['distance_m']/1000:.0f} km, ~{seconds_to_hhmm(direct['duration_s'])})."
                )
            raise RouteError(
                f"No Jadrolinija ferry ports found for {osm_island} island. "
                "Is the destination on an island served by Jadrolinija?"
            )
        print(dest_island)
        island_cluster = {sid for sid, isl in stop_island.items() if isl == dest_island}
        dest_ports_nearby = nearest_stops(destination[0], destination[1], stops,
                                          max_km=max_drive_from_port_km)
        arr_port_ids = island_cluster & {s.stop_id for _, s in dest_ports_nearby}
        print(f"  Destination island: {dest_island}")
        print(f"  Island ports (valid arrival): {sorted(arr_port_ids)}")
        if not arr_port_ids:
            raise RouteError("No island arrival ports found near destination.")
        candidates = build_candidates(arr_port_ids, destination, origin,
                                      island_is_origin=False)
        if not candidates:
            raise RouteError("No island arrival ports reachable by road from destination.")

    else:
        # Destination is not on an island — try origin instead (island → mainland).
        if origin_island is None:
            print("  Looking up origin island via OSM...", end=" ", flush=True)
            origin_island = get_island_from_osm(origin[0], origin[1])

        if not origin_island:
            raise RouteError(
                "Neither origin nor destination appears to be on an island. Try driving directly."
            )

        orig_island = resolve_island(origin_island)
        if not orig_island:
            raise RouteError(
                f"No Jadrolinija ferry ports found for {origin_island} island. "
                "Is the origin on an island served by Jadrolinija?"
            )
        print(orig_island)
        island_cluster = {sid for sid, isl in stop_island.items() if isl == orig_island}
        orig_ports_nearby = nearest_stops(origin[0], origin[1], stops,
                                          max_km=max_drive_to_port_km)
        dep_port_ids = island_cluster & {s.stop_id for _, s in orig_ports_nearby}
        print(f"  Origin island: {orig_island}")
        print(f"  Island ports (valid departure): {sorted(dep_port_ids)}")
        if not dep_port_ids:
            raise RouteError("No island departure ports found near origin.")
        candidates = build_candidates(dep_port_ids, origin, destination,
                                      island_is_origin=True)
        if not candidates:
            raise RouteError("No routes found from origin island to destination.")

    candidates.sort(key=lambda r: r.total_seconds)
    return candidates[:max_results]


# ---------------------------------------------------------------------------
# Serialisation (for web API)
# ---------------------------------------------------------------------------

def route_to_dict(route: Route) -> dict:
    ferry = route.ferry
    ferry_dur = gtfs_time_to_seconds(ferry.arr_time) - gtfs_time_to_seconds(ferry.dep_time)
    ferry_date_display = datetime.strptime(ferry.dep_date, "%Y%m%d").strftime("%d %b")
    return {
        "total_seconds": route.total_seconds,
        "total_display": seconds_to_hhmm(route.total_seconds),
        "drive_to_port": {
            "duration_s": route.drive_to_port["duration_s"],
            "distance_m": route.drive_to_port["distance_m"],
            "duration_display": seconds_to_hhmm(route.drive_to_port["duration_s"]),
            "distance_km": round(route.drive_to_port["distance_m"] / 1000),
            "port_name": route.dep_port.name,
            "approximate": route.drive_to_port.get("approximate", False),
        },
        "ferry": {
            "dep_time": gtfs_display_time(ferry.dep_time),
            "arr_time": gtfs_display_time(ferry.arr_time),
            "duration_display": seconds_to_hhmm(ferry_dur),
            "date_display": ferry_date_display,
            "route_name": ferry.route_name,
        },
        "drive_from_port": {
            "duration_s": route.drive_from_port["duration_s"],
            "distance_m": route.drive_from_port["distance_m"],
            "duration_display": seconds_to_hhmm(route.drive_from_port["duration_s"]),
            "distance_km": round(route.drive_from_port["distance_m"] / 1000),
            "port_name": route.arr_port.name,
            "approximate": route.drive_from_port.get("approximate", False),
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_route(i: int, route: Route, origin_name: str, dest_name: str,
                travel_date: date):
    print(f"\nOption {i}  —  total ~{seconds_to_hhmm(route.total_seconds)}")
    print(f"  🚗  Drive to port     {seconds_to_hhmm(route.drive_to_port['duration_s'])}  "
          f"({route.drive_to_port['distance_m']/1000:.0f} km)  →  {route.dep_port.name}")

    ferry = route.ferry
    ferry_dur = gtfs_time_to_seconds(ferry.arr_time) - gtfs_time_to_seconds(ferry.dep_time)
    ferry_date_display = datetime.strptime(ferry.dep_date, "%Y%m%d").strftime("%d %b")
    print(f"  ⛴️   Ferry            {gtfs_display_time(ferry.dep_time)} → "
          f"{gtfs_display_time(ferry.arr_time)}  ({seconds_to_hhmm(ferry_dur)})  "
          f"[{ferry_date_display}]  {ferry.route_name}")

    print(f"  🚗  Drive to dest     {seconds_to_hhmm(route.drive_from_port['duration_s'])}  "
          f"({route.drive_from_port['distance_m']/1000:.0f} km)  "
          f"from {route.arr_port.name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Find driving + ferry routes to Adriatic islands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("origin", help="Starting location, e.g. 'Rijeka' or 'Zagreb'")
    parser.add_argument("destination", help="Destination on an island, e.g. 'Bol, Brač'")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="Travel date YYYY-MM-DD (default: today)")
    parser.add_argument("--depart-after", default="00:00",
                        help="Earliest departure time HH:MM (default: 00:00), e.g. 17:00")
    parser.add_argument("--results", type=int, default=3,
                        help="Max number of options to show (default: 3)")
    args = parser.parse_args()

    travel_date = date.fromisoformat(args.date)

    try:
        h, m = args.depart_after.split(":")
        depart_after_secs = int(h) * 3600 + int(m) * 60
    except ValueError:
        raise SystemExit(f"Invalid --depart-after time '{args.depart_after}', expected HH:MM")

    print(f"Loading GTFS feed...", end=" ", flush=True)
    gtfs = load_gtfs(GTFS_ZIP)
    print(f"OK ({len(gtfs['stops'])} stops, {len(gtfs['departures'])} route pairs)")

    try:
        print(f"Geocoding '{args.origin}'...", end=" ", flush=True)
        orig_lat, orig_lon, orig_name = geocode(args.origin)
        print(orig_name[:70])

        print(f"Geocoding '{args.destination}'...", end=" ", flush=True)
        dest_lat, dest_lon, dest_name = geocode(args.destination)
        print(dest_name[:70])
    except RouteError as e:
        sys.exit(str(e))

    depart_str = f", departing after {args.depart_after}" if depart_after_secs else ""
    print(f"\nSearching routes on {travel_date.strftime('%d %b %Y')}{depart_str}...\n")

    try:
        routes = find_routes(
            origin=(orig_lat, orig_lon),
            destination=(dest_lat, dest_lon),
            gtfs=gtfs,
            travel_date=travel_date,
            depart_after=depart_after_secs,
            max_results=args.results,
        )
    except RouteError as e:
        sys.exit(str(e))

    print("=" * 60)
    print(f"  {args.origin}  →  {args.destination}")
    print("=" * 60)

    if not routes:
        print("\nNo routes found. Try a different date or check the destination.")
        sys.exit(1)

    for i, route in enumerate(routes, 1):
        print_route(i, route, orig_name, dest_name, travel_date)

    print()


if __name__ == "__main__":
    main()
