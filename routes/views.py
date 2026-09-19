"""API views for health, route optimization, and map rendering."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FuelStop, GeocodeCache, RouteCache
from .serializers import OptimizeRouteSerializer
from .services import fuel_optimizer, geo, gazetteer
from .services.ors_client import ORSError, directions, geocode

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


def _resolve_location(loc: dict) -> tuple[float, float, str]:
    """
    Resolve a validated location to (lat, lng, text_label).
    Order: coords -> bundled gazetteer (city-level) -> GeocodeCache -> ORS.
    """
    if loc["type"] == "coords":
        return loc["lat"], loc["lng"], loc["value"]

    # City-level inputs ("City, ST") resolve locally via the bundled gazetteer,
    # avoiding an external geocoding call. Specific addresses fall through.
    hit = gazetteer.resolve_city_state(loc["value"])
    if hit is not None:
        return hit[0], hit[1], loc["value"]

    query = loc["value"].strip().lower()
    cached = GeocodeCache.objects.filter(query=query).first()
    if cached:
        return cached.latitude, cached.longitude, loc["value"]

    result = geocode(loc["value"])
    if result is None:
        raise ValueError(f"Could not geocode location: {loc['value']}")

    lat, lng = result
    GeocodeCache.objects.update_or_create(
        query=query,
        defaults={"latitude": lat, "longitude": lng, "source": "ors"},
    )
    return lat, lng, loc["value"]


def _get_or_create_route(
    start_lat: float,
    start_lng: float,
    finish_lat: float,
    finish_lng: float,
    start_text: str,
    finish_text: str,
) -> RouteCache:
    # Round coords slightly for cache key stability
    slat, slng = round(start_lat, 5), round(start_lng, 5)
    flat, flng = round(finish_lat, 5), round(finish_lng, 5)

    cached = RouteCache.objects.filter(
        start_lat=slat,
        start_lng=slng,
        finish_lat=flat,
        finish_lng=flng,
    ).first()
    if cached:
        return cached

    result = directions([(start_lat, start_lng), (finish_lat, finish_lng)])
    return RouteCache.objects.create(
        start_lat=slat,
        start_lng=slng,
        finish_lat=flat,
        finish_lng=flng,
        geometry=result["geometry"],
        distance_miles=result["distance_miles"],
        start_text=start_text[:255],
        finish_text=finish_text[:255],
    )


def _stop_to_dict(matched: dict) -> dict:
    stop = matched["stop"]
    return {
        "opis_id": stop.opis_id,
        "name": stop.name,
        "city": stop.city,
        "state": stop.state,
        "latitude": stop.latitude,
        "longitude": stop.longitude,
        "mile_marker": matched["mile_marker"],
        "retail_price": float(stop.retail_price),
        "address": stop.address,
    }


class OptimizeRouteView(APIView):
    """POST /api/routes/optimize/ — plan a fuel-optimal route."""

    def post(self, request):
        serializer = OptimizeRouteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "bad_request", "detail": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        start_full_tank = data.get("start_full_tank", True)
        corridor_miles = data.get(
            "corridor_miles", getattr(settings, "DEFAULT_CORRIDOR_MILES", 5.0)
        )
        range_miles = getattr(settings, "VEHICLE_RANGE_MILES", 500.0)
        mpg = getattr(settings, "VEHICLE_MPG", 10.0)

        try:
            start_lat, start_lng, start_text = _resolve_location(data["start"])
            finish_lat, finish_lng, finish_text = _resolve_location(data["finish"])
        except ValueError as exc:
            return Response(
                {"error": "geocode_failed", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ORSError as exc:
            logger.exception("ORS geocode error")
            return Response(
                {"error": "ors_error", "detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            route = _get_or_create_route(
                start_lat,
                start_lng,
                finish_lat,
                finish_lng,
                start_text,
                finish_text,
            )
        except ORSError as exc:
            logger.exception("ORS directions error")
            detail = str(exc)
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
            if "404" in detail or "no route" in detail.lower():
                return Response(
                    {"error": "no_route", "detail": detail},
                    status=code,
                )
            return Response(
                {"error": "ors_error", "detail": detail},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        geometry = route.geometry
        distance_miles = route.distance_miles

        try:
            min_lat, min_lng, max_lat, max_lng = geo.bbox_of(
                geometry, pad_miles=corridor_miles + 5.0
            )
        except ValueError:
            return Response(
                {"error": "invalid_route", "detail": "Route geometry is empty"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        candidates = FuelStop.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            latitude__gte=min_lat,
            latitude__lte=max_lat,
            longitude__gte=min_lng,
            longitude__lte=max_lng,
        )

        matched = geo.match_stops_to_route(
            geometry, list(candidates), corridor_miles=corridor_miles
        )
        stop_dicts = [_stop_to_dict(m) for m in matched]

        result = fuel_optimizer.optimize(
            stop_dicts,
            total_distance_miles=distance_miles,
            range_miles=range_miles,
            mpg=mpg,
            start_full_tank=start_full_tank,
        )

        if not result["feasible"]:
            return Response(
                {
                    "error": "infeasible",
                    "detail": result["reason"],
                    "route": {"type": "LineString", "coordinates": geometry},
                    "distance_miles": round(distance_miles, 2),
                    "route_id": route.pk,
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        fuel_stops_out = []
        for stop in result["fuel_stops"]:
            fuel_stops_out.append(
                {
                    "opis_id": stop.get("opis_id"),
                    "name": stop.get("name"),
                    "city": stop.get("city"),
                    "state": stop.get("state"),
                    "latitude": stop.get("latitude"),
                    "longitude": stop.get("longitude"),
                    "mile_marker": stop.get("mile_marker"),
                    "retail_price": f"{stop['retail_price']:.4f}",
                    "gallons": f"{stop['gallons']:.2f}",
                    "cost": f"{stop['cost']:.2f}",
                }
            )

        return Response(
            {
                "route_id": route.pk,
                "route": {"type": "LineString", "coordinates": geometry},
                "distance_miles": round(distance_miles, 2),
                "fuel_stops": fuel_stops_out,
                "total_cost": f"{result['total_cost']:.2f}",
                "total_gallons": f"{result['total_gallons']:.2f}",
                "map_url": f"/api/routes/{route.pk}/map/",
                "assumptions": {
                    "range_miles": range_miles,
                    "mpg": mpg,
                    "start_full_tank": start_full_tank,
                    "corridor_miles": corridor_miles,
                },
            }
        )


def route_map(request, pk: int):
    """GET /api/routes/<id>/map/ — Leaflet HTML map of a cached route + fuel stops."""
    route = get_object_or_404(RouteCache, pk=pk)

    corridor = float(request.GET.get("corridor_miles", settings.DEFAULT_CORRIDOR_MILES))
    start_full_tank = request.GET.get("start_full_tank", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    geometry = route.geometry
    min_lat, min_lng, max_lat, max_lng = geo.bbox_of(geometry, pad_miles=corridor + 5.0)
    candidates = FuelStop.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lng,
        longitude__lte=max_lng,
    )
    matched = geo.match_stops_to_route(geometry, list(candidates), corridor_miles=corridor)
    stop_dicts = [_stop_to_dict(m) for m in matched]
    result = fuel_optimizer.optimize(
        stop_dicts,
        total_distance_miles=route.distance_miles,
        range_miles=settings.VEHICLE_RANGE_MILES,
        mpg=settings.VEHICLE_MPG,
        start_full_tank=start_full_tank,
    )

    context = {
        "route_json": json.dumps(geometry),
        "stops_json": json.dumps(result["fuel_stops"]),
        "stop_count": len(result["fuel_stops"]),
        "start_text": route.start_text or "Start",
        "finish_text": route.finish_text or "Finish",
        "distance_miles": round(route.distance_miles, 1),
        "total_cost": f"{result['total_cost']:.2f}",
        "total_gallons": f"{result['total_gallons']:.2f}",
        "feasible": result["feasible"],
        "reason": result.get("reason") or "",
        "start_lat": route.start_lat,
        "start_lng": route.start_lng,
        "finish_lat": route.finish_lat,
        "finish_lng": route.finish_lng,
    }
    return render(request, "routes/map.html", context)
