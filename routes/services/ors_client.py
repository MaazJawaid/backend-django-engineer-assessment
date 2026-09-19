"""OpenRouteService (HeiGIT) API client for geocoding and directions."""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.heigit.org"
METERS_PER_MILE = 1609.344


class ORSError(Exception):
    """Raised when the OpenRouteService API returns an error."""


class ORSClient:
    """Thin client for HeiGIT OpenRouteService geocoding and directions."""

    def __init__(self, api_key: Optional[str] = None, timeout: int = 10):
        self.api_key = api_key if api_key is not None else getattr(settings, "ORS_API_KEY", "")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": self.api_key,
                # GeoJSON directions require application/geo+json; json-only Accept → 406
                "Accept": "application/json, application/geo+json",
            }
        )

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if not self.api_key:
            raise ORSError("ORS_API_KEY is not configured. Set it in your .env file.")

        kwargs.setdefault("timeout", self.timeout)
        last_error: Optional[Exception] = None

        for attempt in range(2):
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise ORSError(f"ORS request failed: {exc}") from exc

        if last_error:
            raise ORSError(f"ORS request failed: {last_error}") from last_error
        raise ORSError("ORS request failed after retries")

    def geocode(self, text: str) -> Optional[tuple[float, float]]:
        """
        Geocode a free-text location within the USA.

        Returns (lat, lng) or None if no result.
        """
        url = f"{BASE_URL}/pelias/v1/search"
        params = {
            "text": text,
            "size": 1,
            "boundary.country": "US",
        }
        response = self._request("GET", url, params=params)

        if response.status_code != 200:
            raise ORSError(
                f"Geocode failed ({response.status_code}): {response.text[:300]}"
            )

        data = response.json()
        features = data.get("features") or []
        if not features:
            return None

        coords = features[0]["geometry"]["coordinates"]  # [lng, lat]
        lng, lat = float(coords[0]), float(coords[1])
        return (lat, lng)

    def directions(
        self, coords: list[tuple[float, float]]
    ) -> dict:
        """
        Get a driving route between coordinates.

        Args:
            coords: list of (lat, lng) pairs, typically start and finish.

        Returns:
            {"geometry": [[lng, lat], ...], "distance_miles": float}
        """
        if len(coords) < 2:
            raise ORSError("directions requires at least two coordinates")

        url = f"{BASE_URL}/openrouteservice/v2/directions/driving-car/geojson"
        # ORS expects [lng, lat]
        body = {
            "coordinates": [[lng, lat] for lat, lng in coords],
            "instructions": False,
        }
        headers = {"Content-Type": "application/json"}
        response = self._request("POST", url, json=body, headers=headers)

        if response.status_code != 200:
            raise ORSError(
                f"Directions failed ({response.status_code}): {response.text[:300]}"
            )

        data = response.json()
        features = data.get("features") or []
        if not features:
            raise ORSError("ORS returned no route features")

        feature = features[0]
        geometry = feature["geometry"]["coordinates"]
        props = feature.get("properties") or {}
        summary = props.get("summary") or {}
        distance_m = summary.get("distance")
        if distance_m is None:
            segments = props.get("segments") or []
            distance_m = sum(seg.get("distance", 0) for seg in segments)

        distance_miles = float(distance_m) / METERS_PER_MILE
        return {
            "geometry": geometry,
            "distance_miles": distance_miles,
        }


# Module-level convenience instance (lazy — key read at call time via settings)
_default_client: Optional[ORSClient] = None


def get_client() -> ORSClient:
    global _default_client
    if _default_client is None:
        _default_client = ORSClient()
    return _default_client


def geocode(text: str) -> Optional[tuple[float, float]]:
    return get_client().geocode(text)


def directions(coords: list[tuple[float, float]]) -> dict:
    return get_client().directions(coords)
