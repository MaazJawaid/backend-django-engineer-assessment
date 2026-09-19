"""Unit tests for geo helpers."""

from django.test import SimpleTestCase

from routes.services.geo import (
    bbox_of,
    haversine,
    match_stops_to_route,
    point_to_segment_distance,
)
from routes.tests.factories import FakeStop


class GeoTests(SimpleTestCase):
    def test_haversine_nyc_to_la_approx(self):
        # NYC approx to LA approx ~2445 miles
        dist = haversine(40.7128, -74.0060, 34.0522, -118.2437)
        self.assertAlmostEqual(dist, 2445, delta=50)

    def test_haversine_zero(self):
        self.assertAlmostEqual(haversine(41.0, -87.0, 41.0, -87.0), 0.0, places=6)

    def test_haversine_one_degree_lat(self):
        # 1 degree of latitude ≈ 69 miles
        dist = haversine(40.0, -90.0, 41.0, -90.0)
        self.assertAlmostEqual(dist, 69.0, delta=1.0)

    def test_point_on_segment(self):
        # Point midway on a short east-west segment
        dist, plat, plng, t = point_to_segment_distance(
            40.0, -90.5, 40.0, -91.0, 40.0, -90.0
        )
        self.assertAlmostEqual(t, 0.5, places=2)
        self.assertLess(dist, 0.5)

    def test_point_off_segment(self):
        dist, _, _, t = point_to_segment_distance(
            41.0, -90.5, 40.0, -91.0, 40.0, -90.0
        )
        self.assertGreater(dist, 60)  # roughly 69 mi per degree lat
        self.assertGreaterEqual(t, 0.0)
        self.assertLessEqual(t, 1.0)

    def test_degenerate_zero_length_segment(self):
        dist, plat, plng, t = point_to_segment_distance(
            40.5, -90.0, 40.0, -90.0, 40.0, -90.0
        )
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(plat, 40.0)
        self.assertAlmostEqual(plng, -90.0)
        self.assertAlmostEqual(dist, haversine(40.5, -90.0, 40.0, -90.0), places=4)

    def test_bbox_padding(self):
        coords = [[-90.0, 40.0], [-89.0, 41.0]]
        min_lat, min_lng, max_lat, max_lng = bbox_of(coords, pad_miles=10)
        self.assertLess(min_lat, 40.0)
        self.assertGreater(max_lat, 41.0)
        self.assertLess(min_lng, -90.0)
        self.assertGreater(max_lng, -89.0)

    def test_bbox_empty_raises(self):
        with self.assertRaises(ValueError):
            bbox_of([])

    def test_match_stops_to_route(self):
        # Straight-ish route along lat 40, lng -91 to -89
        route = [[-91.0, 40.0], [-90.0, 40.0], [-89.0, 40.0]]
        on_route = FakeStop(40.01, -90.0)  # ~0.7 mi off
        far = FakeStop(42.0, -90.0)  # far north
        matched = match_stops_to_route(route, [on_route, far], corridor_miles=5.0)
        self.assertEqual(len(matched), 1)
        self.assertIs(matched[0]["stop"], on_route)
        self.assertGreater(matched[0]["mile_marker"], 0)
        self.assertIn("distance_off_route", matched[0])
        self.assertLess(matched[0]["distance_off_route"], 5.0)

    def test_match_short_route_returns_empty(self):
        self.assertEqual(
            match_stops_to_route([[-90.0, 40.0]], [FakeStop(40.0, -90.0)], 5.0),
            [],
        )
        self.assertEqual(match_stops_to_route([], [FakeStop(40.0, -90.0)], 5.0), [])

    def test_match_skips_null_coords(self):
        route = [[-91.0, 40.0], [-89.0, 40.0]]
        null_stop = FakeStop(None, None)
        good = FakeStop(40.0, -90.0)
        matched = match_stops_to_route(route, [null_stop, good], corridor_miles=5.0)
        self.assertEqual(len(matched), 1)
        self.assertIs(matched[0]["stop"], good)

    def test_corridor_boundary_inclusive(self):
        """Stop exactly at corridor_miles distance is included (<=)."""
        route = [[-91.0, 40.0], [-89.0, 40.0]]
        # ~5 miles north of the route
        offset_deg = 5.0 / 69.0
        stop = FakeStop(40.0 + offset_deg, -90.0)

        # Measure actual off-route distance with a wide corridor
        probed = match_stops_to_route(route, [stop], corridor_miles=100.0)
        self.assertEqual(len(probed), 1)
        actual_dist = probed[0]["distance_off_route"]
        self.assertGreater(actual_dist, 0)

        matched_in = match_stops_to_route(
            route, [stop], corridor_miles=actual_dist
        )
        matched_out = match_stops_to_route(
            route, [stop], corridor_miles=actual_dist - 0.01
        )
        self.assertEqual(len(matched_in), 1)
        self.assertAlmostEqual(
            matched_in[0]["distance_off_route"], actual_dist, places=4
        )
        self.assertEqual(len(matched_out), 0)

    def test_mile_marker_multi_segment_accuracy(self):
        """
        Route with known segment lengths; stop projects onto 2nd segment at t≈0.5.
        Marker ≈ seg1_len + 0.5 * seg2_len.
        """
        # Three points along lat 40, each ~1 degree lng apart (~53 mi at lat 40)
        route = [[-91.0, 40.0], [-90.0, 40.0], [-89.0, 40.0], [-88.0, 40.0]]
        seg1 = haversine(40.0, -91.0, 40.0, -90.0)
        seg2 = haversine(40.0, -90.0, 40.0, -89.0)
        # Midpoint of second segment: lng = -89.5
        stop = FakeStop(40.0, -89.5)
        matched = match_stops_to_route(route, [stop], corridor_miles=5.0)
        self.assertEqual(len(matched), 1)
        expected = seg1 + 0.5 * seg2
        self.assertAlmostEqual(matched[0]["mile_marker"], expected, delta=2.0)
        self.assertAlmostEqual(matched[0]["distance_off_route"], 0.0, delta=0.5)
