#!/usr/bin/env python3
"""Seed missing Google Maps grid URLs from data/target_locations.csv into Supabase.

Existing URLs are never upserted or reset. On full success, Pending locations are
marked Seeded in the CSV; the workflow commits that state to prevent repeat geocoding.
"""
import csv
import math
import os
import sys
import time
import urllib.parse
from pathlib import Path

import osmnx as ox
from shapely.geometry import Point
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://conomfadgsdjgewrwquq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "target_locations.csv"
KEYWORDS = [
    "Appliance Repair service",
    "Refrigerator Repair Service",
    "Washer & Dryer Repair Service",
    "Dishwasher Repair Service",
]
BATCH_SIZE = 100


def read_csv():
    with DATA_FILE.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_locations(rows, max_locations: int):
    pending = [
        row for row in rows
        if (row.get("Status") or "").strip().casefold() == "pending"
        and row.get("location_name")
    ]
    return pending[:max_locations] if max_locations > 0 else pending


def write_csv(rows):
    if not rows:
        return
    with DATA_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def existing_urls(sb):
    urls = set()
    offset = 0
    page_size = 1000
    while True:
        response = (
            sb.table("grid_queue")
            .select("url")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        urls.update(row["url"] for row in page if row.get("url"))
        if len(page) < page_size:
            break
        offset += page_size
    return urls


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY GitHub secret.")
    max_locations = int(os.environ.get("MAX_LOCATIONS", "60"))
    dry_run = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    ox.settings.use_cache = True
    ox.settings.log_console = False

    source_rows = read_csv()
    locations = load_locations(source_rows, max_locations)
    if not locations:
        print("No Pending locations found in data/target_locations.csv.")
        return

    selected_names = {row["location_name"] for row in locations}
    print(f"Pending locations selected: {len(locations)}")
    existing = existing_urls(sb)
    print(f"Existing grid_queue URLs loaded: {len(existing)}")

    pending_inserts = []
    global_grid_index = 0
    geocode_failures = []

    for index, row in enumerate(locations, start=1):
        state = (row.get("State") or "").strip().upper()
        location_name = (row.get("location_name") or "").strip()
        target_level = (row.get("target_level") or "").strip()
        step_meters = int(float(row["step_meters"]))
        zoom = (row.get("zoom") or "").strip()
        print(f"[{index}/{len(locations)}] Geocoding {location_name} | step={step_meters}m | zoom={zoom}")

        try:
            gdf = ox.geocode_to_gdf(location_name)
            polygon = gdf.geometry.iloc[0]
            min_lng, min_lat, max_lng, max_lat = polygon.bounds
        except Exception as exc:
            geocode_failures.append((location_name, str(exc)))
            print(f"  ERROR: boundary lookup failed: {exc}")
            time.sleep(1.1)
            continue

        lat_step = step_meters / 111000.0
        curr_lat = min_lat
        location_generated = 0
        location_new = 0

        while curr_lat <= max_lat:
            cos_lat = math.cos(math.radians(curr_lat))
            if abs(cos_lat) < 1e-9:
                curr_lat += lat_step
                continue
            lng_step = step_meters / (111000.0 * cos_lat)
            curr_lng = min_lng

            while curr_lng <= max_lng:
                point = Point(curr_lng, curr_lat)
                if polygon.contains(point):
                    keyword = KEYWORDS[global_grid_index % len(KEYWORDS)]
                    global_grid_index += 1
                    encoded_kw = urllib.parse.quote(keyword, safe="")
                    url = (
                        f"https://www.google.com/maps/search/{encoded_kw}/"
                        f"@{curr_lat:.6f},{curr_lng:.6f},{zoom}?hl=en&gl=us"
                    )
                    location_generated += 1
                    if url not in existing:
                        pending_inserts.append({
                            "state": state,
                            "location_name": location_name,
                            "target_level": target_level,
                            "keyword": keyword,
                            "url": url,
                            "step_meters": step_meters,
                            "zoom": zoom,
                            "latitude": round(curr_lat, 6),
                            "longitude": round(curr_lng, 6),
                            "status": "pending",
                        })
                        existing.add(url)
                        location_new += 1
                curr_lng += lng_step
            curr_lat += lat_step

        print(f"  Grid points: {location_generated}; new URLs queued: {location_new}")
        time.sleep(1.1)

    print(f"\nTotal new URLs: {len(pending_inserts)}")
    if dry_run:
        print("DRY_RUN=true: no Supabase rows were written or locations marked Seeded.")
        return

    for offset in range(0, len(pending_inserts), BATCH_SIZE):
        chunk = pending_inserts[offset:offset + BATCH_SIZE]
        sb.table("grid_queue").insert(chunk).execute()
        print(f"Inserted {min(offset + len(chunk), len(pending_inserts))}/{len(pending_inserts)}")

    if geocode_failures:
        print(f"Boundary lookup failures: {len(geocode_failures)}")
        for name, error in geocode_failures:
            print(f"  - {name}: {error}")
        # Leave statuses as Pending; retrying is safe because existing URLs are skipped.
        sys.exit(2)

    for row in source_rows:
        if row.get("location_name") in selected_names and (row.get("Status") or "").strip().casefold() == "pending":
            row["Status"] = "Seeded"
    write_csv(source_rows)
    print(f"Seeding complete. Added {len(pending_inserts)} new URLs; existing rows were left unchanged.")
    print("Marked processed locations Seeded so the scheduled action will not repeat this work.")


if __name__ == "__main__":
    main()
