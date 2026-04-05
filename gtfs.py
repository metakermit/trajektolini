#!/usr/bin/env python3
"""Generate a GTFS Schedule feed from Jadrolinija ferry data."""

import json
import re
import time
from datetime import date
from pathlib import Path

import requests

from scrape import get_session, get_departure_points

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "jadrolinija-gtfs/0.1 (github.com/jadrolinija)"

# How many days ahead to scrape for voyages
SCRAPE_DAYS = 90

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
    """
    Given a list of port dicts (with Code, Desc, CountryCode),
    return a mapping of port code -> (lat, lon).
    Ports that can't be geocoded are omitted with a warning.
    """
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
# Main
# ---------------------------------------------------------------------------

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()

    print("=== Step 1: Discover ports ===")
    print("Authenticating...", end=" ", flush=True)
    session = get_session()
    print("OK")

    print("Fetching all departure points...")
    ports = get_departure_points(session, today)
    print(f"Found {len(ports)} departure points.")

    print("\nResolving port coordinates via OpenStreetMap Nominatim...")
    coords = resolve_port_coordinates(ports)
    print(f"\nResolved {len(coords)}/{len(ports)} ports.")

    # Save intermediate result for review / manual correction
    port_data = []
    for p in ports:
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

    out_path = OUTPUT_DIR / "ports.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(port_data, f, indent=2, ensure_ascii=False)
    print(f"\nPort data saved to {out_path}")
    print("Review it (especially ports with null lat/lon) before proceeding.")


if __name__ == "__main__":
    main()
