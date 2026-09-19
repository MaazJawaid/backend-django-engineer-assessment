"""
Cost-optimal fuel-stop planner for a fixed-range vehicle.

Classic greedy algorithm (provably optimal for known prices, single tank):
  At the current station, consider all stations within a FULL tank range.
  - If any cheaper station is within that range, buy just enough to reach the
    nearest cheaper one.
  - Otherwise fill the tank completely and drive to the farthest station
    within range (or finish if the destination is reachable).
"""

from __future__ import annotations

from typing import Optional


def _dedupe_by_marker(stops: list[dict]) -> list[dict]:
    """Keep the cheapest stop when multiple share (nearly) the same mile_marker."""
    by_marker: dict[float, dict] = {}
    for s in stops:
        key = round(float(s["mile_marker"]), 4)
        price = float(s["retail_price"])
        if key not in by_marker or price < float(by_marker[key]["retail_price"]):
            by_marker[key] = s
    return sorted(by_marker.values(), key=lambda x: float(x["mile_marker"]))


def _infeasible(reason: str) -> dict:
    return {
        "fuel_stops": [],
        "total_cost": 0.0,
        "total_gallons": 0.0,
        "feasible": False,
        "reason": reason,
    }


def optimize(
    stops: list[dict],
    total_distance_miles: float,
    range_miles: float = 500.0,
    mpg: float = 10.0,
    start_full_tank: bool = True,
) -> dict:
    """
    Compute optimal fuel purchases along a route.

    Returns:
        {fuel_stops, total_cost, total_gallons, feasible, reason}
    """
    if total_distance_miles < 0:
        return _infeasible("total_distance_miles must be non-negative")

    stations = [
        s for s in stops if 0 < float(s["mile_marker"]) < total_distance_miles
    ]
    stations = _dedupe_by_marker(stations)

    positions = (
        [0.0] + [float(s["mile_marker"]) for s in stations] + [total_distance_miles]
    )
    for i in range(len(positions) - 1):
        gap = positions[i + 1] - positions[i]
        if gap > range_miles:
            return _infeasible(
                f"gap of {gap:.1f} mi at mile {positions[i]:.1f} exceeds "
                f"{range_miles:.0f} mi range"
            )

    if start_full_tank and total_distance_miles <= range_miles:
        return {
            "fuel_stops": [],
            "total_cost": 0.0,
            "total_gallons": 0.0,
            "feasible": True,
            "reason": None,
        }

    if not start_full_tank:
        if not stations or float(stations[0]["mile_marker"]) > 1e-6:
            return _infeasible(
                "start_full_tank=false requires a fuel station at the start location"
            )

    fuel = range_miles if start_full_tank else 0.0  # remaining range (miles)
    pos = 0.0
    current_price = float("inf")
    current_station: Optional[dict] = None
    purchases: list[dict] = []

    def record(station: Optional[dict], gallons: float) -> None:
        if station is None or gallons <= 1e-9:
            return
        price = float(station["retail_price"])
        cost = gallons * price
        if purchases and purchases[-1].get("opis_id") == station.get("opis_id"):
            purchases[-1]["gallons"] += gallons
            purchases[-1]["cost"] += cost
        else:
            purchases.append(
                {
                    "opis_id": station.get("opis_id"),
                    "name": station.get("name"),
                    "city": station.get("city"),
                    "state": station.get("state"),
                    "latitude": station.get("latitude"),
                    "longitude": station.get("longitude"),
                    "mile_marker": float(station["mile_marker"]),
                    "retail_price": price,
                    "gallons": gallons,
                    "cost": cost,
                }
            )

    def stations_within(from_pos: float, reach: float) -> list[dict]:
        return [
            s
            for s in stations
            if from_pos < float(s["mile_marker"]) <= from_pos + reach + 1e-9
        ]

    max_iters = len(stations) * 4 + 20
    for _ in range(max_iters):
        # Can finish with remaining fuel?
        if pos + fuel >= total_distance_miles - 1e-9:
            break

        # At start we cannot buy — only use remaining fuel for reach.
        # At a station we CAN fill, so look ahead using full tank range.
        can_buy = current_station is not None
        look_ahead = range_miles if can_buy else fuel

        # Destination reachable if we fill (or with remaining fuel at start)?
        if can_buy and pos + range_miles >= total_distance_miles - 1e-9:
            need_miles = total_distance_miles - pos
            buy_gal = max(0.0, (need_miles - fuel) / mpg)
            record(current_station, buy_gal)
            break

        candidates = stations_within(pos, look_ahead)
        if not candidates:
            return _infeasible(
                f"no reachable fuel station from mile {pos:.1f} "
                f"within range {look_ahead:.1f} mi"
            )

        # Nearest cheaper station within full-tank look-ahead
        cheaper = None
        for s in candidates:
            if float(s["retail_price"]) < current_price:
                cheaper = s
                break

        if cheaper is not None:
            need_miles = float(cheaper["mile_marker"]) - pos
            buy_gal = max(0.0, (need_miles - fuel) / mpg)
            record(current_station, buy_gal)
            fuel = max(0.0, fuel + buy_gal * mpg - need_miles)
            pos = float(cheaper["mile_marker"])
            current_station = cheaper
            current_price = float(cheaper["retail_price"])
            continue

        # No cheaper station within range: fill completely (if possible), then
        # drive to the farthest reachable station.
        if can_buy:
            buy_gal = (range_miles - fuel) / mpg
            record(current_station, buy_gal)
            fuel = range_miles
            candidates = stations_within(pos, fuel)
            if not candidates:
                if pos + fuel >= total_distance_miles - 1e-9:
                    break
                return _infeasible(
                    f"cannot advance from mile {pos:.1f} after filling tank"
                )
            nxt = candidates[-1]  # farthest
        else:
            # Start: drive to nearest station on existing fuel
            nxt = candidates[0]

        drive = float(nxt["mile_marker"]) - pos
        fuel -= drive
        pos = float(nxt["mile_marker"])
        current_station = nxt
        current_price = float(nxt["retail_price"])
    else:
        return _infeasible("optimizer exceeded iteration limit")

    total_gallons = sum(p["gallons"] for p in purchases)
    total_cost = sum(p["cost"] for p in purchases)

    for p in purchases:
        p["gallons"] = round(p["gallons"], 4)
        p["cost"] = round(p["cost"], 4)
        p["retail_price"] = round(p["retail_price"], 4)
        p["mile_marker"] = round(p["mile_marker"], 2)

    return {
        "fuel_stops": purchases,
        "total_cost": round(total_cost, 4),
        "total_gallons": round(total_gallons, 4),
        "feasible": True,
        "reason": None,
    }
