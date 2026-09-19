"""Unit tests for ORSClient — all HTTP mocked, no network."""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from routes.services.ors_client import METERS_PER_MILE, ORSClient, ORSError
from routes.tests.factories import (
    fake_geometry,
    ors_directions_response,
    ors_geocode_response,
)


def _json_response(status_code: int, payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


class ORSClientTests(SimpleTestCase):
    def setUp(self):
        self.client = ORSClient(api_key="test-key", timeout=5)

    @patch("routes.services.ors_client.time.sleep")
    def test_geocode_success(self, _sleep):
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(200, ors_geocode_response(41.8781, -87.6298)),
        ) as mock_req:
            result = self.client.geocode("Chicago, IL")
        self.assertEqual(result, (41.8781, -87.6298))
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["params"]["boundary.country"], "US")
        self.assertEqual(self.client.session.headers["Authorization"], "test-key")

    @patch("routes.services.ors_client.time.sleep")
    def test_geocode_no_features_returns_none(self, _sleep):
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(200, {"features": []}),
        ):
            self.assertIsNone(self.client.geocode("Nowhere, XX"))

    @patch("routes.services.ors_client.time.sleep")
    def test_geocode_non_200_raises(self, _sleep):
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(403, {"error": "forbidden"}),
        ):
            with self.assertRaises(ORSError) as ctx:
                self.client.geocode("Chicago, IL")
        self.assertIn("403", str(ctx.exception))

    @patch("routes.services.ors_client.time.sleep")
    def test_directions_success_meters_to_miles(self, _sleep):
        coords = fake_geometry()
        payload = ors_directions_response(coords, distance_miles=100.0)
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(200, payload),
        ):
            result = self.client.directions([(41.8781, -87.6298), (39.7684, -86.1581)])
        self.assertEqual(result["geometry"], coords)
        self.assertAlmostEqual(result["distance_miles"], 100.0, places=4)

    def test_directions_requires_two_coords(self):
        with self.assertRaises(ORSError) as ctx:
            self.client.directions([(41.0, -87.0)])
        self.assertIn("at least two coordinates", str(ctx.exception))

    @patch("routes.services.ors_client.time.sleep")
    def test_directions_no_features_raises(self, _sleep):
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(200, {"features": []}),
        ):
            with self.assertRaises(ORSError) as ctx:
                self.client.directions([(41.0, -87.0), (39.0, -86.0)])
        self.assertIn("no route features", str(ctx.exception))

    @patch("routes.services.ors_client.time.sleep")
    def test_directions_non_200_raises(self, _sleep):
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(500, {"error": "server"}),
        ):
            # 500 triggers one retry then returns the same response
            with self.assertRaises(ORSError) as ctx:
                self.client.directions([(41.0, -87.0), (39.0, -86.0)])
        self.assertIn("500", str(ctx.exception))

    @patch("routes.services.ors_client.time.sleep")
    def test_directions_falls_back_to_segments(self, _sleep):
        coords = fake_geometry()
        payload = ors_directions_response(
            coords, distance_miles=50.0, use_segments=True
        )
        with patch.object(
            self.client.session,
            "request",
            return_value=_json_response(200, payload),
        ):
            result = self.client.directions([(41.0, -87.0), (39.0, -86.0)])
        self.assertAlmostEqual(result["distance_miles"], 50.0, places=4)
        self.assertAlmostEqual(
            50.0 * METERS_PER_MILE / METERS_PER_MILE, result["distance_miles"], places=4
        )

    def test_missing_api_key_raises(self):
        client = ORSClient(api_key="")
        with self.assertRaises(ORSError) as ctx:
            client.geocode("Chicago, IL")
        self.assertIn("ORS_API_KEY is not configured", str(ctx.exception))

    @patch("routes.services.ors_client.time.sleep")
    def test_retry_on_429_then_success(self, mock_sleep):
        ok = _json_response(200, ors_geocode_response(41.0, -87.0))
        rate_limited = _json_response(429, {"error": "rate limit"})
        with patch.object(
            self.client.session,
            "request",
            side_effect=[rate_limited, ok],
        ) as mock_req:
            result = self.client.geocode("Chicago, IL")
        self.assertEqual(result, (41.0, -87.0))
        self.assertEqual(mock_req.call_count, 2)
        mock_sleep.assert_called()

    @patch("routes.services.ors_client.time.sleep")
    def test_retry_on_request_exception_then_success(self, mock_sleep):
        ok = _json_response(200, ors_geocode_response(41.0, -87.0))
        with patch.object(
            self.client.session,
            "request",
            side_effect=[requests.ConnectionError("boom"), ok],
        ) as mock_req:
            result = self.client.geocode("Chicago, IL")
        self.assertEqual(result, (41.0, -87.0))
        self.assertEqual(mock_req.call_count, 2)
        mock_sleep.assert_called()

    @patch("routes.services.ors_client.time.sleep")
    def test_request_exception_exhausted_raises(self, mock_sleep):
        with patch.object(
            self.client.session,
            "request",
            side_effect=requests.Timeout("timed out"),
        ):
            with self.assertRaises(ORSError) as ctx:
                self.client.geocode("Chicago, IL")
        self.assertIn("ORS request failed", str(ctx.exception))
        mock_sleep.assert_called()
