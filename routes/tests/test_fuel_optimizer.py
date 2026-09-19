"""Unit tests for the fuel optimizer."""

from django.test import SimpleTestCase

from routes.services.fuel_optimizer import optimize
from routes.tests.factories import make_stop


class FuelOptimizerTests(SimpleTestCase):
    def test_short_trip_no_refuel(self):
        """Full tank covers the whole trip — no purchases."""
        stops = [make_stop(100, 3.0, opis_id=1)]
        result = optimize(stops, total_distance_miles=400, start_full_tank=True)
        self.assertTrue(result["feasible"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["fuel_stops"], [])
        self.assertEqual(result["total_cost"], 0.0)
        self.assertEqual(result["total_gallons"], 0.0)

    def test_infeasible_large_gap(self):
        stops = [make_stop(100, 3.0, opis_id=1), make_stop(800, 3.0, opis_id=2)]
        result = optimize(stops, total_distance_miles=900, start_full_tank=True)
        self.assertFalse(result["feasible"])
        self.assertIn("gap", result["reason"])

    def test_empty_start_requires_station_at_start(self):
        stops = [make_stop(50, 3.0, opis_id=1)]
        result = optimize(
            stops, total_distance_miles=400, start_full_tank=False
        )
        self.assertFalse(result["feasible"])

    def test_duplicate_markers_keep_cheapest(self):
        stops = [
            make_stop(200, 4.0, opis_id=1),
            make_stop(200, 3.0, opis_id=2),
        ]
        result = optimize(stops, total_distance_miles=600, start_full_tank=True)
        self.assertTrue(result["feasible"])
        # Must buy at mile 200 to finish (600 - 500 = 100 mi short)
        self.assertEqual(len(result["fuel_stops"]), 1)
        self.assertEqual(result["fuel_stops"][0]["opis_id"], 2)
        self.assertAlmostEqual(result["fuel_stops"][0]["retail_price"], 3.0)

    def test_greedy_prefers_cheaper_station(self):
        """
        Trip 1200 mi, start full (500 mi free).
        Stations: 100@$3, 600@$4, 1100@$3.
        After driving to 100 (fuel left 400), no cheaper than inf so we arrived
        at 100 with price $3. From 100, 600@$4 is reachable but not cheaper;
        1100 is out of range (1000 mi). So fill at 100 and go farthest (600).
        At 600@$4, 1100@$3 is cheaper and reachable — buy just enough to reach 1100.
        At 1100 buy enough to finish.
        """
        stops = [
            make_stop(100, 3.0, opis_id=1),
            make_stop(600, 4.0, opis_id=2),
            make_stop(1100, 3.0, opis_id=3),
        ]
        result = optimize(
            stops, total_distance_miles=1200, mpg=10, start_full_tank=True
        )
        self.assertTrue(result["feasible"], result.get("reason"))
        self.assertIsNone(result["reason"])
        self.assertGreater(len(result["fuel_stops"]), 0)

        # Total fuel needed beyond initial tank: (1200 - 500) / 10 = 70 gallons
        self.assertAlmostEqual(result["total_gallons"], 70.0, places=1)

        # Should purchase at cheap stations (1 and 3), possibly little/none at 2
        bought_ids = {s["opis_id"] for s in result["fuel_stops"]}
        self.assertIn(1, bought_ids)
        self.assertIn(3, bought_ids)

        # Exact gallon amounts at intermediate stops
        by_id = {s["opis_id"]: s for s in result["fuel_stops"]}
        # At station 1 (mile 100): fill completely from remaining 400 mi fuel
        # → buy (500 - 400) / 10 = 10 gal, then drive to 600
        self.assertAlmostEqual(by_id[1]["gallons"], 10.0, places=1)
        # At station 2 (mile 600): buy just enough to reach 1100
        # Arrived with 0 fuel after 500 mi drive; need 500 mi → 50 gal... wait
        # After fill at 100, fuel=500, drive 500 mi to 600 → fuel=0.
        # Cheaper at 1100 is within full tank (500 mi); buy (500 - 0)/10 = 50 gal
        # to reach 1100... need_miles = 500, buy_gal = (500 - 0)/10 = 50.
        # But we only need enough to reach cheaper — yes 50 gal.
        # Actually at 600 with fuel=0, buy just enough: (1100-600 - 0)/10 = 50 gal.
        self.assertAlmostEqual(by_id[2]["gallons"], 50.0, places=1)
        # At 1100: need 100 mi more → 10 gal
        self.assertAlmostEqual(by_id[3]["gallons"], 10.0, places=1)

        # Cost should be less than buying everything at $4
        self.assertLess(result["total_cost"], 70 * 4.0)

    def test_single_refuel_mid_route(self):
        """800 mi trip: need 30 gal beyond initial tank."""
        stops = [make_stop(400, 3.5, opis_id=10)]
        result = optimize(stops, total_distance_miles=800, start_full_tank=True)
        self.assertTrue(result["feasible"])
        self.assertIsNone(result["reason"])
        self.assertEqual(len(result["fuel_stops"]), 1)
        self.assertAlmostEqual(result["total_gallons"], 30.0, places=1)
        self.assertAlmostEqual(result["total_cost"], 30.0 * 3.5, places=1)

    def test_negative_distance_infeasible(self):
        result = optimize([], total_distance_miles=-10, start_full_tank=True)
        self.assertFalse(result["feasible"])
        self.assertIn("non-negative", result["reason"])

    def test_empty_stops_long_trip_infeasible(self):
        result = optimize([], total_distance_miles=600, start_full_tank=True)
        self.assertFalse(result["feasible"])
        self.assertIn("gap", result["reason"])

    def test_empty_tank_with_station_at_start_feasible(self):
        """start_full_tank=False requires a station at ~mile 0 (marker <= 1e-6)."""
        # Marker must be > 0 (filter) and <= 1e-6 (empty-tank check), and
        # reachable with look_ahead=0 (marker <= 1e-9 tolerance).
        stops = [make_stop(1e-9, 3.0, opis_id=1)]
        result = optimize(
            stops, total_distance_miles=400, mpg=10, start_full_tank=False
        )
        self.assertTrue(result["feasible"], result.get("reason"))
        self.assertEqual(len(result["fuel_stops"]), 1)
        # Need full 400 mi of fuel at 10 mpg = 40 gallons
        self.assertAlmostEqual(result["total_gallons"], 40.0, places=1)
        self.assertAlmostEqual(result["total_cost"], 40.0 * 3.0, places=1)

    def test_gap_at_start_infeasible(self):
        """First station beyond range from origin."""
        stops = [make_stop(600, 3.0, opis_id=1)]
        result = optimize(stops, total_distance_miles=800, start_full_tank=True)
        self.assertFalse(result["feasible"])
        self.assertIn("gap", result["reason"])

    def test_custom_range_and_mpg(self):
        """Custom range_miles=300 and mpg=20: (500-300)/20 = 10 gallons."""
        stops = [make_stop(200, 4.0, opis_id=1)]
        result = optimize(
            stops,
            total_distance_miles=500,
            range_miles=300,
            mpg=20,
            start_full_tank=True,
        )
        self.assertTrue(result["feasible"], result.get("reason"))
        self.assertAlmostEqual(result["total_gallons"], 10.0, places=1)
        self.assertAlmostEqual(result["total_cost"], 10.0 * 4.0, places=1)

    def test_destination_reachable_after_single_fill(self):
        """
        700 mi trip, station at 300. Start full (500).
        Drive to 300 (fuel left 200). Fill enough to finish:
        need 400 more miles → buy 20 gal. Destination reachable; one purchase.
        """
        stops = [make_stop(300, 3.0, opis_id=5)]
        result = optimize(stops, total_distance_miles=700, start_full_tank=True)
        self.assertTrue(result["feasible"], result.get("reason"))
        self.assertEqual(len(result["fuel_stops"]), 1)
        self.assertAlmostEqual(result["total_gallons"], 20.0, places=1)
        self.assertEqual(result["fuel_stops"][0]["opis_id"], 5)

    def test_purchase_consolidation_same_opis(self):
        """
        Force two buys at the same station via the record() merge path.
        Trip 900 mi, single station at 400 @ $3.
        Start full → drive to 400 (fuel 100), fill to finish (need 500 mi → 40 gal
        of the 50-gal tank capacity? range=500, fuel=100, buy (500-100)/10=40 gal
        to fill, then can reach destination at 900). One purchase entry.
        """
        stops = [make_stop(400, 3.0, opis_id=99)]
        result = optimize(stops, total_distance_miles=900, start_full_tank=True)
        self.assertTrue(result["feasible"], result.get("reason"))
        # Only one stop exists, so purchases should be a single consolidated entry
        self.assertEqual(len(result["fuel_stops"]), 1)
        self.assertEqual(result["fuel_stops"][0]["opis_id"], 99)
        # (900 - 500) / 10 = 40 gallons total
        self.assertAlmostEqual(result["total_gallons"], 40.0, places=1)

    def test_boundary_stops_filtered(self):
        """Stops at mile 0 or at destination are excluded from planning."""
        stops = [
            make_stop(0, 2.0, opis_id=1),
            make_stop(400, 3.0, opis_id=2),
            make_stop(800, 2.5, opis_id=3),  # at destination — filtered
        ]
        result = optimize(stops, total_distance_miles=800, start_full_tank=True)
        self.assertTrue(result["feasible"], result.get("reason"))
        bought_ids = {s["opis_id"] for s in result["fuel_stops"]}
        self.assertNotIn(1, bought_ids)
        self.assertNotIn(3, bought_ids)
        self.assertIn(2, bought_ids)
