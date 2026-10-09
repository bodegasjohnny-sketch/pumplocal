"""Oct 9 stress test (judge-style poking at the live app): each fix here has a test.

- posted fuel prices survive "Start new shift" (the price check had 'no posted price to compare')
- bad ids / garbage slips / negative cash inputs give a clear 400, never a 500
- a single sale of 1,000,000,000 L is refused
- a sale meter or receipt uploaded on the Pump tab is called out
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import ai  # noqa: E402
import core  # noqa: E402
import seed  # noqa: E402
import totalizer  # noqa: E402
from test_flow import Server, call  # noqa: E402


class CoreStressTests(unittest.TestCase):
    def setUp(self):
        self.old = core.DB_PATH
        self.tmp = tempfile.mkdtemp()
        core.DB_PATH = os.path.join(self.tmp, "t.db")
        with contextlib.redirect_stdout(io.StringIO()):
            seed.seed_demo_empty()
        self.pid = core.list_pumps()[0]["id"]

    def tearDown(self):
        core.DB_PATH = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def premium(self):
        return [g for g in core.pump_check()["groups"] if g["fuel_type"] == "Premium"][0]

    def test_posted_price_survives_new_shift(self):
        core.save_reading(self.pid, "open", "2559778", "32333.73", "photo")
        core.save_reading(self.pid, "close", "2595535", "32749.80", "photo")
        core.new_shift("Juan")
        self.assertEqual(core.posted_price("Premium"), core.Decimal("85.90"))
        core.save_reading(self.pid, "close", "2604127", "32849.80", "manual")  # 100 L, no sales yet this shift
        g = self.premium()
        self.assertEqual((g["price"]["ok"], g["price"]["basis"] if "basis" in g["price"] else "recorded sales"),
                         (True, "recorded sales"))
        self.assertIn("vs posted ₱85.90/L", g["price"]["text"])
        self.assertNotIn("no posted price", g["price"]["text"])
        # a credit sale on the new shift uses the posted price
        rec, errors, _ = core.save_sale({"payment": "credit", "customer": "Mang Jose", "fuel_type": "Premium",
                                         "amount_pesos": "500"})
        self.assertEqual((errors, rec["price_per_liter"]), ([], "85.90"))

    def test_price_change_becomes_posted_and_undo_restores(self):
        self.assertEqual(core.set_price_change("Premium", "85.90", "86.40"), [])
        self.assertEqual(core.posted_price("Premium"), core.Decimal("86.40"))
        self.assertEqual(core.set_price_change("Premium", "", ""), [])
        self.assertEqual(core.posted_price("Premium"), core.Decimal("85.90"))

    def test_bad_ids_and_inputs(self):
        self.assertIsNone(core.dec("1e9"))
        self.assertEqual(core.dec("₱1,000.50"), core.Decimal("1000.50"))
        self.assertEqual(core.void_sale("abc"), ["Choose a sale to void."])
        self.assertEqual(core.void_sale(None), ["Choose a sale to void."])
        self.assertEqual(core.void_expense("x"), ["Choose an expense to void."])
        rec, errors, _ = core.save_sale({"fuel_type": "Diesel", "liters": "1000000000", "price_per_liter": "94.50"})
        self.assertIsNone(rec)
        self.assertIn("too large", errors[0])
        for args in (("-5",), ("abc",), ("100", "-1000"), ("100", "1000", "zz"), ("1e5",)):
            self.assertTrue(core.cash_input_errors(*args), args)
        self.assertEqual(core.cash_input_errors("39,169.55", "1000", "800"), [])
        self.assertEqual(core.cash_input_errors("₱39169.55", 1000, 800.0), [])
        out = core.slip_apply({"expenses": [{"amount_pesos": "-5"}, "zz"], "credits": "zz"})
        self.assertEqual((out["saved_expenses"], out["saved_credits"]), ([], []))
        self.assertEqual(core.slip_compare("zz")["expenses"], [])

    def test_sale_meter_on_pump_tab_is_called_out(self):
        with open(os.path.join(HERE, "ocr_fixtures", "meter_diesel.json")) as f:
            lines = json.load(f)["lines"]
        raw = "\n".join(ln["text"] for ln in lines)
        r = ai._totalizer_result(totalizer.parse(lines), raw, 0.1, "vision", "test")
        self.assertTrue(r["not_totalizer"])
        self.assertIn("not a pump totalizer", r["message"])
        with open(os.path.join(HERE, "ocr_fixtures", "vision_mac_premium3_closing_shift_pesos.json")) as f:
            lines = json.load(f)["lines"]
        r = ai._totalizer_result(totalizer.parse(lines), "\n".join(ln["text"] for ln in lines), 0.1, "vision", "t")
        self.assertFalse(r["not_totalizer"])


class ApiStressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server(MOCK_AI="1")
        cls.b = cls.s.base

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_no_500_on_garbage(self):
        for path, body in [("/api/sales/void", {"id": "abc"}), ("/api/sales/void", {}),
                           ("/api/expenses/void", {"id": "x"}), ("/api/slip/apply", {"slip": "zz"}),
                           ("/api/slip/apply", {"slip": {"credits": "zz"}}), ("/api/slip/compare", {"slip": [1]}),
                           ("/api/cashcheck", {"declared": "-5", "opening_float": "1000"}),
                           ("/api/cashcheck", {"declared": "100", "opening_float": "1000", "noncash": "zz"}),
                           ("/api/sales", {"fuel_type": "Diesel", "liters": "1000000000", "price_per_liter": "94.50"})]:
            code, j = call(self.b, path, body)
            self.assertIn(code, (200, 400), (path, body, j))
            self.assertNotIn("Server error", json.dumps(j), (path, body))

    def test_valid_cash_check_still_works(self):
        code, j = call(self.b, "/api/cashcheck", {"declared": "1000", "opening_float": "1000", "noncash": "0"})
        self.assertEqual(code, 200, j)


if __name__ == "__main__":
    unittest.main()
