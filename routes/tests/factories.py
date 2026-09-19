"""Shared test helpers and fake payloads for the routes test suite."""

from __future__ import annotations

from routes.services.ors_client import METERS_PER_MILE


def make_stop(marker, price, opis_id=1, **extra):
    """Build a fuel-stop dict suitable for fuel_optimizer.optimize()."""
    base = {
        "opis_id": opis_id,
        "name": f"Stop {opis_id}",
        "city": "Test",
        "state": "TX",
        "latitude": 0.0,
        "longitude": 0.0,
        "mile_marker": marker,
        "retail_price": price,
    }
    base.update(extra)
    return base


class FakeStop:
    """Lightweight stand-in for FuelStop with latitude/longitude attributes."""

    def __init__(self, lat, lng, name="S"):
        self.latitude = lat
        self.longitude = lng
        self.name = name


def fake_geometry():
    """Rough Chicago -> Indianapolis polyline [[lng, lat], ...]."""
    return [
        [-87.6298, 41.8781],
        [-87.0, 41.2],
        [-86.8753, 40.4167],
        [-86.4692, 40.0484],
        [-86.1581, 39.7684],
    ]


def ors_geocode_response(lat: float, lng: float) -> dict:
    """Pelias-shaped geocode JSON: coordinates are [lng, lat]."""
    return {
        "features": [
            {
                "geometry": {"coordinates": [lng, lat]},
                "properties": {"label": "Test Place"},
            }
        ]
    }


def ors_directions_response(
    coords: list[list[float]] | None = None,
    distance_miles: float = 100.0,
    *,
    use_segments: bool = False,
) -> dict:
    """
    ORS GeoJSON directions response.

    Args:
        coords: LineString coordinates [[lng, lat], ...]. Defaults to fake_geometry().
        distance_miles: Route length in miles (converted to meters for the payload).
        use_segments: If True, omit summary.distance and put distance in segments.
    """
    if coords is None:
        coords = fake_geometry()
    distance_m = distance_miles * METERS_PER_MILE
    if use_segments:
        properties = {
            "segments": [{"distance": distance_m}],
        }
    else:
        properties = {
            "summary": {"distance": distance_m},
        }
    return {
        "features": [
            {
                "geometry": {"coordinates": coords, "type": "LineString"},
                "properties": properties,
            }
        ]
    }
