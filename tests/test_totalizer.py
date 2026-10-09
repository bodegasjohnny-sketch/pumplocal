"""Pump totalizer tests: OCR text -> reading (totalizer.py) and the pump-vs-sales gap math (core.py).

The real photo samples/real_totalizer_diesel2.png (from the team's own station) shows an LCD menu
"2.Money All" / "Volume 775397" / "Cancel" "Ok", a sticker "DIESEL 2" and partly visible side buttons
"Am" / "Qua". tests/ocr_fixtures/real_totalizer_diesel2.json is that text in Apple Vision's line format.
"""
import json
import os
import tempfile
import unittest
from decimal import Decimal

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_fixtures")
REAL_LINES = ["2.Money All", "Volume 775397", "Cancel", "Ok", "DIESEL 2", "Am", "Qua"]

import core  # noqa: E402
import totalizer  # noqa: E402


class ParseRealPhotoTests(unittest.TestCase):
    def check_real(self, lines, confidence="labelled"):
        r = totalizer.parse(lines)
        self.assertEqual(r["reading"], "775397", r)
        self.assertEqual(r["pump_name"], "Diesel 2")
        self.assertEqual(r["fuel_type"].upper(), "DIESEL")
        self.assertEqual(r["confidence"], confidence)
        self.assertEqual(r["unit_hint"], "L")  # the line says "Volume" (a hint only; unit is set per pump)
        return r

    def test_real_photo_plain_lines(self):
        self.check_real(REAL_LINES)

    def test_real_photo_vision_fixture_with_boxes(self):
        with open(os.path.join(FIXTURES, "real_totalizer_diesel2.json")) as f:
            self.check_real(json.load(f)["lines"])

    def test_real_photo_ocr_variants(self):
        self.check_real(["2. Money All", "Volume", "775397", "Cancel", "Ok", "DIESEL 2", "Am", "Qua"],
                        confidence="nearby")
        self.check_real(["2.Money All", "Vo1ume 775 397", "Cancel Ok", "DIESEL 2"])
        self.check_real(["Volume 775,397", "2.Money All", "Qua", "DIESEL 2"])

    def test_menu_and_label_numbers_ignored(self):
        r = totalizer.parse(["2.Money All", "DIESEL 2"])
        self.assertIsNone(r["reading"])  # "2." and "DIESEL 2" are never the reading
        self.assertEqual(r["pump_name"], "Diesel 2")

    def test_largest_labelled_number_wins(self):
        r = totalizer.parse(["Total 1", "Amount 4521337.50", "Volume 78765.432", "PREMIUM 1", "PUMP 3"])
        self.assertEqual((r["reading"], r["pump_name"], r["fuel_type"]), ("4521337.50", "Premium 1", "Premium"))
        self.assertEqual(r["unit_hint"], "PHP")

    def test_pump_labels(self):
        self.assertEqual(totalizer.pump_label(["UNLEADED"]), ("Unleaded", "Unleaded"))
        self.assertEqual(totalizer.pump_label(["PREMIUM 1"]), ("Premium 1", "Premium"))
        self.assertEqual(totalizer.pump_label(["Pump 3", "Diesel"]), ("Diesel 3", "Diesel"))
        self.assertEqual(totalizer.pump_label(["Volume 1"]), ("", ""))

    def test_unlabelled_guess_and_dates(self):
        r = totalizer.parse(["2026-10-09 10:42", "12 0034561", "7"])
        self.assertEqual((r["reading"], r["confidence"]), ("0034561", "guess"))  # digits as shown
        self.assertTrue(r["notes"])
        self.assertIsNone(totalizer.parse([])["reading"])


