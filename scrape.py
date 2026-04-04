#!/usr/bin/env python3
"""Scrape today's Jadrolinija ferry connections and print them to the console."""

import uuid
from datetime import date

import requests

BASE_URL = "https://www2.jadrolinija.hr/voyager2/api/"
TODAY = date.today().isoformat()

HEADERS = {
    "Content-Type": "application/json",
    "CultureCode": "hr-hr",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www2.jadrolinija.hr",
    "Referer": "https://www2.jadrolinija.hr/Voyager2Web/",
}


def get_token(session: requests.Session) -> str:
    payload = {
        "UserName": "intS",
        "DeviceSerialNumber": "InternetProdaja",
        "SessionID": str(uuid.uuid4()),
        "ClientAppName": "Voyager2 Web",
        "ClientAppVersion": "1.0.0.0",
    }
    r = session.post(BASE_URL + "Auth/Token", json=payload)
    r.raise_for_status()
    return r.json()["AccessToken"]


def get_departure_points(session: requests.Session) -> list:
    payload = {"DepartureDate": TODAY, "RelativeTo": None}
    r = session.post(BASE_URL + "Routes/DepartureRoutePoints", json=payload)
    r.raise_for_status()
    return r.json()


def get_destination_points(session: requests.Session, departure_point: dict) -> list:
    payload = {"DepartureDate": TODAY, "RelativeTo": departure_point}
    r = session.post(BASE_URL + "Routes/DestinationRoutePoints", json=payload)
    r.raise_for_status()
    return r.json()


def search_voyages(
    session: requests.Session, departure: dict, destination: dict, max_results=5) -> list:
    payload = {
        "DeparturePoint": departure,
        "DestinationPoint": destination,
        "DepartureDate": TODAY,
        "MaxResultCount": max_results,
    }
    r = session.post(BASE_URL + "Routes/SearchVoyages", json=payload)
    r.raise_for_status()
    return r.json()


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Jadrolinija ferry connections for {TODAY}\n")

    print("Authenticating...", end=" ", flush=True)
    token = get_token(session)
    session.headers["Authorization"] = f"Bearer {token}"
    print("OK")

    print("Fetching departure ports...", end=" ", flush=True)
    departures = get_departure_points(session)
    print(f"{len(departures)} found")

    total_voyages = 0

    for dep in departures:
        dep_name = dep.get("DisplayName") or dep.get("PortName") or dep.get("Code", "?")

        destinations = get_destination_points(session, dep)
        if not destinations:
            continue

        printed_header = False

        for dest in destinations:
            dest_name = (
                dest.get("DisplayName") or dest.get("PortName") or dest.get("Code", "?")
            )

            voyages = search_voyages(session, dep, dest)
            if not voyages:
                continue

            if not printed_header:
                print(f"\n{'='*60}")
                print(f"From: {dep_name}")
                print(f"{'='*60}")
                printed_header = True

            print(f"\n  --> {dest_name}")
            for v in voyages:
                dep_time = v.get("DepartureTime", "?")
                arr_time = v.get("ArrivalTime", "?")
                ship = v.get("ShipName") or (
                    v.get("Line", {}) or {}
                ).get("ShipType", {}).get("ShipTypeName", "")
                line = (v.get("Line") or {}).get("LineName") or (
                    v.get("Line") or {}
                ).get("LineCode", "")
                info_parts = [f"    {dep_time} → {arr_time}"]
                if ship:
                    info_parts.append(f"Ship: {ship}")
                if line:
                    info_parts.append(f"Line: {line}")
                print("  " + "  |  ".join(info_parts))
                total_voyages += 1

    print(f"\n{'='*60}")
    print(f"Total voyages found: {total_voyages}")


if __name__ == "__main__":
    main()
