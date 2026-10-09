"""OCR line parsing tests (pure code, no Vision needed).

Apple Vision can't run on Linux CI, so these feed realistic Vision output for the three synthetic
samples: one observation per text block, boxes normalized 0..1 with a top-left origin (as printed
by ocr/ocr.swift), including the kinds of splits/merges and misreads Vision produces.
"""
import json
import os
import unittest

import meterparse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_fixtures")


def px(text, x0, y0, x1, y1, W, H, conf=1.0):
    """Line from a pixel box on a W x H image."""
    return {"text": text, "confidence": conf, "x": x0 / W, "y": y0 / H, "w": (x1 - x0) / W, "h": (y1 - y0) / H}


def meter(fuel, amount, liters, price, drop=(), override=None):
    W, H = 900, 1000
    rows = [
        (fuel.upper(), 243, 68, 657, 132),
        ("AMOUNT (PESOS)", 90, 214, 440, 248),
        (amount, 288, 296, 788, 392),
        ("LITERS", 90, 464, 220, 497),
        (liters, 360, 546, 786, 642),
        ("PRICE PER LITER", 90, 714, 407, 748),
        (price, 432, 796, 788, 892),
        ("SYNTHETIC SAMPLE - PumpLocal demo", 210, 935, 690, 957),
    ]
    out = []
    for text, *box in rows:
        if text in drop:
            continue
        text = (override or {}).get(text, text)
        out.append(px(text, *box, W=W, H=H, conf=0.5 if text[0].isdigit() else 1.0))
    return out


def receipt(merged=False, peso_sign="P "):
    """receipt_diesel.png (681 x 1024 after the 2.5 degree rotation)."""
    W, H = 681, 1024
    rows = [
        ("DEMO FUEL STATION", 98, 62, 547, 116),
        ("Brgy. Sample, Philippines", 113, 106, 532, 147),
        ("SALES INVOICE", 209, 154, 442, 190),
        ("Date: 2026-10-09 10:42", 50, 245, 437, 289),
        ("Pump: 2 Nozzle: 1", 53, 296, 372, 335),
    ]
    pairs = [
        ("Product:", (57, 396, 200, 428), "DIESEL", (344, 383, 453, 414)),
        ("Volume:", (59, 441, 185, 474), "34.843 L", (292, 429, 437, 462)),
        ("Price/L:", (61, 487, 204, 520), peso_sign + "57.40", (276, 476, 403, 507)),
        ("TOTAL:", (65, 580, 221, 624), peso_sign + "2,000.00", (354, 560, 619, 607)),
        ("Cash:", (69, 673, 152, 702), peso_sign + "2,000.00", (252, 658, 421, 690)),
        ("Change:", (70, 716, 188, 747), peso_sign + "0.00", (254, 704, 423, 735)),
    ]
    for label, lb, value, vb in pairs:
        if merged:
            rows.append(("%s %s" % (label, value), lb[0], min(lb[1], vb[1]), vb[2], max(lb[3], vb[3])))
        else:
            rows.append((label, *lb))
            rows.append((value, *vb))
    rows += [("Thank you! Salamat po!", 155, 793, 549, 836), ("SYNTHETIC SAMPLE", 222, 886, 491, 922),
             ("not a real receipt", 208, 930, 510, 968)]
    return [px(t, *b, W=W, H=H) for t, *b in rows]


class SampleTests(unittest.TestCase):
    def check(self, lines, fuel, liters, price, amount, consistent=True):
        r = meterparse.parse(lines)
        self.assertEqual(r["fields"], {"fuel_type": fuel, "liters": liters, "price_per_liter": price,
                                       "amount_pesos": amount}, r)
        self.assertEqual(r["consistent"], consistent, r)
        return r

    def test_meter_premium(self):
        r = self.check(meter("Premium", "1000.00", "15.387", "64.99"), "Premium", "15.387", "64.99", "1000.00")
        self.assertEqual(r["computed"], [])

    def test_meter_unleaded(self):
        self.check(meter("Unleaded", "500.00", "8.163", "61.25"), "Unleaded", "8.163", "61.25", "500.00")

    def test_receipt_diesel_split_lines(self):
        # TOTAL and Cash both say P 2,000.00; Change is P 0.00; date/time/pump numbers must be ignored.
        self.check(receipt(), "Diesel", "34.843", "57.40", "2000.00")

    def test_receipt_diesel_merged_lines(self):
        self.check(receipt(merged=True), "Diesel", "34.843", "57.40", "2000.00")

    def test_receipt_peso_sign_variants(self):
        self.check(receipt(peso_sign="₱"), "Diesel", "34.843", "57.40", "2000.00")
        self.check(receipt(merged=True, peso_sign="PHP "), "Diesel", "34.843", "57.40", "2000.00")

    def test_fixture_files(self):
        """Saved Vision-format JSON (same shape ocr.swift prints) for each sample."""
        expected = {
            "meter_premium.json": ("Premium", "15.387", "64.99", "1000.00"),
            "meter_unleaded.json": ("Unleaded", "8.163", "61.25", "500.00"),
            "receipt_diesel.json": ("Diesel", "34.843", "57.40", "2000.00"),
        }
        for name, want in expected.items():
            with open(os.path.join(FIXTURES, name)) as f:
                lines = json.load(f)["lines"]
            with self.subTest(name):
                self.check(lines, *want)


