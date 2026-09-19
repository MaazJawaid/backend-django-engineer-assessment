"""API integration tests with mocked OpenRouteService."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from routes.models import FuelStop, GeocodeCache, RouteCache
from routes.services.ors_client import ORSError
from routes.tests.factories import fake_geometry


class OptimizeAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Seed a few stops along a fake Chicago -> Indianapolis corridor
        FuelStop.objects.create(
            opis_id=1,
            name="Cheap Stop",
            address="I-65",
            city="Lafayette",
            state="IN",
            retail_price=Decimal("3.0000"),
            latitude=40.4167,
            longitude=-86.8753,
            geocode_source="gazetteer",
        )
        FuelStop.objects.create(
            opis_id=2,
            name="Pricey Stop",
            address="I-65",
            city="Lebanon",
            state="IN",
            retail_price=Decimal("4.5000"),
            latitude=40.0484,
            longitude=-86.4692,
            geocode_source="gazetteer",
        )

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_optimize_success(self, mock_geocode, mock_directions):
        mock_geocode.side_effect = [
            (41.8781, -87.6298),  # Chicago
            (39.7684, -86.1581),  # Indianapolis
        ]
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }

        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": "Chicago, IL",
                "finish": "Indianapolis, IN",
                "start_full_tank": True,
                "corridor_miles": 20,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn("route", body)
        self.assertEqual(body["route"]["type"], "LineString")
        self.assertIn("distance_miles", body)
        self.assertIn("fuel_stops", body)
        self.assertIn("total_cost", body)
        self.assertIn("total_gallons", body)
        self.assertIn("route_id", body)
        self.assertTrue(body["assumptions"]["start_full_tank"])

        # City-level inputs resolve via the bundled gazetteer -> no ORS geocode.
        self.assertEqual(mock_geocode.call_count, 0)
        self.assertEqual(mock_directions.call_count, 1)

        # Gazetteer hits are not written to GeocodeCache (ORS-only); route is cached.
        self.assertEqual(GeocodeCache.objects.count(), 0)
        self.assertEqual(RouteCache.objects.count(), 1)

        # Second identical request hits the route cache — still no ORS calls.
        resp2 = self.client.post(
            "/api/routes/optimize/",
            {
                "start": "Chicago, IL",
                "finish": "Indianapolis, IN",
                "start_full_tank": True,
                "corridor_miles": 20,
            },
            format="json",
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(mock_geocode.call_count, 0)  # unchanged
        self.assertEqual(mock_directions.call_count, 1)  # unchanged

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_response_shape_details(self, mock_geocode, mock_directions):
        mock_geocode.side_effect = [
            (41.8781, -87.6298),
            (39.7684, -86.1581),
        ]
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": "Chicago, IL",
                "finish": "Indianapolis, IN",
                "corridor_miles": 20,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        route_id = body["route_id"]
        self.assertEqual(body["map_url"], f"/api/routes/{route_id}/map/")
        self.assertEqual(body["assumptions"]["range_miles"], 500.0)
        self.assertEqual(body["assumptions"]["mpg"], 10.0)
        self.assertEqual(body["assumptions"]["corridor_miles"], 20)
        # Short trip — no fuel stops; totals are zero-formatted strings
        self.assertEqual(body["total_cost"], "0.00")
        self.assertEqual(body["total_gallons"], "0.00")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_coords_input_skips_geocode(self, mock_geocode, mock_directions):
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": {"lat": 41.8781, "lng": -87.6298},
                "finish": {"lat": 39.7684, "lng": -86.1581},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        mock_geocode.assert_not_called()
        mock_directions.assert_called_once()

    def test_bad_request_missing_fields(self):
        resp = self.client.post("/api/routes/optimize/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_health(self):
        resp = self.client.get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_map_view(self, mock_geocode, mock_directions):
        mock_geocode.side_effect = [
            (41.8781, -87.6298),
            (39.7684, -86.1581),
        ]
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        opt = self.client.post(
            "/api/routes/optimize/",
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            format="json",
        )
        route_id = opt.json()["route_id"]
        resp = self.client.get(f"/api/routes/{route_id}/map/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"leaflet", resp.content.lower())
        self.assertIn(b"arcgisonline", resp.content.lower())

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_geocode_failed_returns_400(self, mock_geocode, mock_directions):
        mock_geocode.return_value = None
        resp = self.client.post(
            "/api/routes/optimize/",
            {"start": "Nowhere, XX", "finish": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "geocode_failed")
        mock_directions.assert_not_called()

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_ors_error_geocode_returns_502(self, mock_geocode, mock_directions):
        mock_geocode.side_effect = ORSError("Geocode failed (503): unavailable")
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": "1600 Amphitheatre Pkwy, Mountain View, CA",
                "finish": "Dallas, TX",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error"], "ors_error")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_no_route_returns_422(self, mock_geocode, mock_directions):
        mock_geocode.side_effect = [
            (41.8781, -87.6298),
            (39.7684, -86.1581),
        ]
        mock_directions.side_effect = ORSError(
            "Directions failed (404): no route found"
        )
        resp = self.client.post(
            "/api/routes/optimize/",
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"], "no_route")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_ors_error_directions_returns_502(self, mock_geocode, mock_directions):
        mock_geocode.side_effect = [
            (41.8781, -87.6298),
            (39.7684, -86.1581),
        ]
        mock_directions.side_effect = ORSError("Directions failed (500): boom")
        resp = self.client.post(
            "/api/routes/optimize/",
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            format="json",
        )
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error"], "ors_error")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_invalid_route_empty_geometry_returns_422(
        self, mock_geocode, mock_directions
    ):
        mock_geocode.side_effect = [
            (41.8781, -87.6298),
            (39.7684, -86.1581),
        ]
        mock_directions.return_value = {
            "geometry": [],
            "distance_miles": 0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"], "invalid_route")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_infeasible_returns_422_with_route(self, mock_geocode, mock_directions):
        FuelStop.objects.all().delete()
        mock_geocode.side_effect = [
            (41.8781, -87.6298),
            (32.7767, -96.7970),
        ]
        # Long route with no fuel stops → gap exceeds range
        mock_directions.return_value = {
            "geometry": [
                [-87.6298, 41.8781],
                [-96.7970, 32.7767],
            ],
            "distance_miles": 1000.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": "Chicago, IL",
                "finish": "Dallas, TX",
                "corridor_miles": 5,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["error"], "infeasible")
        self.assertIn("route", body)
        self.assertIn("route_id", body)
        self.assertEqual(body["route"]["type"], "LineString")

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_geocode_cache_hit_skips_ors(self, mock_geocode, mock_directions):
        GeocodeCache.objects.create(
            query="chicago, il",
            latitude=41.8781,
            longitude=-87.6298,
            source="ors",
        )
        GeocodeCache.objects.create(
            query="indianapolis, in",
            latitude=39.7684,
            longitude=-86.1581,
            source="ors",
        )
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        mock_geocode.assert_not_called()
        mock_directions.assert_called_once()

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_city_level_input_skips_ors_geocode(self, mock_geocode, mock_directions):
        """City-level 'City, ST' inputs resolve via the bundled gazetteer."""
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {"start": "Chicago, IL", "finish": "Indianapolis, IN"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        mock_geocode.assert_not_called()
        mock_directions.assert_called_once()
        # Gazetteer hits are not persisted to the ORS-only GeocodeCache.
        self.assertEqual(GeocodeCache.objects.count(), 0)

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_specific_address_still_calls_ors_geocode(
        self, mock_geocode, mock_directions
    ):
        """A street address is not city-level; it falls through to ORS geocode."""
        mock_geocode.side_effect = [
            (37.4220, -122.0841),  # 1600 Amphitheatre Pkwy, Mountain View, CA
        ]
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": "1600 Amphitheatre Pkwy, Mountain View, CA",
                "finish": "Chicago, IL",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        # Only the address required geocoding; the city was gazetteer-resolved.
        self.assertEqual(mock_geocode.call_count, 1)
        mock_directions.assert_called_once()
        self.assertEqual(GeocodeCache.objects.count(), 1)

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_cheap_stop_selected_on_long_route(
        self, mock_geocode, mock_directions
    ):
        """End-to-end: optimizer picks cost-effective stops on a long corridor."""
        FuelStop.objects.all().delete()
        # Eastbound corridor along lat 40: ~12 deg lng ≈ 636 mi at this latitude.
        # distance_miles must match geometry so mile markers align with destination.
        geometry = [
            [-91.0, 40.0],
            [-88.0, 40.0],
            [-85.0, 40.0],
            [-82.0, 40.0],
            [-79.0, 40.0],
        ]
        # ~636 mi total; full tank covers 500 → need ~13.6 gal beyond initial tank.
        distance_miles = 636.0
        FuelStop.objects.create(
            opis_id=10,
            name="Cheap Early",
            city="A",
            state="IL",
            retail_price=Decimal("2.5000"),
            latitude=40.0,
            longitude=-88.0,
            geocode_source="gazetteer",
        )
        FuelStop.objects.create(
            opis_id=20,
            name="Pricey Mid",
            city="B",
            state="IN",
            retail_price=Decimal("4.5000"),
            latitude=40.0,
            longitude=-85.0,
            geocode_source="gazetteer",
        )
        FuelStop.objects.create(
            opis_id=30,
            name="Cheap Late",
            city="C",
            state="OH",
            retail_price=Decimal("2.8000"),
            latitude=40.0,
            longitude=-82.0,
            geocode_source="gazetteer",
        )

        mock_directions.return_value = {
            "geometry": geometry,
            "distance_miles": distance_miles,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": {"lat": 40.0, "lng": -91.0},
                "finish": {"lat": 40.0, "lng": -79.0},
                "corridor_miles": 10,
                "start_full_tank": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertGreater(len(body["fuel_stops"]), 0)
        # Total gallons beyond initial tank: (636 - 500) / 10 ≈ 13.6
        self.assertAlmostEqual(float(body["total_gallons"]), 13.6, delta=1.0)
        bought_ids = {s["opis_id"] for s in body["fuel_stops"]}
        # Should prefer cheap stops over buying only at the pricey mid stop
        self.assertTrue(bought_ids & {10, 30})
        for stop in body["fuel_stops"]:
            self.assertRegex(stop["retail_price"], r"^\d+\.\d{4}$")
            self.assertRegex(stop["gallons"], r"^\d+\.\d{2}$")
            self.assertRegex(stop["cost"], r"^\d+\.\d{2}$")
        # Cost must be less than buying all fuel at $4.50
        self.assertLess(
            float(body["total_cost"]), float(body["total_gallons"]) * 4.50
        )

    @patch("routes.views.directions")
    @patch("routes.views.geocode")
    def test_start_full_tank_false_in_assumptions(
        self, mock_geocode, mock_directions
    ):
        # Place a stop essentially at the route start so empty-tank is feasible
        FuelStop.objects.create(
            opis_id=99,
            name="Start Station",
            city="Chicago",
            state="IL",
            retail_price=Decimal("3.2000"),
            latitude=41.8781,
            longitude=-87.6298,
            geocode_source="gazetteer",
        )
        mock_directions.return_value = {
            "geometry": fake_geometry(),
            "distance_miles": 180.0,
        }
        resp = self.client.post(
            "/api/routes/optimize/",
            {
                "start": {"lat": 41.8781, "lng": -87.6298},
                "finish": {"lat": 39.7684, "lng": -86.1581},
                "start_full_tank": False,
                "corridor_miles": 20,
            },
            format="json",
        )
        # May be 200 (feasible if start stop matched) or 422 (infeasible)
        body = resp.json()
        if resp.status_code == 200:
            self.assertFalse(body["assumptions"]["start_full_tank"])
        else:
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(body["error"], "infeasible")

    def test_map_404(self):
        resp = self.client.get("/api/routes/999999/map/")
        self.assertEqual(resp.status_code, 404)

    def test_map_infeasible_rendering(self):
        route = RouteCache.objects.create(
            start_lat=41.8781,
            start_lng=-87.6298,
            finish_lat=32.7767,
            finish_lng=-96.7970,
            geometry=[[-87.6298, 41.8781], [-96.7970, 32.7767]],
            distance_miles=1000.0,
            start_text="Chicago, IL",
            finish_text="Dallas, TX",
        )
        FuelStop.objects.all().delete()
        resp = self.client.get(f"/api/routes/{route.pk}/map/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8").lower()
        # Template shows infeasible reason when feasible is False
        self.assertTrue(
            "gap" in content or "infeasible" in content or "cannot" in content
            or "no reachable" in content or "exceeds" in content,
            msg=f"Expected infeasible messaging in map HTML, got: {content[:500]}",
        )

    def test_map_query_params(self):
        route = RouteCache.objects.create(
            start_lat=41.8781,
            start_lng=-87.6298,
            finish_lat=39.7684,
            finish_lng=-86.1581,
            geometry=fake_geometry(),
            distance_miles=180.0,
            start_text="Chicago, IL",
            finish_text="Indianapolis, IN",
        )
        resp = self.client.get(
            f"/api/routes/{route.pk}/map/",
            {"corridor_miles": "20", "start_full_tank": "false"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"leaflet", resp.content.lower())
