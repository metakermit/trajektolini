#!/usr/bin/env python3
"""Verify the integrity of the generated Jadrolinija GTFS zip."""

import csv
import io
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

GTFS_ZIP = Path("gtfs/jadrolinija_gtfs.zip")

REQUIRED_FILES = [
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "stop_times.txt",
    "calendar_dates.txt",
    "feed_info.txt",
]

REQUIRED_FIELDS = {
    "agency.txt":         ["agency_name", "agency_url", "agency_timezone"],
    "stops.txt":          ["stop_id", "stop_name", "stop_lat", "stop_lon"],
    "routes.txt":         ["route_id", "route_type"],
    "trips.txt":          ["route_id", "service_id", "trip_id"],
    "stop_times.txt":     ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
    "feed_info.txt":      ["feed_publisher_name", "feed_publisher_url", "feed_lang"],
}


def load_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))


def gtfs_time_to_seconds(t: str) -> int:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def check(condition: bool, message: str, errors: list, warnings: list, is_warning=False):
    if not condition:
        (warnings if is_warning else errors).append(message)


def main():
    errors: list[str] = []
    warnings: list[str] = []

    if not GTFS_ZIP.exists():
        print(f"ERROR: {GTFS_ZIP} not found.")
        sys.exit(1)

    print(f"Verifying {GTFS_ZIP}\n")

    with zipfile.ZipFile(GTFS_ZIP) as zf:
        names = set(zf.namelist())

        # ----------------------------------------------------------------
        # 1. Required files present
        # ----------------------------------------------------------------
        print("[ 1 ] Required files")
        for fname in REQUIRED_FILES:
            present = fname in names
            check(present, f"Missing required file: {fname}", errors, warnings)
            print(f"      {'OK' if present else 'MISSING':8}  {fname}")

        # ----------------------------------------------------------------
        # 2. Required fields present
        # ----------------------------------------------------------------
        print("\n[ 2 ] Required fields")
        tables: dict[str, list[dict]] = {}
        for fname in REQUIRED_FILES:
            if fname not in names:
                continue
            rows = load_csv(zf, fname)
            tables[fname] = rows
            if not rows:
                check(False, f"{fname}: file is empty", errors, warnings)
                print(f"      EMPTY    {fname}")
                continue
            actual = set(rows[0].keys())
            required = set(REQUIRED_FIELDS.get(fname, []))
            missing = required - actual
            check(not missing, f"{fname}: missing fields {missing}", errors, warnings)
            status = "OK" if not missing else f"MISSING {missing}"
            print(f"      {status[:8]:8}  {fname}  ({len(rows)} rows)")

        # ----------------------------------------------------------------
        # 3. Referential integrity
        # ----------------------------------------------------------------
        print("\n[ 3 ] Referential integrity")

        stop_ids     = {r["stop_id"] for r in tables.get("stops.txt", [])}
        route_ids    = {r["route_id"] for r in tables.get("routes.txt", [])}
        trip_ids     = {r["trip_id"] for r in tables.get("trips.txt", [])}
        service_ids  = {r["service_id"] for r in tables.get("trips.txt", [])}
        cal_svc_ids  = {r["service_id"] for r in tables.get("calendar_dates.txt", [])}

        # trips.route_id -> routes.route_id
        bad = {r["route_id"] for r in tables.get("trips.txt", []) if r["route_id"] not in route_ids}
        check(not bad, f"trips.txt: {len(bad)} unknown route_id(s): {sorted(bad)[:5]}", errors, warnings)
        print(f"      {'OK' if not bad else 'FAIL':8}  trips.route_id → routes.route_id")

        # stop_times.trip_id -> trips.trip_id
        bad = {r["trip_id"] for r in tables.get("stop_times.txt", []) if r["trip_id"] not in trip_ids}
        check(not bad, f"stop_times.txt: {len(bad)} unknown trip_id(s)", errors, warnings)
        print(f"      {'OK' if not bad else 'FAIL':8}  stop_times.trip_id → trips.trip_id")

        # stop_times.stop_id -> stops.stop_id
        bad = {r["stop_id"] for r in tables.get("stop_times.txt", []) if r["stop_id"] not in stop_ids}
        check(not bad, f"stop_times.txt: {len(bad)} unknown stop_id(s): {sorted(bad)[:5]}", errors, warnings)
        print(f"      {'OK' if not bad else 'FAIL':8}  stop_times.stop_id → stops.stop_id")

        # trips.service_id -> calendar_dates.service_id
        missing_svc = service_ids - cal_svc_ids
        check(not missing_svc, f"trips.txt: {len(missing_svc)} service_id(s) not in calendar_dates", errors, warnings)
        print(f"      {'OK' if not missing_svc else 'FAIL':8}  trips.service_id → calendar_dates.service_id")

        # ----------------------------------------------------------------
        # 4. Stop coordinates in valid range
        # ----------------------------------------------------------------
        print("\n[ 4 ] Stop coordinates")
        bad_coords = []
        for row in tables.get("stops.txt", []):
            try:
                lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    bad_coords.append(row["stop_id"])
            except ValueError:
                bad_coords.append(row["stop_id"])
        check(not bad_coords, f"stops.txt: invalid coordinates for {bad_coords}", errors, warnings)
        print(f"      {'OK' if not bad_coords else 'FAIL':8}  all stop coordinates in valid range")

        # ----------------------------------------------------------------
        # 5. Stop times: arrival <= departure, sequence order, overnight
        # ----------------------------------------------------------------
        print("\n[ 5 ] Stop times")

        by_trip: dict[str, list[dict]] = defaultdict(list)
        for row in tables.get("stop_times.txt", []):
            by_trip[row["trip_id"]].append(row)

        arr_after_dep = 0
        seq_issues = 0
        overnight_count = 0

        for trip_id, stops in by_trip.items():
            stops_sorted = sorted(stops, key=lambda r: int(r["stop_sequence"]))

            prev_dep_sec = None
            for stop in stops_sorted:
                try:
                    arr_sec = gtfs_time_to_seconds(stop["arrival_time"])
                    dep_sec = gtfs_time_to_seconds(stop["departure_time"])
                except ValueError:
                    seq_issues += 1
                    continue

                if arr_sec > dep_sec:
                    arr_after_dep += 1
                if dep_sec >= 24 * 3600 or arr_sec >= 24 * 3600:
                    overnight_count += 1
                if prev_dep_sec is not None and arr_sec < prev_dep_sec:
                    seq_issues += 1
                prev_dep_sec = dep_sec

        check(arr_after_dep == 0,
              f"stop_times.txt: {arr_after_dep} stops where arrival_time > departure_time",
              errors, warnings)
        check(seq_issues == 0,
              f"stop_times.txt: {seq_issues} stop sequence ordering issues",
              errors, warnings, is_warning=True)
        print(f"      {'OK' if arr_after_dep == 0 else 'FAIL':8}  arrival_time <= departure_time")
        print(f"      {'OK' if seq_issues == 0 else 'WARN':8}  stop sequence ordering")
        print(f"      {'INFO':8}  {overnight_count} stop times with 24h+ times (overnight trips)")

        # ----------------------------------------------------------------
        # 6. Each trip has at least 2 stops
        # ----------------------------------------------------------------
        print("\n[ 6 ] Trip stop counts")
        short_trips = [tid for tid, stops in by_trip.items() if len(stops) < 2]
        check(not short_trips,
              f"trips.txt: {len(short_trips)} trip(s) with fewer than 2 stops",
              errors, warnings)
        trip_stop_counts = [len(s) for s in by_trip.values()]
        max_stops = max(trip_stop_counts) if trip_stop_counts else 0
        avg_stops = sum(trip_stop_counts) / len(trip_stop_counts) if trip_stop_counts else 0
        print(f"      {'OK' if not short_trips else 'FAIL':8}  all trips have >= 2 stops")
        print(f"      {'INFO':8}  stop counts — avg: {avg_stops:.1f}, max: {max_stops}")

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    print("\n" + "=" * 50)
    if errors:
        print(f"FAILED — {len(errors)} error(s), {len(warnings)} warning(s)")
        for e in errors:
            print(f"  ERROR:   {e}")
        for w in warnings:
            print(f"  WARNING: {w}")
        sys.exit(1)
    elif warnings:
        print(f"PASSED with {len(warnings)} warning(s)")
        for w in warnings:
            print(f"  WARNING: {w}")
    else:
        print("PASSED — no errors or warnings")


if __name__ == "__main__":
    main()
