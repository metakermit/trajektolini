"""Tests for route planning logic."""

import math
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from route import (
    FerryTrip, Route, Stop,
    find_routes, gtfs_display_time, gtfs_time_to_seconds, load_gtfs,
    seconds_to_hhmm,
)

GTFS_ZIP = Path(__file__).parent / "fixtures" / "jadrolinija_gtfs.zip"
TEST_DATE = date(2026, 4, 10)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def gtfs():
    if not GTFS_ZIP.exists():
        pytest.skip("GTFS fixture not found")
    return load_gtfs(GTFS_ZIP)


def haversine_drive(origin, dest):
    """Approximate driving time/distance using haversine + tortuosity factor."""
    R = 6371
    dlat = math.radians(dest[0] - origin[0])
    dlon = math.radians(dest[1] - origin[1])
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(origin[0])) * math.cos(math.radians(dest[0]))
         * math.sin(dlon / 2) ** 2)
    km = R * 2 * math.asin(math.sqrt(a))
    distance_m = km * 1400  # 1.4 tortuosity factor
    duration_s = distance_m / 1000 / 80 * 3600  # 80 km/h average
    return {"duration_s": duration_s, "distance_m": distance_m}


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
# ---------------------------------------------------------------------------

class TestTimeHelpers:
    def test_gtfs_time_to_seconds(self):
        assert gtfs_time_to_seconds("00:00:00") == 0
        assert gtfs_time_to_seconds("01:00:00") == 3600
        assert gtfs_time_to_seconds("01:30:00") == 5400
        assert gtfs_time_to_seconds("25:00:00") == 90000  # GTFS allows 24h+

    def test_seconds_to_hhmm(self):
        assert seconds_to_hhmm(60) == "1min"
        assert seconds_to_hhmm(3600) == "1h 00min"
        assert seconds_to_hhmm(5400) == "1h 30min"
        assert seconds_to_hhmm(7384) == "2h 03min"

    def test_gtfs_display_time_normal(self):
        assert gtfs_display_time("08:30:00") == "08:30"
        assert gtfs_display_time("14:05:00") == "14:05"

    def test_gtfs_display_time_past_midnight(self):
        # 25:30 = 01:30 next day
        assert gtfs_display_time("25:30:00") == "01:30"


# ---------------------------------------------------------------------------
# Integration tests — route finding
# ---------------------------------------------------------------------------

class TestRouteZagrebHvar:
    """Zagreb (mainland) → Hvar (island) — tests long-distance routing."""

    ZAGREB = (45.815, 15.982)
    HVAR = (43.172, 16.441)

    def test_finds_routes(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.ZAGREB, self.HVAR, gtfs, TEST_DATE,
                                 osm_island="Hvar")
        assert len(routes) >= 1

    def test_all_routes_arrive_on_hvar(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.ZAGREB, self.HVAR, gtfs, TEST_DATE,
                                 osm_island="Hvar")
        hvar_ports = {"JEL", "STA", "SUC", "VRA"}
        for r in routes:
            assert r.arr_port.stop_id in hvar_ports, (
                f"Expected arrival at a Hvar port, got {r.arr_port.stop_id}"
            )

    def test_routes_sorted_by_total_time(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.ZAGREB, self.HVAR, gtfs, TEST_DATE,
                                 osm_island="Hvar")
        times = [r.total_seconds for r in routes]
        assert times == sorted(times)


class TestRouteSplitVis:
    """Split (mainland port city) → Vis (island) — tests short direct route."""

    SPLIT = (43.508, 16.440)
    VIS = (43.060, 16.180)

    def test_finds_split_vis_ferry(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.SPLIT, self.VIS, gtfs, TEST_DATE,
                                 osm_island="Vis")
        assert any(
            r.dep_port.stop_id == "SPL" and r.arr_port.stop_id == "VIS"
            for r in routes
        ), "Expected a Split → Vis ferry option"

    def test_depart_after_filters_earlier_ferries(self, gtfs):
        # Departing after 20:00 should not produce same-day ferries we can't catch
        depart_after = 20 * 3600
        date_str = TEST_DATE.strftime("%Y%m%d")
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.SPLIT, self.VIS, gtfs, TEST_DATE,
                                 depart_after=depart_after, osm_island="Vis")
        for r in routes:
            if r.ferry.dep_date != date_str:
                continue  # next-day ferries are always valid
            arrive_at_port = depart_after + r.drive_to_port["duration_s"]
            ferry_dep = gtfs_time_to_seconds(r.ferry.dep_time)
            assert ferry_dep >= arrive_at_port + 900, (
                "Same-day ferry departs before we can reach the port"
            )