class ReadingMathTests(unittest.TestCase):
    def test_decimals(self):
        self.assertEqual(core.reading_value("775397", 0), Decimal("775397"))
        self.assertEqual(core.reading_value("775397", 2), Decimal("7753.97"))
        self.assertEqual(core.reading_value("775397", 3), Decimal("775.397"))
        self.assertEqual(core.reading_value("775,397", 2), Decimal("7753.97"))
        self.assertEqual(core.reading_value("7753.97", 3), Decimal("7753.97"))  # typed point wins
        self.assertIsNone(core.reading_value("abc"))
        self.assertIsNone(core.reading_value("-5"))

    def test_dispensed_with_decimals(self):
        self.assertEqual(core.dispensed("775397", "775809", 0)["value"], Decimal("412"))
        self.assertEqual(core.dispensed("775397", "775809", 2)["value"], Decimal("4.12"))
        self.assertEqual(core.dispensed("775397", "775809", 3)["value"], Decimal("0.412"))

    def test_closing_below_opening_is_error(self):
        d = core.dispensed("775397", "775000", 0)
        self.assertFalse(d["ok"])
        self.assertIsNone(d["value"])
        self.assertIn("lower than opening", d["error"])
        self.assertTrue(core.dispensed("775397", None)["incomplete"])

    def test_gap_math_and_tolerance(self):
        g = core.gap_check("181", "174.216", "L", "0.5")
        self.assertEqual((g["dispensed"], g["recorded"], g["gap"], g["gap_pct"], g["status"], g["flag"]),
                         ("181.000", "174.216", "6.784", "3.75", "UNACCOUNTED", True))
        self.assertEqual(core.gap_headline(g), "Pump says 181 L dispensed; recorded sales 174.216 L; "
                                               "6.784 L (3.75%) unaccounted.")
        self.assertIn("hindi naitala", core.gap_headline(g, "tl"))
        self.assertEqual(core.gap_check("181", "174.216", "L", "5")["status"], "OK")  # 3.75% < 5%
        self.assertEqual(core.gap_check("200", "199", "L", "0.5")["status"], "OK")  # exactly 0.5%
        self.assertEqual(core.gap_check("200", "198.99", "L", "0.5")["status"], "UNACCOUNTED")
        o = core.gap_check("100", "110", "L", "1")
        self.assertEqual((o["gap"], o["status"]), ("-10.000", "OVER_RECORDED"))
        self.assertIn("more recorded", core.gap_headline(o))
        p = core.gap_check("10000", "9800", "PHP", "1")
        self.assertEqual((p["gap"], p["gap_pct"], p["status"]), ("200.00", "2.00", "UNACCOUNTED"))
        self.assertIn("₱200.00 (2.00%) unaccounted", core.gap_headline(p))
        self.assertIsNone(core.gap_check("0", "0", "L", "0.5")["gap_pct"])


class PumpStorageTests(unittest.TestCase):
    def setUp(self):
        self.old = core.DB_PATH
        core.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        core.init_db()

    def tearDown(self):
        core.DB_PATH = self.old

    def test_pump_validation(self):
        p, e = core.save_pump({"name": "DIESEL 2", "fuel_type": "diesel", "unit": "Liters", "decimals": "2"})
        self.assertEqual((p["name"], p["fuel_type"], p["unit"], p["decimals"], e), ("Diesel 2", "Diesel", "L", 2, []))
        self.assertTrue(core.save_pump({"name": "", "fuel_type": "Diesel"})[1])
        self.assertTrue(core.save_pump({"name": "X", "fuel_type": "Diesel", "unit": "gal"})[1])
        self.assertTrue(core.save_pump({"name": "X", "fuel_type": "Diesel", "decimals": 5})[1])
        p2, _ = core.save_pump({"name": "diesel 2", "fuel_type": "Diesel", "unit": "PHP"})
        self.assertEqual((p2["id"], p2["unit"]), (p["id"], "PHP"))  # same name updates

    def test_shift_check_two_pumps_and_carry_over(self):
        d1, _ = core.save_pump({"name": "Diesel 1", "fuel_type": "Diesel"})
        d2, _ = core.save_pump({"name": "Diesel 2", "fuel_type": "Diesel"})
        core.save_pump({"name": "Premium 1", "fuel_type": "Premium"})  # not read: left out
        core.save_sale({"fuel_type": "Diesel", "liters": "20", "price_per_liter": "57.40"})
        core.save_reading(d1["id"], "open", "1000")
        core.save_reading(d2["id"], "opening", "500")
        c = core.pump_check(tolerance_pct="1")
        self.assertEqual(c["groups"][0]["status"], "INCOMPLETE")
        core.save_reading(d1["id"], "close", "1012")
        core.save_reading(d2["id"], "closing", "510")
        c = core.pump_check(tolerance_pct="1")
        g = c["groups"][0]
        self.assertEqual(len(c["groups"]), 1)
        self.assertEqual((g["dispensed"], g["recorded"], g["gap"], g["status"]), ("22.000", "20.000", "2.000",
                                                                                  "UNACCOUNTED"))
        self.assertEqual([p["status"] for p in c["pumps"]], ["OK", "OK", "NONE"])
        rec, errs, warns = core.save_reading(d2["id"], "close", "499")
        self.assertEqual(errs, [])
        self.assertIn("lower than opening", warns[0])
        self.assertEqual(core.pump_check()["groups"][0]["status"], "ERROR")
        self.assertTrue(core.save_reading(d2["id"], "close", "abc")[1])
        self.assertTrue(core.save_reading(d2["id"], "middle", "5")[1])
        core.new_shift("Juan")
        c = core.pump_check()
        self.assertEqual([(p["name"], p["opening"], p["opening_source"]) for p in c["pumps"][:2]],
                         [("Diesel 1", "1012", "carried"), ("Diesel 2", "499", "carried")])

    def test_tolerance_setting(self):
        self.assertEqual(core.pump_tolerance(), Decimal("0.5"))
        core.set_setting("pump_tolerance_pct", "2")
        self.assertEqual(core.pump_tolerance(), Decimal("2"))


if __name__ == "__main__":
    unittest.main()
