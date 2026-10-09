"""Unit tests for the math and parsing. Run: python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import ai  # noqa: E402


class MathTests(unittest.TestCase):
    def test_liters_from_pesos(self):
        r = core.reconcile(None, "64.99", "1000")
        self.assertEqual(r["liters"], "15.387")
        self.assertIn("Liters computed in code: amount ÷ price.", r["notes"])

    def test_amount_from_liters(self):
        self.assertEqual(core.reconcile("10", "57.40", None)["amount_pesos"], "574.00")

    def test_price_from_amount_and_liters(self):
        self.assertEqual(core.reconcile("34.843", None, "2000")["price_per_liter"], "57.40")

    def test_inconsistent_values_warn(self):
        r = core.reconcile("10", "64.99", "1000")
        self.assertTrue(any("Check values" in w for w in r["warnings"]))

    def test_peso_strings(self):
        self.assertEqual(core.dec("₱1,234.50"), core.Decimal("1234.50"))
        self.assertEqual(core.dec("P 2,000.00"), core.Decimal("2000.00"))
        self.assertEqual(core.dec("15.387 L"), core.Decimal("15.387"))
        self.assertIsNone(core.dec("n/a"))
        self.assertIsNone(core.dec(None))

    def test_cash_short(self):
        r = core.compute_cash("16350.00", "16000", "0", "0")
        self.assertEqual((r["expected"], r["diff"], r["diff_pct"], r["status"]), ("16350.00", "-350.00", "-2.14", "SHORT"))

    def test_cash_with_float_and_gcash(self):
        r = core.compute_cash("16350.00", "15850", "1000", "1500")
        self.assertEqual((r["expected"], r["diff"], r["status"]), ("15850.00", "0.00", "OK"))

    def test_cash_over_and_tolerance(self):
        self.assertEqual(core.compute_cash("1000", "1003", tolerance="5")["status"], "OK")
        r = core.compute_cash("1000", "1100", tolerance="5")
        self.assertEqual((r["status"], r["diff_pct"]), ("OVER", "10.00"))

    def test_fuel_names(self):
        self.assertEqual(core.normalize_fuel("PREMIUM 95"), "Premium")
        self.assertEqual(core.normalize_fuel("diesel"), "Diesel")
        self.assertEqual(core.normalize_fuel("Regular Gasoline"), "Unleaded")


class ParseTests(unittest.TestCase):
    def test_fenced_json_with_chatter(self):
        obj = ai.parse_json_text(ai.MOCK_EXTRACT)
        f = ai.fields_from_obj(obj)
        self.assertEqual(f["fuel_type"], "Premium")
        self.assertEqual(f["amount_pesos"], core.Decimal("1000.00"))

    def test_trailing_comma_and_single_quotes(self):
        self.assertEqual(ai.parse_json_text("x {'liters': 5.5,} y")["liters"], 5.5)

    def test_garbage(self):
        self.assertIsNone(ai.parse_json_text("I cannot read this image."))
        self.assertIsNone(ai.parse_json_text("{not json at all"))

    def test_language(self):
        self.assertEqual(ai.detect_lang("Magkano ang benta ng diesel ngayon?"), "tl")
        self.assertEqual(ai.detect_lang("How much diesel did we sell?"), "en")

    def test_unverified_numbers(self):
        ctx = "Diesel: 4 sales, 52.300 liters, ₱3,000.00."
        self.assertEqual(ai.unverified_numbers("Diesel ₱3,000.00, 52.3 liters", ctx), [])
        self.assertEqual(ai.unverified_numbers("Diesel ₱3,500.00", ctx), ["3,500.00"])


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        core.DB_PATH = os.path.join(self.tmp, "t.db")
        core.init_db()

    def test_save_and_summary_and_queue(self):
        sale, errors, _ = core.save_sale({"fuel_type": "Diesel", "price_per_liter": "57.40", "amount_pesos": "1000"})
        self.assertFalse(errors)
        self.assertEqual(sale["liters"], "17.422")
        self.assertEqual(sale["synced"], 0)
        core.save_sale({"fuel_type": "Diesel", "liters": "10", "price_per_liter": "57.40"})
        s = core.shift_summary()
        self.assertEqual((s["count"], s["total_amount"], s["total_liters"]), (2, "1574.00", "27.422"))
        self.assertEqual(core.queued_count(), 3)  # 1 shift + 2 sales
        core.void_sale(sale["id"])
        self.assertEqual(core.shift_summary()["total_amount"], "574.00")

    def test_reject_incomplete(self):
        _, errors, _ = core.save_sale({"fuel_type": "", "amount_pesos": "100"})
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
