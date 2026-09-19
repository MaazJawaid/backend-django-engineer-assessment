"""
Build data/us_cities_gazetteer.csv from GeoNames cities1000 (CC0 / public domain).

Downloads https://download.geonames.org/export/dump/cities1000.zip,
filters to US, maps admin1 codes to 2-letter state abbreviations,
dedupes by (city, state) keeping the highest-population row.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

from django.conf import settings
from django.core.management.base import BaseCommand

GEONAMES_URL = "https://download.geonames.org/export/dump/cities1000.zip"

# GeoNames US admin1 codes are already 2-letter state codes for the US.
# See https://download.geonames.org/export/dump/admin1CodesASCII.txt


class Command(BaseCommand):
    help = "Download GeoNames cities1000 and build data/us_cities_gazetteer.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default="",
            help="Output CSV path (default: data/us_cities_gazetteer.csv)",
        )

    def handle(self, *args, **options):
        out_path = Path(options["output"]) if options["output"] else (
            Path(settings.BASE_DIR) / "data" / "us_cities_gazetteer.csv"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        zip_path = out_path.parent / "cities1000.zip"
        self.stdout.write(f"Downloading {GEONAMES_URL} ...")
        urlretrieve(GEONAMES_URL, zip_path)

        # GeoNames tab-separated columns (0-indexed):
        # 0 geonameid, 1 name, 2 asciiname, 3 alternatenames, 4 latitude, 5 longitude,
        # 6 feature class, 7 feature code, 8 country code, 9 cc2, 10 admin1, 11 admin2,
        # 12 admin3, 13 admin4, 14 population, 15 elevation, 16 dem, 17 timezone, 18 modification
        best: dict[tuple[str, str], tuple[str, str, float, float, int]] = {}

        with zipfile.ZipFile(zip_path) as zf:
            name = [n for n in zf.namelist() if n.endswith(".txt")][0]
            with zf.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8")
                for line in text:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 15:
                        continue
                    if parts[8] != "US":
                        continue
                    city = parts[1].strip()
                    state = parts[10].strip().upper()
                    if not city or len(state) != 2:
                        continue
                    try:
                        lat = float(parts[4])
                        lng = float(parts[5])
                        pop = int(parts[14] or 0)
                    except ValueError:
                        continue
                    key = (city.lower(), state)
                    if key not in best or pop > best[key][4]:
                        best[key] = (city, state, lat, lng, pop)

        rows = sorted(best.values(), key=lambda r: (r[1], r[0].lower()))
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["city", "state", "lat", "lng"])
            for city, state, lat, lng, _pop in rows:
                writer.writerow([city, state, f"{lat:.6f}", f"{lng:.6f}"])

        zip_path.unlink(missing_ok=True)
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(rows)} cities to {out_path}")
        )
