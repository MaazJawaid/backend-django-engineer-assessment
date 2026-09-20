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


def _build_segment_grid(
    route_coords: Sequence[Sequence[float]],
    corridor_miles: float,
):
    """
    Precompute per-segment projected geometry and a uniform lat/lng grid index.

    Each segment is registered into every grid cell its bbox (padded by
    corridor_miles) overlaps, so a stop only needs to inspect the segments in
    its own cell to find every segment within corridor_miles.

    Returns (segs, grid, cell_lat_deg, cell_lng_deg, min_lat, min_lng,
    n_lat, n_lng) where segs is a list of dicts with the precomputed
    per-segment data and grid maps (i, j) -> list of segment indices.
    """
    n = len(route_coords)
    segs: list[dict] = []
    cum = 0.0
    for i in range(n - 1):
        lng1, lat1 = route_coords[i]
        lng2, lat2 = route_coords[i + 1]
        length = haversine(lat1, lng1, lat2, lng2)

        mid_lat = math.radians((lat1 + lat2) / 2.0)
        cos_mid = math.cos(mid_lat)
        ax = math.radians(lng1) * EARTH_RADIUS_MI * cos_mid
        ay = math.radians(lat1) * EARTH_RADIUS_MI
        bx = math.radians(lng2) * EARTH_RADIUS_MI * cos_mid
        by = math.radians(lat2) * EARTH_RADIUS_MI
        dx = bx - ax
        dy = by - ay
        seg_len_sq = dx * dx + dy * dy

        segs.append(
            {
                "lat1": lat1,
                "lng1": lng1,
                "lat2": lat2,
                "lng2": lng2,
                "ax": ax,
                "ay": ay,
                "dx": dx,
                "dy": dy,
                "seg_len_sq": seg_len_sq,
                "cos_mid": cos_mid,
                "length": length,
                "cum_start": cum,
            }
        )
        cum += length

    # Grid extents over the route bbox padded by corridor_miles.
    min_lat = min(c[1] for c in route_coords) - corridor_miles / 69.0
    max_lat = max(c[1] for c in route_coords) + corridor_miles / 69.0
    mean_lat = (min_lat + max_lat) / 2.0
    cos_lat = math.cos(math.radians(mean_lat))
    cell_lat_deg = max(corridor_miles / 69.0, 1e-6)
    cell_lng_deg = (
        corridor_miles / (69.0 * cos_lat) if abs(cos_lat) > 1e-6 else corridor_miles / 69.0
    )
    min_lng = min(c[0] for c in route_coords) - cell_lng_deg
    max_lng = max(c[0] for c in route_coords) + cell_lng_deg

    n_lat = max(int(math.ceil((max_lat - min_lat) / cell_lat_deg)), 1)
    n_lng = max(int(math.ceil((max_lng - min_lng) / cell_lng_deg)), 1)

    grid: dict[tuple[int, int], list[int]] = {}
    for idx, s in enumerate(segs):
        lo_lat = min(s["lat1"], s["lat2"]) - corridor_miles / 69.0
        hi_lat = max(s["lat1"], s["lat2"]) + corridor_miles / 69.0
        lo_lng = min(s["lng1"], s["lng2"]) - cell_lng_deg
        hi_lng = max(s["lng1"], s["lng2"]) + cell_lng_deg

        i0 = int((lo_lat - min_lat) / cell_lat_deg)
        i1 = int((hi_lat - min_lat) / cell_lat_deg)
        j0 = int((lo_lng - min_lng) / cell_lng_deg)
        j1 = int((hi_lng - min_lng) / cell_lng_deg)
        i0 = max(0, min(i0, n_lat - 1))
        i1 = max(0, min(i1, n_lat - 1))
        j0 = max(0, min(j0, n_lng - 1))
        j1 = max(0, min(j1, n_lng - 1))

        for ci in range(i0, i1 + 1):
            for cj in range(j0, j1 + 1):
                grid.setdefault((ci, cj), []).append(idx)

    return segs, grid, cell_lat_deg, cell_lng_deg, min_lat, min_lng, n_lat, n_lng


def match_stops_to_route(
    route_coords: Sequence[Sequence[float]],
    stops: Sequence[Any],
    corridor_miles: float,
) -> list[dict]:
    """
    Project fuel stops onto a route corridor.

    Args:
        route_coords: GeoJSON LineString coordinates [[lng, lat], ...]
        stops: iterable of dicts with "latitude" and "longitude" keys
        corridor_miles: max off-route distance to include a stop

    Returns:
        list of dicts {stop, mile_marker, distance_off_route} sorted by mile_marker

    Implementation: a uniform lat/lng grid indexes the route segments (each
    segment registered in every cell its bbox padded by corridor_miles
    overlaps), so each stop only tests the handful of segments in its own
    cell. Per-segment equirectangular projections are precomputed once, so the
    inner loop is plain arithmetic. The final distance_off_route and the
    corridor boundary test use the exact haversine, preserving the same
    results as the brute-force point-to-polyline matcher.
    """
    if len(route_coords) < 2:
        return []

    (
        segs,
        grid,
        cell_lat_deg,
        cell_lng_deg,
        min_lat,
        min_lng,
        n_lat,
        n_lng,
    ) = _build_segment_grid(route_coords, corridor_miles)

    matched: list[dict] = []
    for stop in stops:
        slat = stop["latitude"]
        slng = stop["longitude"]
        if slat is None or slng is None:
            continue

        ci = int((slat - min_lat) / cell_lat_deg)
        cj = int((slng - min_lng) / cell_lng_deg)
        if ci < 0 or ci >= n_lat or cj < 0 or cj >= n_lng:
            continue
        cell = grid.get((ci, cj))
        if not cell:
            continue

        best_dist = float("inf")
        best_idx = -1
        best_t = 0.0
        for idx in cell:
            s = segs[idx]
            # Project the stop into this segment's local equirectangular plane
            # to find the projection parameter t, then measure the true
            # great-circle distance with haversine for an exact result.
            px = math.radians(slng) * EARTH_RADIUS_MI * s["cos_mid"]
            py = math.radians(slat) * EARTH_RADIUS_MI
            if s["seg_len_sq"] < 1e-18:
                t = 0.0
            else:
                t = ((px - s["ax"]) * s["dx"] + (py - s["ay"]) * s["dy"]) / s["seg_len_sq"]
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
            proj_lat = s["lat1"] + t * (s["lat2"] - s["lat1"])
            proj_lng = s["lng1"] + t * (s["lng2"] - s["lng1"])
            dist = haversine(slat, slng, proj_lat, proj_lng)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
                best_t = t

        if best_idx >= 0 and best_dist <= corridor_miles:
            s = segs[best_idx]
            matched.append(
                {
                    "stop": stop,
                    "mile_marker": s["cum_start"] + best_t * s["length"],
                    "distance_off_route": best_dist,
                }
            )

    matched.sort(key=lambda m: m["mile_marker"])
    return matched
