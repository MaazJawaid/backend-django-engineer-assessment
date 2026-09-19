"""Tests for load_fuel_stops management command."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from routes.models import FuelStop, GeocodeCache


FUEL_CSV_HEADER = (
    "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
)
GAZETTEER_HEADER = "city,state,lat,lng,population\n"


class LoadFuelStopsCommandTests(TestCase):
    def _write_temp(self, content: str, suffix: str = ".csv") -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def _gazetteer(self, *rows: str) -> Path:
        return self._write_temp(GAZETTEER_HEADER + "".join(rows))

    def _fuel_csv(self, *rows: str) -> Path:
        return self._write_temp(FUEL_CSV_HEADER + "".join(rows))

    def test_loads_valid_rows_joined_to_gazetteer(self):
        gaz = self._gazetteer("Lafayette,IN,40.4167,-86.8753,67000\n")
        csv_path = self._fuel_csv(
            "1,Cheap Stop,I-65,Lafayette,IN,100,3.2500\n"
            "2,Other Stop,Main St,Unknown,ZZ,101,3.5000\n"
        )
        call_command("load_fuel_stops", csv=str(csv_path), gazetteer=str(gaz))

        self.assertEqual(FuelStop.objects.count(), 2)
        cheap = FuelStop.objects.get(opis_id=1)
        self.assertAlmostEqual(cheap.latitude, 40.4167)
        self.assertAlmostEqual(cheap.longitude, -86.8753)
        self.assertEqual(cheap.geocode_source, "gazetteer")
        self.assertEqual(cheap.retail_price, Decimal("3.2500"))

        unknown = FuelStop.objects.get(opis_id=2)
        self.assertIsNone(unknown.latitude)
        self.assertEqual(unknown.geocode_source, "none")

    def test_dedupes_by_opis_id_keeping_min_price(self):
        gaz = self._gazetteer("Lafayette,IN,40.4167,-86.8753,67000\n")
        csv_path = self._fuel_csv(
            "1,Stop A,Addr,Lafayette,IN,100,4.0000\n"
            "1,Stop A Cheap,Addr,Lafayette,IN,100,3.0000\n"
        )
        call_command("load_fuel_stops", csv=str(csv_path), gazetteer=str(gaz))

        self.assertEqual(FuelStop.objects.count(), 1)
        stop = FuelStop.objects.get(opis_id=1)
        self.assertEqual(stop.retail_price, Decimal("3.0000"))
        # Name kept from first row when price is replaced
        self.assertEqual(stop.name, "Stop A")

    def test_skips_bad_rows(self):
        gaz = self._gazetteer("Lafayette,IN,40.4167,-86.8753,67000\n")
        csv_path = self._fuel_csv(
            "bad,No ID,Addr,Lafayette,IN,100,3.0000\n"
            "2,Bad Price,Addr,Lafayette,IN,100,not-a-price\n"
            "3,Bad State,Addr,Lafayette,IND,100,3.0000\n"
            "4,Good Stop,Addr,Lafayette,IN,100,3.1000\n"
        )
        call_command("load_fuel_stops", csv=str(csv_path), gazetteer=str(gaz))

        self.assertEqual(FuelStop.objects.count(), 1)
        self.assertEqual(FuelStop.objects.get().opis_id, 4)

    def test_gazetteer_join_case_insensitive(self):
        gaz = self._gazetteer("lafayette,IN,40.4167,-86.8753,67000\n")
        csv_path = self._fuel_csv(
            "1,Stop,Addr,LAFAYETTE,in,100,3.0000\n"
        )
        call_command("load_fuel_stops", csv=str(csv_path), gazetteer=str(gaz))

        stop = FuelStop.objects.get(opis_id=1)
        self.assertEqual(stop.geocode_source, "gazetteer")
        self.assertAlmostEqual(stop.latitude, 40.4167)

    @patch("routes.management.commands.load_fuel_stops.time.sleep")
    @patch("routes.management.commands.load_fuel_stops.geocode")
    def test_geocode_misses_uses_ors_and_cache(self, mock_geocode, _sleep):
        gaz = self._gazetteer()  # empty — everything is a miss
        csv_path = self._fuel_csv(
            "1,ORS Stop,Addr,Faketown,TX,100,3.2500\n"
        )
        mock_geocode.return_value = (32.0, -97.0)

        call_command(
            "load_fuel_stops",
            csv=str(csv_path),
            gazetteer=str(gaz),
            geocode_misses=True,
        )

        stop = FuelStop.objects.get(opis_id=1)
        self.assertEqual(stop.geocode_source, "ors")
        self.assertAlmostEqual(stop.latitude, 32.0)
        self.assertAlmostEqual(stop.longitude, -97.0)
        self.assertEqual(GeocodeCache.objects.count(), 1)
        self.assertEqual(mock_geocode.call_count, 1)

        # Second run should hit GeocodeCache — no new ORS call
        mock_geocode.reset_mock()
        call_command(
            "load_fuel_stops",
            csv=str(csv_path),
            gazetteer=str(gaz),
            geocode_misses=True,
        )
        mock_geocode.assert_not_called()
        stop = FuelStop.objects.get(opis_id=1)
        self.assertEqual(stop.geocode_source, "ors")

    @patch("routes.management.commands.load_fuel_stops.time.sleep")
    @patch("routes.management.commands.load_fuel_stops.geocode")
    def test_geocode_misses_none_saves_null_coords(self, mock_geocode, _sleep):
        gaz = self._gazetteer()
        csv_path = self._fuel_csv(
            "1,Miss Stop,Addr,Ghosttown,NV,100,3.0000\n"
        )
        mock_geocode.return_value = None

        call_command(
            "load_fuel_stops",
            csv=str(csv_path),
            gazetteer=str(gaz),
            geocode_misses=True,
        )

        stop = FuelStop.objects.get(opis_id=1)
        self.assertIsNone(stop.latitude)
        self.assertEqual(stop.geocode_source, "none")

    def test_missing_csv_raises_command_error(self):
        gaz = self._gazetteer("Lafayette,IN,40.4167,-86.8753,67000\n")
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "load_fuel_stops",
                csv="/nonexistent/fuel.csv",
                gazetteer=str(gaz),
            )
        self.assertIn("CSV not found", str(ctx.exception))

    def test_missing_gazetteer_raises_command_error(self):
        csv_path = self._fuel_csv(
            "1,Stop,Addr,Lafayette,IN,100,3.0000\n"
        )
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "load_fuel_stops",
                csv=str(csv_path),
                gazetteer="/nonexistent/gaz.csv",
            )
        self.assertIn("Gazetteer not found", str(ctx.exception))

    def test_replaces_existing_fuel_stops(self):
        FuelStop.objects.create(
            opis_id=999,
            name="Stale",
            city="Old",
            state="TX",
            retail_price=Decimal("9.9900"),
            geocode_source="none",
        )
        gaz = self._gazetteer("Lafayette,IN,40.4167,-86.8753,67000\n")
        csv_path = self._fuel_csv(
            "1,Fresh Stop,Addr,Lafayette,IN,100,3.0000\n"
        )
        call_command("load_fuel_stops", csv=str(csv_path), gazetteer=str(gaz))

        self.assertFalse(FuelStop.objects.filter(opis_id=999).exists())
        self.assertEqual(FuelStop.objects.count(), 1)
        self.assertEqual(FuelStop.objects.get().opis_id, 1)