class TestRouteMartinscicaZagreb:
    """Martinšćica (Cres island) → Zagreb (mainland) — tests island-origin routing."""

    MARTINSCICA = (44.966, 14.371)
    ZAGREB = (45.815, 15.982)

    def test_finds_routes(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.MARTINSCICA, self.ZAGREB, gtfs, TEST_DATE,
                                 origin_island="Cres")
        assert len(routes) >= 1

    def test_all_routes_depart_from_cres(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.MARTINSCICA, self.ZAGREB, gtfs, TEST_DATE,
                                 origin_island="Cres")
        cres_ports = {"CRE", "MAR", "MER", "POR"}
        for r in routes:
            assert r.dep_port.stop_id in cres_ports, (
                f"Expected departure from a Cres port, got {r.dep_port.stop_id}"
            )

    def test_arrival_port_is_on_mainland(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.MARTINSCICA, self.ZAGREB, gtfs, TEST_DATE,
                                 origin_island="Cres")
        stop_island = gtfs["stop_island"]
        for r in routes:
            assert not stop_island.get(r.arr_port.stop_id), (
                f"Arrival port {r.arr_port.stop_id} should be on mainland"
            )


class TestRouteZagrebCres:
    """Zagreb (mainland) → Cres (island) — tests routing to a ferry-only island."""

    ZAGREB = (45.815, 15.982)
    CRES = (44.960, 14.410)

    def test_finds_routes(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.ZAGREB, self.CRES, gtfs, TEST_DATE,
                                 osm_island="Cres")
        assert len(routes) >= 1

    def test_all_routes_arrive_on_cres(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.ZAGREB, self.CRES, gtfs, TEST_DATE,
                                 osm_island="Cres")
        cres_ports = {"CRE", "MAR", "MER", "POR"}
        for r in routes:
            assert r.arr_port.stop_id in cres_ports, (
                f"Expected arrival at a Cres port, got {r.arr_port.stop_id}"
            )

    def test_finds_routes_when_osrm_fails_on_island(self, gtfs):
        """Haversine fallback must kick in when OSRM can't route island-internally."""
        def drive_no_short_routes(origin, dest):
            # Simulate OSRM failure for short island-internal legs (<80 km road distance)
            result = haversine_drive(origin, dest)
            if result["distance_m"] < 80_000:
                return None
            return result

        with patch("route.drive", side_effect=drive_no_short_routes):
            routes = find_routes(self.ZAGREB, self.CRES, gtfs, TEST_DATE,
                                 osm_island="Cres")
        assert len(routes) >= 1
        # At least one leg should be marked approximate (island-internal fallback used)
        assert any(
            r.drive_from_port.get("approximate") for r in routes
        ), "Expected at least one route with an approximate island-internal leg"


class TestRouteRijekaCres:
    """Rijeka → Cres (island) — tests northern Adriatic routing."""

    RIJEKA = (45.327, 14.442)
    CRES = (44.960, 14.410)

    def test_finds_routes(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.RIJEKA, self.CRES, gtfs, TEST_DATE,
                                 osm_island="Cres")
        assert len(routes) >= 1

    def test_all_routes_arrive_on_cres(self, gtfs):
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.RIJEKA, self.CRES, gtfs, TEST_DATE,
                                 osm_island="Cres")
        cres_ports = {"CRE", "MAR", "MER", "POR"}
        for r in routes:
            assert r.arr_port.stop_id in cres_ports, (
                f"Expected arrival at a Cres port, got {r.arr_port.stop_id}"
            )

    def test_no_island_port_used_as_departure(self, gtfs):
        # Departure ports must be on the mainland, not on any island
        with patch("route.drive", side_effect=haversine_drive):
            routes = find_routes(self.RIJEKA, self.CRES, gtfs, TEST_DATE,
                                 osm_island="Cres")
        stop_island = gtfs["stop_island"]
        for r in routes:
            island = stop_island.get(r.dep_port.stop_id, "")
            assert not island, (
                f"Departure port {r.dep_port.stop_id} is on island '{island}', not mainland"
            )
