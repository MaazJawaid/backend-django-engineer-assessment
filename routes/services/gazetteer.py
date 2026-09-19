"""Offline city-level geocoding via the bundled GeoNames US cities gazetteer.

Resolves strict ``"City, ST"`` inputs to city-center coordinates without an
external API call. Specific street addresses are intentionally NOT matched
here so they fall through to ORS Pelias for precise geocoding.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from django.conf import settings

_GAZETTEER_PATH = Path(settings.BASE_DIR) / "data" / "us_cities_gazetteer.csv"

# Strict "City, ST" pattern: exactly one comma, 2-letter state, no digits in
# the city part (digits indicate a street number / address).
_STATE_RE = re.compile(r"^[A-Z]{2}$")

_gazetteer: Optional[dict[tuple[str, str], tuple[float, float]]] = None


def load_gazetteer() -> dict[tuple[str, str], tuple[float, float]]:
    """Load the bundled gazetteer once into a module-level dict.

    Keyed by ``(city.lower(), state.upper())``. Returns an empty dict if the
    file is missing so callers gracefully fall back to ORS geocoding.
    """
    global _gazetteer
    if _gazetteer is not None:
        return _gazetteer

    result: dict[tuple[str, str], tuple[float, float]] = {}
    if not _GAZETTEER_PATH.exists():
        _gazetteer = result
        return result

    with _GAZETTEER_PATH.open(newline="", encoding="utf-8") as f:
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

    _gazetteer = result
    return result


def resolve_city_state(text: str) -> Optional[tuple[float, float]]:
    """Resolve a strict ``"City, ST"`` string to ``(lat, lng)`` via the gazetteer.

    Returns ``None`` for anything that is not an unambiguous city-level query
    (multi-comma addresses, street numbers, unknown cities, non-2-letter
    states), so those inputs fall back to ORS geocoding for accuracy.
    """
    if not text:
        return None

    parts = text.strip().split(",")
    if len(parts) != 2:
        return None

    city = parts[0].strip()
    state = parts[1].strip().upper()
    if not city or not _STATE_RE.match(state):
        return None
    # A digit in the city part indicates a street number / address, not a city.
    if any(ch.isdigit() for ch in city):
        return None

    return load_gazetteer().get((city.lower(), state))
