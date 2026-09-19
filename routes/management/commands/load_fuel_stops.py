"""
Load fuel stops from the assessment CSV, join city/state to the bundled gazetteer,
and optionally geocode misses via OpenRouteService.
"""

from __future__ import annotations

import csv
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routes.models import FuelStop, GeocodeCache
from routes.services.ors_client import ORSError, geocode


class Command(BaseCommand):
    help = "Load and geocode fuel stops from the OPIS CSV"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default="",
            help="Path to fuel prices CSV (default: fuel-prices-for-be-assessment.csv)",
        )
        parser.add_argument(
            "--gazetteer",
            type=str,
            default="",
            help="Path to us_cities_gazetteer.csv",
        )
        parser.add_argument(
            "--geocode-misses",
            action="store_true",
            help="Geocode city/state misses via OpenRouteService (rate-limited)",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv"]) if options["csv"] else (
            Path(settings.BASE_DIR) / "fuel-prices-for-be-assessment.csv"
        )
        gaz_path = Path(options["gazetteer"]) if options["gazetteer"] else (
            Path(settings.BASE_DIR) / "data" / "us_cities_gazetteer.csv"
        )

        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")
        if not gaz_path.exists():
            raise CommandError(
                f"Gazetteer not found: {gaz_path}. "
                "Run: python manage.py build_gazetteer"
            )

        gazetteer = self._load_gazetteer(gaz_path)
        self.stdout.write(f"Loaded {len(gazetteer)} gazetteer entries")

        raw_rows = self._read_csv(csv_path)
        self.stdout.write(f"Read {len(raw_rows)} CSV rows")

        deduped = self._dedupe(raw_rows)
        self.stdout.write(f"Deduped to {len(deduped)} unique OPIS IDs (min price)")

        gazetteer_hits = 0
        ors_hits = 0
        skipped = 0
        objects: list[FuelStop] = []

        for row in deduped:
            key = (row["city"].lower(), row["state"])
            lat = lng = None
            source = "none"

            if key in gazetteer:
                lat, lng = gazetteer[key]
                source = "gazetteer"
                gazetteer_hits += 1
            elif options["geocode_misses"]:
                query = f"{row['city']}, {row['state']}"
                cache_key = query.lower()
                cached = GeocodeCache.objects.filter(query=cache_key).first()
                if cached:
                    lat, lng = cached.latitude, cached.longitude
                    source = "ors"
                    ors_hits += 1
                else:
                    try:
                        result = geocode(query)
                        time.sleep(0.35)  # ~3 req/sec
                        if result:
                            lat, lng = result
                            GeocodeCache.objects.update_or_create(
                                query=cache_key,
                                defaults={
                                    "latitude": lat,
                                    "longitude": lng,
                                    "source": "ors",
                                },
                            )
                            source = "ors"
                            ors_hits += 1
                        else:
                            skipped += 1
                    except ORSError as exc:
                        self.stderr.write(f"Geocode failed for {query}: {exc}")
                        skipped += 1
            else:
                skipped += 1

            objects.append(
                FuelStop(
                    opis_id=row["opis_id"],
                    name=row["name"],
                    address=row["address"],
                    city=row["city"],
                    state=row["state"],
                    rack_id=row["rack_id"],
                    retail_price=row["retail_price"],
                    latitude=lat,
                    longitude=lng,
                    geocode_source=source,
                )
            )

        with transaction.atomic():
            FuelStop.objects.all().delete()
            FuelStop.objects.bulk_create(objects, batch_size=500)

        with_coords = sum(1 for o in objects if o.latitude is not None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(objects)} fuel stops "
                f"({with_coords} with coords: "
                f"{gazetteer_hits} gazetteer, {ors_hits} ors; "
                f"{skipped} without coords)"
            )
        )

    def _load_gazetteer(self, path: Path) -> dict[tuple[str, str], tuple[float, float]]:
        result: dict[tuple[str, str], tuple[float, float]] = {}
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                city = (row.get("city") or "").strip()
                state = (row.get("state") or "").strip().upper()
                if not city or not state:
                    continue
                try:
                    lat = float(row["lat"])
                    lng = float(row["lng"])
                except (KeyError, ValueError, TypeError):
                    continue
                result[(city.lower(), state)] = (lat, lng)
        return result

    def _read_csv(self, path: Path) -> list[dict]:
        rows: list[dict] = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                try:
                    opis_id = int(raw["OPIS Truckstop ID"])
                except (KeyError, ValueError, TypeError):
                    continue
                try:
                    price = Decimal(str(raw["Retail Price"]).strip())
                except (InvalidOperation, KeyError, AttributeError):
                    continue

                city = (raw.get("City") or "").strip()
                state = (raw.get("State") or "").strip().upper()
                if not city or len(state) != 2:
                    continue

                rack_raw = (raw.get("Rack ID") or "").strip()
                rack_id = int(rack_raw) if rack_raw.isdigit() else None

                rows.append(
                    {
                        "opis_id": opis_id,
                        "name": (raw.get("Truckstop Name") or "").strip()[:255],
                        "address": (raw.get("Address") or "").strip()[:255],
                        "city": city[:100],
                        "state": state,
                        "rack_id": rack_id,
                        "retail_price": price,
                    }
                )
        return rows

    def _dedupe(self, rows: list[dict]) -> list[dict]:
        best: dict[int, dict] = {}
        for row in rows:
            oid = row["opis_id"]
            if oid not in best or row["retail_price"] < best[oid]["retail_price"]:
                # Keep first non-price fields when replacing with cheaper price
                if oid in best:
                    kept = best[oid].copy()
                    kept["retail_price"] = row["retail_price"]
                    best[oid] = kept
                else:
                    best[oid] = row
        return list(best.values())