class TwoOfThreeTests(unittest.TestCase):
    def test_liters_unreadable_is_computed(self):
        r = meterparse.parse(meter("Premium", "1000.00", "15.387", "64.99", drop=("15.387",)))
        self.assertEqual(r["fields"]["liters"], "15.387")  # 1000 / 64.99
        self.assertEqual(r["computed"], ["liters"])
        self.assertTrue(r["consistent"])

    def test_amount_unreadable_is_computed(self):
        r = meterparse.parse(meter("Unleaded", "500.00", "8.163", "61.25", drop=("500.00",)))
        self.assertEqual(r["fields"]["amount_pesos"], "499.98")  # 8.163 x 61.25 (meter rounds liters)
        self.assertEqual(r["computed"], ["amount_pesos"])
        self.assertTrue(r["consistent"])

    def test_price_unreadable_is_computed(self):
        r = meterparse.parse(meter("Premium", "1000.00", "15.387", "64.99", drop=("64.99",)))
        self.assertEqual(r["fields"]["price_per_liter"], "64.99")
        self.assertTrue(r["consistent"])

    def test_receipt_missing_price(self):
        lines = [ln for ln in receipt() if ln["text"] not in ("Price/L:", "P 57.40")]
        r = meterparse.parse(lines)
        self.assertEqual(r["fields"]["price_per_liter"], "57.40")
        self.assertTrue(r["consistent"])


class RobustnessTests(unittest.TestCase):
    def test_missing_labels_still_found_by_consistency(self):
        lines = meter("Premium", "1000.00", "15.387", "64.99", drop=("AMOUNT (PESOS)", "LITERS", "PRICE PER LITER"))
        r = meterparse.parse(lines)
        self.assertEqual((r["fields"]["amount_pesos"], r["fields"]["liters"], r["fields"]["price_per_liter"]),
                         ("1000.00", "15.387", "64.99"))
        self.assertTrue(r["consistent"])

    def test_ocr_letter_confusions(self):
        lines = meter("Premium", "1000.00", "15.387", "64.99", override={"1000.00": "1OOO.0O", "15.387": "1S.387"})
        r = meterparse.parse(lines)
        self.assertEqual(r["fields"]["amount_pesos"], "1000.00")
        self.assertEqual(r["fields"]["liters"], "15.387")
        self.assertTrue(r["consistent"])

    def test_misread_digit_is_inconsistent(self):
        # liters misread 75.387: 75.387 x 64.99 = 4,899 != 1,000 -> not trusted (falls back to Gemma)
        r = meterparse.parse(meter("Premium", "1000.00", "15.387", "64.99", override={"15.387": "75.387"}))
        self.assertFalse(r["consistent"])

    def test_within_one_percent(self):
        r = meterparse.parse(meter("Premium", "1000.00", "15.387", "64.99", override={"15.387": "15.4"}))
        self.assertTrue(r["consistent"])  # 15.4 x 64.99 = 1000.85, within 1%
        r = meterparse.parse(meter("Premium", "1000.00", "15.387", "64.99", override={"15.387": "15.6"}))
        self.assertFalse(r["consistent"])  # 1013.84, off by 1.4%

    def test_nothing_readable(self):
        r = meterparse.parse([px("PREMIUM", 243, 68, 657, 132, 900, 1000)])
        self.assertEqual(r["fields"], {"fuel_type": "Premium", "liters": None, "price_per_liter": None,
                                       "amount_pesos": None})
        self.assertFalse(r["consistent"])
        self.assertFalse(meterparse.parse([])["consistent"])

    def test_only_one_value(self):
        r = meterparse.parse(meter("Diesel", "1000.00", "15.387", "64.99", drop=("15.387", "64.99")))
        self.assertEqual(r["fields"]["amount_pesos"], "1000.00")
        self.assertFalse(r["consistent"])

    def test_price_label_containing_liter_is_price(self):
        self.assertEqual(meterparse.label_of("PRICE PER LITER"), "price")
        self.assertEqual(meterparse.label_of("Price/L:"), "price")
        self.assertEqual(meterparse.label_of("LITRO"), "liters")
        self.assertEqual(meterparse.label_of("TOTAL:"), "amount")
        self.assertEqual(meterparse.label_of("Cash:"), "ignore")

    def test_numbers(self):
        n = meterparse.numbers_in
        self.assertEqual([str(x) for x in n("P 2,000.00")], ["2000.00"])
        self.assertEqual([str(x) for x in n("57,40")], ["57.40"])
        self.assertEqual(n("Date: 2026-10-09 10:42"), [])


if __name__ == "__main__":
    unittest.main()
