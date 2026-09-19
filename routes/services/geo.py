"""Geographic helpers: haversine, bbox, point-to-segment projection, corridor matching."""

from __future__ import annotations

import math
from typing import Any, Sequence

EARTH_RADIUS_MI = 3958.7613


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two WGS84 points."""
    rlat1, rlng1, rlat2, rlng2 = map(math.radians, (lat1, lng1, lat2, lng2))
    dlat = rlat2 - rlat1
    dlng = rlng2 - rlng1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def bbox_of(
    coords: Sequence[Sequence[float]], pad_miles: float = 10.0
) -> tuple[float, float, float, float]:
    """
    Bounding box of a GeoJSON-style coordinate list [[lng, lat], ...]
    padded by pad_miles. Returns (min_lat, min_lng, max_lat, max_lng).
    """
    if not coords:
        raise ValueError("coords must be non-empty")

    lats = [c[1] for c in coords]
    lngs = [c[0] for c in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    mean_lat = (min_lat + max_lat) / 2.0
    lat_pad = pad_miles / 69.0
    cos_lat = math.cos(math.radians(mean_lat))
    lng_pad = pad_miles / (69.0 * cos_lat) if abs(cos_lat) > 1e-6 else pad_miles / 69.0

    return (
        min_lat - lat_pad,
        min_lng - lng_pad,
        max_lat + lat_pad,
        max_lng + lng_pad,
    )


def point_to_segment_distance(
    plat: float,
    plng: float,
    alat: float,
    alng: float,
    blat: float,
    blng: float,
) -> tuple[float, float, float, float]:
    """
    Distance from point P to segment AB using equirectangular projection.

    Returns (distance_miles, proj_lat, proj_lng, t) where t in [0, 1].
    """
    mid_lat = math.radians((alat + blat) / 2.0)
    cos_mid = math.cos(mid_lat)

    # Convert to local Cartesian (miles)
    ax = math.radians(alng) * EARTH_RADIUS_MI * cos_mid
    ay = math.radians(alat) * EARTH_RADIUS_MI
    bx = math.radians(blng) * EARTH_RADIUS_MI * cos_mid
    by = math.radians(blat) * EARTH_RADIUS_MI
    px = math.radians(plng) * EARTH_RADIUS_MI * cos_mid
    py = math.radians(plat) * EARTH_RADIUS_MI

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-18:
        t = 0.0
        proj_lat, proj_lng = alat, alng
        dist = haversine(plat, plng, alat, alng)
        return dist, proj_lat, proj_lng, t

    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))

    proj_lat = alat + t * (blat - alat)
    proj_lng = alng + t * (blng - alng)
    dist = haversine(plat, plng, proj_lat, proj_lng)
    return dist, proj_lat, proj_lng, t


def match_stops_to_route(
    route_coords: Sequence[Sequence[float]],
    stops: Sequence[Any],
    corridor_miles: float,
) -> list[dict]:
    """
    Project fuel stops onto a route corridor.

    Args:
        route_coords: GeoJSON LineString coordinates [[lng, lat], ...]
        stops: iterable of objects with .latitude and .longitude
        corridor_miles: max off-route distance to include a stop

    Returns:
        list of dicts {stop, mile_marker, distance_off_route} sorted by mile_marker
    """
    if len(route_coords) < 2:
        return []

    # Precompute segment lengths and cumulative mileage
    seg_lengths: list[float] = []
    cum_miles = [0.0]
    for i in range(len(route_coords) - 1):
        lng1, lat1 = route_coords[i]
        lng2, lat2 = route_coords[i + 1]
        length = haversine(lat1, lng1, lat2, lng2)
        seg_lengths.append(length)
        cum_miles.append(cum_miles[-1] + length)

    matched: list[dict] = []
    for stop in stops:
        if stop.latitude is None or stop.longitude is None:
            continue

        best_dist = float("inf")
        best_marker = 0.0
        for i in range(len(route_coords) - 1):
            lng1, lat1 = route_coords[i]
            lng2, lat2 = route_coords[i + 1]
            dist, _, _, t = point_to_segment_distance(
                stop.latitude,
                stop.longitude,
                lat1,
                lng1,
                lat2,
                lng2,
            )
            if dist < best_dist:
                best_dist = dist
                best_marker = cum_miles[i] + t * seg_lengths[i]

        if best_dist <= corridor_miles:
            matched.append(
                {
                    "stop": stop,
                    "mile_marker": best_marker,
                    "distance_off_route": best_dist,
                }
            )

    matched.sort(key=lambda m: m["mile_marker"])
    return matched
