"""Expenses, discounts, credit (utang) sales and the cash denomination count -- all feeding the Cash Check."""
import base64
import json
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal

import core
import meterparse
from tests.test_flow import FAKE_OCR, ROOT, Server, call

RECEIPT = os.path.join(ROOT, "samples", "receipt_diesel.png")


def img():
    with open(RECEIPT, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


class FormulaTests(unittest.TestCase):
    def test_expected_cash_formula(self):
        # 1000 float + 17850 gross - 50 discounts - 3000 credit - 500 expenses - 500 GCash = 14800
        r = core.compute_cash("17850", "14790", "1000", "500", discounts="50", credit="3000", expenses="500")
        self.assertEqual((r["expected"], r["diff"], r["status"]), ("14800.00", "-10.00", "SHORT"))
        self.assertEqual((r["gross_sales"], r["discounts"], r["credit_sales"], r["expenses"]),
                         ("17850.00", "50.00", "3000.00", "500.00"))
        self.assertEqual(core.compute_cash("100", "100")["status"], "OK")  # old call style still works

    def test_cash_count(self):
        c, e = core.count_cash({"1000": "3", "500": 2, "200": "", "100": "4", "50": "1", "20": "7", "coins": "12.50"})
        self.assertEqual((c["total"], e), ("4602.50", []))  # 3000+1000+400+50+140+12.50
        self.assertEqual(c["lines"][0], {"denom": "1000", "count": 3, "subtotal": "3000.00"})
        self.assertTrue(core.count_cash({"1000": "-1"})[1])
        self.assertTrue(core.count_cash({"500": "2.5"})[1])
        self.assertTrue(core.count_cash({"coins": "abc"})[1])
        self.assertEqual(core.count_cash({})[0]["total"], "0.00")

    def test_receipt_total_parse(self):
        lines = [{"text": "ACE HARDWARE", "x": .1, "y": .05, "w": .5, "h": .04},
                 {"text": "SUBTOTAL", "x": .1, "y": .3, "w": .3, "h": .03}, {"text": "350.00", "x": .6, "y": .3, "w": .2, "h": .03},
                 {"text": "TOTAL", "x": .1, "y": .35, "w": .2, "h": .03}, {"text": "P 350.00", "x": .6, "y": .35, "w": .2, "h": .03},
                 {"text": "CASH", "x": .1, "y": .4, "w": .2, "h": .03}, {"text": "500.00", "x": .6, "y": .4, "w": .2, "h": .03},
                 {"text": "CHANGE 150.00", "x": .1, "y": .45, "w": .6, "h": .03}]
        self.assertEqual(meterparse.receipt_total(lines), {"amount": "350.00", "store": "ACE HARDWARE", "label": "TOTAL"})
        self.assertEqual(meterparse.receipt_total([{"text": "GRAND TOTAL 1,234.50"}])["amount"], "1234.50")
        self.assertIsNone(meterparse.receipt_total([{"text": "CASH 500.00"}, {"text": "CHANGE 20"}])["amount"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.old = core.DB_PATH
        core.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        core.init_db()

    def tearDown(self):
        core.DB_PATH = self.old

    def test_discount_validation_and_summary(self):
        sale, e, _ = core.save_sale({"fuel_type": "Diesel", "amount_pesos": "1000", "price_per_liter": "57.40",
                                     "discount_pesos": "20", "discount_reason": "PWD"})
        self.assertEqual((sale["discount_pesos"], sale["discount_reason"], sale["payment"], e), ("20.00", "pwd", "cash", []))
        self.assertTrue(core.save_sale({"fuel_type": "Diesel", "amount_pesos": "100", "price_per_liter": "57.40",
                                        "discount_pesos": "20"})[1])  # reason required
        self.assertTrue(core.save_sale({"fuel_type": "Diesel", "amount_pesos": "100", "price_per_liter": "57.40",
                                        "discount_pesos": "200", "discount_reason": "suki"})[1])  # > amount
        self.assertTrue(core.save_sale({"fuel_type": "Diesel", "amount_pesos": "100", "price_per_liter": "57.40",
                                        "discount_pesos": "-5", "discount_reason": "suki"})[1])
        s = core.shift_summary()
        self.assertEqual((s["discounts_total"], s["discounts_by_reason"], s["total_amount"]),
                         ("20.00", {"pwd": "20.00"}, "1000.00"))

    def test_credit_sale_uses_last_price_and_net_of_discount(self):
        self.assertTrue(core.save_sale({"fuel_type": "Diesel", "amount_pesos": "500", "payment": "credit",
                                        "customer": "Ka Ising"})[1])  # no price known yet
        core.save_sale({"fuel_type": "Diesel", "amount_pesos": "100", "price_per_liter": "57.40"})
        sale, e, _ = core.save_sale({"fuel_type": "Diesel", "amount_pesos": "1148", "payment": "utang",
                                     "customer": "Ka Ising", "discount_pesos": "48", "discount_reason": "suki"})
        self.assertEqual((sale["payment"], sale["customer"], sale["liters"], e), ("credit", "Ka Ising", "20.000", []))
        self.assertTrue(core.save_sale({"fuel_type": "Diesel", "amount_pesos": "100", "payment": "credit"})[1])
        s = core.shift_summary()
        self.assertEqual((s["credit_total"], s["discounts_total"], s["total_amount"]), ("1100.00", "48.00", "1248.00"))
        r = core.compute_cash(s["total_amount"], "100", discounts=s["discounts_total"], credit=s["credit_total"])
        self.assertEqual(r["expected"], "100.00")  # 1248 - 48 - 1100: the discount is not subtracted twice

    def test_expenses(self):
        e, err = core.save_expense({"amount_pesos": "₱150", "description": " Ice  water "})
        self.assertEqual((e["amount_pesos"], e["description"], err), ("150.00", "Ice water", []))
        self.assertTrue(core.save_expense({"amount_pesos": "0", "description": "x"})[1])
        self.assertTrue(core.save_expense({"amount_pesos": "10", "description": ""})[1])
        core.save_expense({"amount": "50", "description": "Rags"})
        self.assertEqual(core.shift_summary()["expenses_total"], "200.00")
        core.void_expense(e["id"])
        self.assertEqual(core.shift_summary()["expenses_total"], "50.00")
        self.assertIn("expenses", core.SYNC_TABLES)

    def test_old_database_is_migrated(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("CREATE TABLE sales (id INTEGER PRIMARY KEY, shift_id INTEGER, created_at TEXT, fuel_type TEXT,"
                           " liters TEXT, price_per_liter TEXT, amount_pesos TEXT, source TEXT, note TEXT,"
                           " voided INTEGER DEFAULT 0, synced INTEGER DEFAULT 0);"
                           "INSERT INTO sales VALUES (1, 1, 'x', 'Diesel', '1', '57.40', '57.40', 'seed', '', 0, 0);")
        conn.close()
        core.DB_PATH = path
        core.init_db()
        r = core.rows("SELECT discount_pesos, payment FROM sales")[0]
        self.assertEqual((r["discount_pesos"], r["payment"]), ("0.00", "cash"))


class CashExtrasFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server(MOCK_AI="1")
        cls.b = cls.s.base

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_1_seed(self):
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"], sh["discounts_total"], sh["credit_total"], sh["expenses_total"]),
                         (15, "16350.00", "50.00", "3000.00", "500.00"))
        self.assertEqual(sh["credit_sales"][0]["customer"], "Mang Ben (trucking)")
        self.assertEqual([e["description"] for e in sh["expenses"]], ["Ice and drinking water", "Nozzle O-ring (hardware)"])

    def test_2_expense_endpoints_and_receipt_photo(self):
        code, ex = call(self.b, "/api/expense/extract", {"image": img()})
        self.assertEqual((code, ex["ok"], ex["amount_pesos"], ex["description"]), (200, True, "350.00", "Demo Hardware"))
        self.assertEqual(call(self.b, "/api/expense/extract", {"image": ""})[0], 400)
        code, j = call(self.b, "/api/expenses", {"amount_pesos": "100", "description": "Load for GCash phone",
                                                 "source": "photo"})
        self.assertEqual((code, j["expense"]["source"]), (200, "photo"))
        self.assertEqual(call(self.b, "/api/expenses", {"amount_pesos": "", "description": "x"})[0], 400)
        call(self.b, "/api/expenses/void", {"id": j["expense"]["id"]})
        self.assertEqual(call(self.b, "/api/shift")[1]["expenses_total"], "500.00")

    def test_3_discount_and_credit_endpoints(self):
        code, j = call(self.b, "/api/sales", {"fuel_type": "Premium", "amount_pesos": "1000", "price_per_liter": "64.99",
                                              "discount_pesos": "30", "discount_reason": "suki"})
        self.assertEqual((code, j["sale"]["discount_pesos"]), (200, "30.00"))
        self.assertEqual(call(self.b, "/api/sales", {"fuel_type": "Premium", "amount_pesos": "1000",
                                                     "price_per_liter": "64.99", "discount_pesos": "30"})[0], 400)
        code, j = call(self.b, "/api/credit", {"customer": "Aling Nena", "fuel_type": "Unleaded", "amount_pesos": "612.50"})
        self.assertEqual((code, j["sale"]["payment"], j["sale"]["liters"]), (200, "credit", "10.000"))  # 612.50 / 61.25
        self.assertEqual(call(self.b, "/api/credit", {"fuel_type": "Unleaded", "amount_pesos": "100"})[0], 400)

    def test_4_cash_check_with_count(self):
        # gross 16350 + 1000 + 612.50 = 17962.50; discounts 80; credit 3612.50; expenses 500 -> expected 13770
        code, c = call(self.b, "/api/cash/count", {"counts": {"1000": 13, "500": 1, "200": 1, "20": 3, "coins": "10"}})
        self.assertEqual(c["total"], "13770.00")
        self.assertEqual(call(self.b, "/api/cash/count", {"counts": {"100": "x"}})[0], 400)
        code, c = call(self.b, "/api/cashcheck", {"counts": {"1000": 13, "500": 1, "200": 1, "20": 3, "coins": "10"}})
        r = c["result"]
        self.assertEqual((r["gross_sales"], r["discounts"], r["credit_sales"], r["expenses"], r["expected"], r["declared"],
                          r["status"]), ("17962.50", "80.00", "3612.50", "500.00", "13770.00", "13770.00", "OK"))
        self.assertEqual(r["count"]["total"], "13770.00")
        self.assertEqual(json.loads(c["record"]["cash_count"])["total"], "13770.00")
        code, c = call(self.b, "/api/cashcheck", {"declared": "13000", "counts": {"1000": 13}})
        self.assertEqual(c["result"]["declared"], "13000.00")  # a typed amount wins over the count
        self.assertEqual(call(self.b, "/api/cashcheck", {})[0], 400)

    def test_5_ask_context(self):
        code, a = call(self.b, "/api/ask", {"question": "Sino ang may utang?"})
        self.assertIn("Mang Ben (trucking)", a["answer"])
        self.assertIn("Utang", a["answer"])
        self.assertEqual(a["unverified_numbers"], [])
        self.assertIn("Expenses (petty cash out): ₱500.00", a["context"])
        self.assertIn("- discounts ₱80.00 - credit ₱3,612.50 - expenses ₱500.00", a["context"])
        code, a = call(self.b, "/api/ask", {"question": "How much were expenses this shift?"})
        self.assertIn("Ice and drinking water ₱150.00", a["answer"])
        code, a = call(self.b, "/api/ask", {"question": "Magkano ang diskwento?"})
        self.assertIn("₱80.00", a["answer"])
        self.assertEqual(a["unverified_numbers"], [])


class ExpenseOcrTest(unittest.TestCase):
    def test_vision_reads_total(self):
        s = Server(MOCK_AI="1", OCR_BIN=FAKE_OCR,
                   FAKE_OCR_JSON=os.path.join(ROOT, "tests", "ocr_fixtures", "receipt_diesel.json"))
        try:
            code, ex = call(s.base, "/api/expense/extract", {"image": img()})
            self.assertEqual((ex["reader"], ex["amount_pesos"], ex["description"]), ("vision", "2000.00", "DEMO FUEL STATION"))
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
