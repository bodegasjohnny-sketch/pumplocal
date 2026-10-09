"""Guard against reading a whole report/sheet as one sale (meterparse.report_reason).

closing_sheet_photo.json / closing_sheet_clean.json are the OCR text of samples/closing_sheet_photo.jpg and
samples/closing_sheet_clean.png (synthetic forms), recorded with tesseract on Linux as a stand-in for Apple Vision,
in the same line format (text + normalized top-left boxes).
"""
import json
import os
import unittest

import meterparse
import totalizer

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_fixtures")


def lines(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)["lines"]


class ReportGuardTests(unittest.TestCase):
    def test_closing_sheet_photo_is_a_report(self):
        why = meterparse.report_reason(lines("closing_sheet_photo.json"))
        self.assertIsNotNone(why)
        self.assertIn("PETTY CASH", why)

    def test_closing_sheet_clean_is_a_report(self):
        self.assertIsNotNone(meterparse.report_reason(lines("closing_sheet_clean.json")))

    def test_sheet_without_keywords_is_caught_by_number_count(self):
        ls = [ln for ln in lines("closing_sheet_photo.json") if not meterparse.REPORT_WORDS.search(ln["text"])]
        why = meterparse.report_reason(ls)
        self.assertIsNotNone(why)
        self.assertIn("numbers found", why)

    def test_existing_samples_pass_the_guard_and_parse_unchanged(self):
        expected = {
            "meter_premium.json": {"fuel_type": "Premium", "liters": "12.000", "price_per_liter": "85.90",
                                   "amount_pesos": "1030.80"},
            "meter_unleaded.json": {"fuel_type": "Unleaded", "liters": "6.000", "price_per_liter": "85.40",
                                    "amount_pesos": "512.40"},
            "receipt_diesel.json": {"fuel_type": "Diesel", "liters": "21.164", "price_per_liter": "94.50",
                                    "amount_pesos": "2000.00"},
        }
        for name, fields in expected.items():
            ls = lines(name)
            self.assertIsNone(meterparse.report_reason(ls), name)
            p = meterparse.parse(ls)
            self.assertEqual((p["fields"], p["consistent"]), (fields, True), name)

    def test_real_totalizer_passes_the_guard(self):
        ls = lines("real_totalizer_diesel2.json")
        self.assertIsNone(meterparse.report_reason(ls))
        self.assertEqual(totalizer.parse(ls)["amount"], "775397")

    def test_small_receipt_with_many_lines_is_not_a_report(self):
        ls = [{"text": t} for t in ["PETRON STATION", "OFFICIAL RECEIPT", "DIESEL", "VOLUME 34.843 L",
                                     "PRICE 57.40/L", "TOTAL 2,000.00", "CASH 2,000.00", "CHANGE 0.00",
                                     "THANK YOU"]]
        self.assertIsNone(meterparse.report_reason(ls))

    def test_messages(self):
        self.assertEqual(meterparse.REPORT_MESSAGE, "This looks like a full report or sheet, not a pump screen or "
                                                    "receipt. Snap one pump display or one receipt.")
        self.assertTrue(meterparse.REPORT_MESSAGE_TL)


if __name__ == "__main__":
    unittest.main()
