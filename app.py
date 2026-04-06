#!/usr/bin/env python3
"""FastAPI web interface for the Jadrolinija route planner."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from route import (
    GTFS_ZIP, RouteError,
    find_routes, geocode, get_island_from_osm, load_gtfs, route_to_dict,
)

_gtfs: dict | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gtfs
    print(f"Loading GTFS feed from {GTFS_ZIP}...", flush=True)
    _gtfs = await asyncio.to_thread(load_gtfs, GTFS_ZIP)
    print(f"GTFS loaded: {len(_gtfs['stops'])} stops, {len(_gtfs['departures'])} route pairs")
    yield


app = FastAPI(title="Jadrolinija Route Planner", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/search")
async def search(
    origin: str = Query(...),
    destination: str = Query(...),
    travel_date: str = Query(default=None, alias="date"),
    depart_after: str = Query(default="00:00"),
    results: int = Query(default=3, ge=1, le=10),
):
    async def generate():
        def event(data: dict) -> str:
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            parsed_date = date.fromisoformat(travel_date) if travel_date else date.today()
        except ValueError:
            yield event({"type": "error", "message": f"Invalid date '{travel_date}', expected YYYY-MM-DD"})
            yield event({"type": "done"})
            return

        try:
            h, m = depart_after.split(":")
            depart_after_secs = int(h) * 3600 + int(m) * 60
        except (ValueError, AttributeError):
            yield event({"type": "error", "message": f"Invalid depart-after '{depart_after}', expected HH:MM"})
            yield event({"type": "done"})
            return

        try:
            yield event({"type": "progress", "message": f"Geocoding \"{origin}\"…"})
            orig_lat, orig_lon, orig_name = await asyncio.to_thread(geocode, origin)

            yield event({"type": "progress", "message": f"Geocoding \"{destination}\"…"})
            dest_lat, dest_lon, dest_name = await asyncio.to_thread(geocode, destination)

            yield event({"type": "progress", "message": "Identifying destination island…"})
            osm_island = await asyncio.to_thread(get_island_from_osm, dest_lat, dest_lon)

            yield event({"type": "progress", "message": "Calculating routes…"})
            routes = await asyncio.to_thread(
                find_routes,
                (orig_lat, orig_lon),
                (dest_lat, dest_lon),
                _gtfs,
                parsed_date,
                depart_after_secs,
                results,
                500.0,
                100.0,
                osm_island,
            )

            yield event({
                "type": "result",
                "origin": origin,
                "destination": destination,
                "routes": [route_to_dict(r) for r in routes],
            })

        except RouteError as e:
            yield event({"type": "error", "message": str(e)})

        yield event({"type": "done"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
