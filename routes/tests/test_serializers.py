"""Unit tests for OptimizeRouteSerializer validation branches."""

from django.test import SimpleTestCase

from routes.serializers import OptimizeRouteSerializer


class OptimizeRouteSerializerTests(SimpleTestCase):
    def test_text_start_finish_valid(self):
        ser = OptimizeRouteSerializer(
            data={"start": "  Chicago, IL  ", "finish": "Dallas, TX"}
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["start"]["type"], "text")
        self.assertEqual(ser.validated_data["start"]["value"], "Chicago, IL")
        self.assertEqual(ser.validated_data["finish"]["type"], "text")
        self.assertEqual(ser.validated_data["finish"]["value"], "Dallas, TX")

    def test_empty_string_start_invalid(self):
        ser = OptimizeRouteSerializer(
            data={"start": "", "finish": "Dallas, TX"}
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("non-empty string", str(ser.errors["start"]))

    def test_whitespace_only_string_invalid(self):
        ser = OptimizeRouteSerializer(
            data={"start": "   ", "finish": "Dallas, TX"}
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("non-empty string", str(ser.errors["start"]))

    def test_coords_dict_valid(self):
        ser = OptimizeRouteSerializer(
            data={
                "start": {"lat": 41.8781, "lng": -87.6298},
                "finish": {"lat": 32.7767, "lng": -96.7970},
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        start = ser.validated_data["start"]
        self.assertEqual(start["type"], "coords")
        self.assertAlmostEqual(start["lat"], 41.8781)
        self.assertAlmostEqual(start["lng"], -87.6298)
        self.assertEqual(start["value"], "41.8781,-87.6298")

    def test_coords_missing_lng_invalid(self):
        ser = OptimizeRouteSerializer(
            data={
                "start": {"lat": 41.8781},
                "finish": "Dallas, TX",
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("must include lat and lng", str(ser.errors["start"]))

    def test_coords_non_numeric_invalid(self):
        ser = OptimizeRouteSerializer(
            data={
                "start": {"lat": "not-a-number", "lng": -87.6298},
                "finish": "Dallas, TX",
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("must be numbers", str(ser.errors["start"]))

    def test_coords_lat_out_of_range_invalid(self):
        ser = OptimizeRouteSerializer(
            data={
                "start": {"lat": 91.0, "lng": -87.6298},
                "finish": "Dallas, TX",
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("out of range", str(ser.errors["start"]))

    def test_coords_lng_out_of_range_invalid(self):
        ser = OptimizeRouteSerializer(
            data={
                "start": {"lat": 41.0, "lng": 181.0},
                "finish": "Dallas, TX",
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("out of range", str(ser.errors["start"]))

    def test_list_location_invalid(self):
        ser = OptimizeRouteSerializer(
            data={"start": [41.0, -87.0], "finish": "Dallas, TX"}
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("start", ser.errors)
        self.assertIn("must be a string or", str(ser.errors["start"]))

    def test_defaults_start_full_tank_and_corridor(self):
        ser = OptimizeRouteSerializer(
            data={"start": "Chicago, IL", "finish": "Dallas, TX"}
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertTrue(ser.validated_data["start_full_tank"])
        self.assertAlmostEqual(ser.validated_data["corridor_miles"], 5.0)

    def test_corridor_miles_below_min_invalid(self):
        ser = OptimizeRouteSerializer(
            data={
                "start": "Chicago, IL",
                "finish": "Dallas, TX",
                "corridor_miles": 0.05,
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("corridor_miles", ser.errors)
