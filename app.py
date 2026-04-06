#!/usr/bin/env python3
"""FastAPI web interface for the Jadrolinija route planner."""

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from route import (
    GTFS_ZIP, RouteError,
    find_routes, geocode, get_island_from_osm, load_gtfs, route_to_dict,
)

_gtfs: dict | None = None

R2_KEY = "jadrolinija_gtfs.zip"


def _download_from_r2() -> Path:
    account_id = os.environ["R2_ACCOUNT_ID"]
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ["R2_BUCKET"]
    tmp = Path(tempfile.mktemp(suffix=".zip"))
    s3.download_file(bucket, R2_KEY, str(tmp))
    return tmp


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gtfs
    tmp_path = None
    if os.environ.get("R2_ACCOUNT_ID"):
        print("Downloading GTFS feed from R2...", flush=True)
        try:
            tmp_path = await asyncio.to_thread(_download_from_r2)
            zip_path = tmp_path
        except (BotoCoreError, ClientError) as e:
            raise RuntimeError(f"Failed to download GTFS from R2: {e}") from e
    else:
        zip_path = GTFS_ZIP

    print(f"Loading GTFS feed from {zip_path}...", flush=True)
    _gtfs = await asyncio.to_thread(load_gtfs, zip_path)
    print(f"GTFS loaded: {len(_gtfs['stops'])} stops, {len(_gtfs['departures'])} route pairs")

    yield

    if tmp_path and tmp_path.exists():
        tmp_path.unlink()


app = FastAPI(title="Trajektolini", lifespan=lifespan)


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
