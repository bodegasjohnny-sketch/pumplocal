"""Pump totalizer tests: OCR text -> peso/liter readings (totalizer.py) and the pump-vs-sales math (core.py).

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
    """The real photo shows ONE counter under the menu title "2.Money All": the PESO totalizer (confirmed by the
    station owner), even though its line reads "Volume". The liter totalizer is the other counter on that pump."""

    def check_real(self, lines, confidence="labelled"):
        r = totalizer.parse(lines)
        self.assertEqual((r["amount"], r["volume"]), ("775397", None), r)
        self.assertEqual(r["pump_name"], "Diesel 2")
        self.assertEqual(r["fuel_type"].upper(), "DIESEL")
        self.assertEqual(r["confidence"], confidence)
        self.assertEqual(r["screen"], "amount")
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

    def test_both_counters_on_one_screen(self):
        r = totalizer.parse(["Money 775397", "Volume 13508", "DIESEL 2"])
        self.assertEqual((r["amount"], r["volume"], r["unassigned"]), ("775397", "13508", None))
        r = totalizer.parse(["Amount 4521337", "Liters 78765", "PREMIUM 1", "PUMP 3"])
        self.assertEqual((r["amount"], r["volume"], r["pump_name"]), ("4521337", "78765", "Premium 1"))

    def test_volume_screen(self):
        r = totalizer.parse(["1.Volume All", "Volume 13508", "DIESEL 2"])
        self.assertEqual((r["amount"], r["volume"], r["screen"]), (None, "13508", "volume"))

    def test_menu_and_label_numbers_ignored(self):
        r = totalizer.parse(["2.Money All", "DIESEL 2"])
        self.assertIsNone(r["reading"])  # "2." and "DIESEL 2" are never the reading
        self.assertEqual(r["pump_name"], "Diesel 2")

    def test_unknown_counter_is_unassigned(self):
        r = totalizer.parse(["Total 99999"])
        self.assertEqual((r["amount"], r["volume"], r["unassigned"]), (None, None, "99999"))
        self.assertTrue(r["notes"])

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


class PriceCheckTests(unittest.TestCase):
    def test_price_check_tolerance_and_price_change(self):
        ok = core.price_check("10390", "181", [Decimal("57.40")])
        self.assertEqual((ok["implied"], ok["ok"], ok["basis"]), ("57.40", True, "recorded sales"))  # 57.4033
        self.assertTrue(core.price_check("5745", "100", [Decimal("57.40")])["ok"])  # +0.05 passes
        warn = core.price_check("5746", "100", [Decimal("57.40")])  # +0.06 fails
        self.assertFalse(warn["ok"])
        self.assertIn("not a missing-fuel flag", warn["text"])
        # price went from 57.40 to 58.40 mid-shift: anything in between passes
        pc = {"old": "57.40", "new": "58.40"}
        self.assertTrue(core.price_check("5790", "100", [Decimal("57.40")], pc)["ok"])
        self.assertTrue(core.price_check("5845", "100", [], pc)["ok"])
        self.assertFalse(core.price_check("5850", "100", [], pc)["ok"])
        self.assertIsNone(core.price_check("5790", "100", [])["ok"])  # nothing to compare
        self.assertIsNone(core.price_check("10", "0", []))


class PumpStorageTests(unittest.TestCase):
    def setUp(self):
        self.old = core.DB_PATH
        core.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        core.init_db()

    def tearDown(self):
        core.DB_PATH = self.old

    def test_pump_validation(self):
        p, e = core.save_pump({"name": "DIESEL 2", "fuel_type": "diesel"})
        self.assertEqual((p["name"], p["fuel_type"], p["amount_decimals"], p["volume_decimals"], e),
                         ("Diesel 2", "Diesel", 0, 0, []))  # whole numbers as displayed by default
        self.assertTrue(core.save_pump({"name": "", "fuel_type": "Diesel"})[1])
        self.assertTrue(core.save_pump({"name": "X", "fuel_type": "Diesel", "volume_decimals": 5})[1])
        p2, _ = core.save_pump({"name": "diesel 2", "fuel_type": "Diesel", "volume_decimals": 2})
        self.assertEqual((p2["id"], p2["volume_decimals"], p2["amount_decimals"]), (p["id"], 2, 0))

    def test_both_counters_gap_price_and_carry_over(self):
        d1, _ = core.save_pump({"name": "Diesel 1", "fuel_type": "Diesel"})
        d2, _ = core.save_pump({"name": "Diesel 2", "fuel_type": "Diesel"})
        core.save_pump({"name": "Premium 1", "fuel_type": "Premium"})  # not read: left out
        core.save_sale({"fuel_type": "Diesel", "liters": "20", "price_per_liter": "57.40"})  # P1,148.00
        core.save_reading(d1["id"], "open", amount="500000", volume="1000")
        core.save_reading(d2["id"], "opening", amount="200000")  # liters read later
        core.save_reading(d2["id"], "opening", volume="500")
        self.assertEqual(core.pump_check(tolerance_pct="1")["groups"][0]["status"], "INCOMPLETE")
        core.save_reading(d1["id"], "close", amount="500689", volume="1012")  # 12 L, P689
        core.save_reading(d2["id"], "closing", amount="200574", volume="510")  # 10 L, P574
        c = core.pump_check(tolerance_pct="1")
        g = c["groups"][0]
        self.assertEqual(len(c["groups"]), 1)
        self.assertEqual((g["liters"]["dispensed"], g["liters"]["recorded"], g["liters"]["gap"]),
                         ("22.000", "20.000", "2.000"))
        self.assertEqual((g["pesos"]["dispensed"], g["pesos"]["recorded"], g["pesos"]["gap"], g["pesos"]["gap_pct"]),
                         ("1263.00", "1148.00", "115.00", "9.11"))
        self.assertEqual((g["status"], g["flag"]), ("UNACCOUNTED", True))
        self.assertEqual((g["price"]["implied"], g["price"]["ok"]), ("57.41", True))  # 1263 / 22 = 57.409
        self.assertEqual([p["status"] for p in c["pumps"]], ["OK", "OK", "NONE"])
        rec, errs, warns = core.save_reading(d2["id"], "close", volume="499")
        self.assertEqual(errs, [])
        self.assertIn("lower than opening", warns[0])
        g = core.pump_check()["groups"][0]
        self.assertEqual((g["status"], g["flag"]), ("ERROR", True))
        self.assertTrue(core.save_reading(d2["id"], "close", amount="abc")[1])
        self.assertTrue(core.save_reading(d2["id"], "close")[1])
        self.assertTrue(core.save_reading(d2["id"], "middle", amount="5")[1])
        core.new_shift("Juan")
        c = core.pump_check()
        self.assertEqual([(p["name"], p["opening_amount"], p["opening_volume"], p["opening_amount_source"])
                          for p in c["pumps"][:2]],
                         [("Diesel 1", "500689", "1012", "carried"), ("Diesel 2", "200574", "499", "carried")])

    def test_only_peso_counter_and_price_warning(self):
        d, _ = core.save_pump({"name": "Diesel 2", "fuel_type": "Diesel"})
        core.save_sale({"fuel_type": "Diesel", "liters": "100", "price_per_liter": "57.40"})  # P5,740
        core.save_reading(d["id"], "open", amount="775397")
        core.save_reading(d["id"], "close", amount="781137")  # P5,740
        g = core.pump_check()["groups"][0]
        self.assertEqual((g["status"], g["liters"], g["pesos"]["status"], g["price"]), ("OK", None, "OK", None))
        core.save_reading(d["id"], "open", volume="13508")
        core.save_reading(d["id"], "close", volume="13606")  # 98 L -> implied 58.57/L: price warning only
        g = core.pump_check(tolerance_pct="5")["groups"][0]
        self.assertEqual((g["status"], g["flag"]), ("OK", False))  # within 5%: no theft flag
        self.assertEqual((g["price"]["implied"], g["price"]["ok"]), ("58.57", False))
        self.assertEqual(len(core.pump_check(tolerance_pct="5")["price_warnings"]), 1)
        self.assertEqual(core.set_price_change("Diesel", "57.40", "58.60"), [])
        self.assertTrue(core.pump_check()["groups"][0]["price"]["ok"])
        self.assertTrue(core.set_price_change("Diesel", "57.40", ""))
        self.assertEqual(core.set_price_change("Diesel", "", ""), [])
        self.assertFalse(core.pump_check()["groups"][0]["price"]["ok"])

    def test_v1_single_counter_readings_migrate(self):
        import sqlite3
        conn = sqlite3.connect(core.DB_PATH)
        conn.execute("INSERT INTO shifts (opened_at) VALUES ('2026-10-09 06:00:00')")
        conn.execute("INSERT INTO pumps (name, fuel_type, unit, decimals, created_at) VALUES ('Diesel 2', 'Diesel', 'PHP', 0, 'x')")
        conn.execute("INSERT INTO pump_readings (shift_id, pump_id, kind, reading, source, created_at) "
                     "VALUES (1, 1, 'open', '775397', 'photo', 'x')")
        conn.execute("INSERT INTO pump_readings (shift_id, pump_id, kind, reading, source, created_at) "
                     "VALUES (1, 1, 'close', '775400', 'seed', 'x')")  # old demo rows are re-seeded, not migrated
        conn.commit()
        conn.close()
        core.init_db()
        r = core.rows("SELECT * FROM totalizer_readings")
        self.assertEqual([(x["kind"], x["amount"], x["volume"]) for x in r], [("open", "775397", None)])

    def test_tolerance_setting(self):
        self.assertEqual(core.pump_tolerance(), Decimal("0.5"))
        core.set_setting("pump_tolerance_pct", "2")
        self.assertEqual(core.pump_tolerance(), Decimal("2"))


if __name__ == "__main__":
    unittest.main()
