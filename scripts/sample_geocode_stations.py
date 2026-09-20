"""One-off experiment: geocode 20 random OPIS stations via ORS Pelias.

Does not modify application models, loaders, or runtime logic.
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
import time
from pathlib import Path

# Project root on sys.path for Django
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuel_route.settings")

import django

django.setup()

from routes.services.gazetteer import load_gazetteer  # noqa: E402
from routes.services.geo import haversine  # noqa: E402
from routes.services.ors_client import ORSClient, ORSError  # noqa: E402

CSV_PATH = ROOT / "fuel-prices-for-be-assessment.csv"
OUT_PATH = ROOT / "data" / "sample_geocoded_stations.json"
SAMPLE_SIZE = 20
RANDOM_SEED = 42
RATE_LIMIT_SLEEP_S = 1.1


def read_unique_stations(path: Path) -> list[dict]:
    best: dict[int, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            try:
                opis_id = int(raw["OPIS Truckstop ID"])
            except (KeyError, ValueError, TypeError):
                continue
            city = (raw.get("City") or "").strip()
            state = (raw.get("State") or "").strip().upper()
            if not city or len(state) != 2:
                continue
            row = {
                "opis_id": opis_id,
                "name": (raw.get("Truckstop Name") or "").strip(),
                "address": (raw.get("Address") or "").strip(),
                "city": city,
                "state": state,
            }
            # Prefer first seen; duplicates share the same location fields
            best.setdefault(opis_id, row)
    return list(best.values())


def build_query(station: dict) -> str:
    parts = [
        station["name"],
        station["address"],
        station["city"],
        station["state"],
        "USA",
    ]
    return ", ".join(p for p in parts if p)


def geocode_rich(client: ORSClient, text: str) -> dict | None:
    """Call Pelias like ORSClient.geocode, but keep feature metadata."""
    url = "https://api.heigit.org/pelias/v1/search"
    params = {"text": text, "size": 1, "boundary.country": "US"}
    response = client._request("GET", url, params=params)
    if response.status_code != 200:
        raise ORSError(
            f"Geocode failed ({response.status_code}): {response.text[:300]}"
        )
    features = (response.json().get("features") or [])
    if not features:
        return None
    feature = features[0]
    coords = feature["geometry"]["coordinates"]  # [lng, lat]
    props = feature.get("properties") or {}
    return {
        "latitude": float(coords[1]),
        "longitude": float(coords[0]),
        "layer": props.get("layer"),
        "label": props.get("label"),
        "name": props.get("name"),
        "confidence": props.get("confidence"),
        "accuracy": props.get("accuracy"),
        "match_type": props.get("match_type"),
    }


def assess_precision(
    station: dict, result: dict | None
) -> tuple[str | None, float | None]:
    """Heuristic: distance from city centroid + Pelias layer."""
    if result is None:
        return None, None
    gaz = load_gazetteer()
    city_hit = gaz.get((station["city"].lower(), station["state"].upper()))
    dist_mi = None
    if city_hit:
        dist_mi = haversine(
            result["latitude"],
            result["longitude"],
            city_hit[0],
            city_hit[1],
        )
    layer = (result.get("layer") or "").lower()
    # venue/address/street tend to be point-accurate; locality/localadmin/region are city-ish
    if layer in {"locality", "localadmin", "county", "region", "macroregion", "country"}:
        precision = "likely_city_center"
    elif layer in {"venue", "address", "street"}:
        # Still city-center-ish if coords sit on the gazetteer centroid
        if dist_mi is not None and dist_mi < 0.15:
            precision = "possibly_city_center"
        else:
            precision = "likely_station_or_address"
    else:
        if dist_mi is not None and dist_mi < 0.15:
            precision = "possibly_city_center"
        else:
            precision = "uncertain"
    return precision, dist_mi


def main() -> None:
    stations = read_unique_stations(CSV_PATH)
    if len(stations) < SAMPLE_SIZE:
        raise SystemExit(f"Need at least {SAMPLE_SIZE} stations, found {len(stations)}")

    rng = random.Random(RANDOM_SEED)
    sample = rng.sample(stations, SAMPLE_SIZE)
    client = ORSClient()

    records: list[dict] = []
    meta_for_report: list[dict] = []

    for i, station in enumerate(sample, start=1):
        query = build_query(station)
        print(f"[{i}/{SAMPLE_SIZE}] {query}")
        try:
            rich = geocode_rich(client, query)
        except ORSError as exc:
            print(f"  ERROR: {exc}")
            rich = None

        precision, dist_mi = assess_precision(station, rich)
        record = {
            "opis_id": station["opis_id"],
            "name": station["name"],
            "address": station["address"],
            "city": station["city"],
            "state": station["state"],
            "latitude": rich["latitude"] if rich else None,
            "longitude": rich["longitude"] if rich else None,
            "geocoding_source": "ors" if rich else "none",
        }
        records.append(record)
        meta_for_report.append(
            {
                **record,
                "query": query,
                "pelias_layer": rich.get("layer") if rich else None,
                "pelias_label": rich.get("label") if rich else None,
                "pelias_confidence": rich.get("confidence") if rich else None,
                "miles_from_city_centroid": round(dist_mi, 3) if dist_mi is not None else None,
                "precision_heuristic": precision,
            }
        )
        if rich:
            print(
                f"  -> ({rich['latitude']:.6f}, {rich['longitude']:.6f}) "
                f"layer={rich.get('layer')} conf={rich.get('confidence')} "
                f"dist_city={dist_mi:.2f}mi" if dist_mi is not None else
                f"  -> ({rich['latitude']:.6f}, {rich['longitude']:.6f}) "
                f"layer={rich.get('layer')}"
            )
        else:
            print("  -> failed")

        if i < SAMPLE_SIZE:
            time.sleep(RATE_LIMIT_SLEEP_S)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
        f.write("\n")

    # Side report for the human summary (not required by the assignment)
    report_path = ROOT / "data" / "sample_geocoded_stations_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(meta_for_report, f, indent=2)
        f.write("\n")

    ok = sum(1 for r in records if r["latitude"] is not None)
    print(f"\nWrote {OUT_PATH} ({ok}/{SAMPLE_SIZE} geocoded)")
    print(f"Wrote diagnostic {report_path}")


if __name__ == "__main__":
    main()
