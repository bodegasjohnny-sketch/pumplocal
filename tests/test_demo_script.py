"""End-to-end Demo Day run: exactly the click order in DEMO_SCRIPT.md, against the real server.

Starts from `python3 seed.py --demo-empty`, reads every sample photo through its tab (OCR mocked with the
recorded text of each image: a stand-in OCR program picks the fixture by the image's sha256), saves each one,
confirms the closing slip, runs the cash check and asks the four questions. The final numbers are asserted here
AND must appear in DEMO_SCRIPT.md, so the script and the app can't drift apart.
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(ROOT, "samples")
FIXTURES = os.path.join(HERE, "ocr_fixtures")
sys.path.insert(0, HERE)
from test_flow import FAKE_OCR, Server, call  # noqa: E402

PHOTOS = {  # sample -> recorded OCR text
    "meter_diesel.png": "meter_diesel.json",
    "meter_unleaded.png": "meter_unleaded.json",
    "receipt_diesel.png": "receipt_diesel.json",
    "real_premium3/premium3_opening_shift_pesos.png": "premium3_opening_shift_pesos.json",
    "real_premium3/premium3_opening_shift_liters.png": "premium3_opening_shift_liters.json",
    "real_premium3/premium3_closing_shift_pesos.png": "premium3_closing_shift_pesos.json",
    "real_premium3/premium3_closing_shift_liters.png": "premium3_closing_shift_liters.json",
    "closing_slip_photo.jpg": "closing_slip_photo.json",
}
# Every one of these must appear in DEMO_SCRIPT.md.
EXPECTED_IN_SCRIPT = [
    "Diesel · 15.000 L × ₱94.50 = ₱1,417.50", "Unleaded · 6.000 L × ₱85.40 = ₱512.40",
    "Diesel · 21.164 L × ₱94.50 = ₱2,000.00", "2559778", "32333.73", "2595535", "32749.80",
    "Pump says 416.07 L dispensed; recorded sales 413.5 L; 2.57 L (0.62%) unaccounted",
    "Pump says ₱35,757.00 dispensed; recorded sales ₱35,519.65; ₱237.35 (0.66%) unaccounted",
    "₱85.94/L", "₱39,219.55", "₱39,169.55", "-₱50.00", "₱40,394.55", "465.664", "₱4,362.50", "46.164",
    "Pump readings: real photos. Sales: sample data. Gap is a demo, not a real station shortage.",
]
HEADLINE_L, HEADLINE_P = 7, 8


def b64(name):
    with open(os.path.join(SAMPLES, name), "rb") as f:
        data = f.read()
    kind = "jpeg" if name.endswith(".jpg") else "png"
    return "data:image/%s;base64,%s" % (kind, base64.b64encode(data).decode())


class DemoScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        mapping = {}
        for photo, fixture in PHOTOS.items():
            with open(os.path.join(SAMPLES, photo), "rb") as f:
                mapping[hashlib.sha256(f.read()).hexdigest()] = os.path.join(FIXTURES, fixture)
        cls.map = os.path.join(cls.tmp, "map.json")
        with open(cls.map, "w") as f:
            json.dump(mapping, f)
        cls.wrapper = os.path.join(cls.tmp, "ocr.sh")
        with open(cls.wrapper, "w") as f:
            f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE_OCR))
        os.chmod(cls.wrapper, 0o755)
        cls.db = os.path.join(cls.tmp, "demo.db")
        out = subprocess.run([sys.executable, os.path.join(ROOT, "seed.py"), "--demo-empty"], cwd=ROOT,
                             env=dict(os.environ, DB_PATH=cls.db), capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        cls.s = Server(MOCK_AI="1", DB_PATH=cls.db, OCR_BIN=cls.wrapper, FAKE_OCR_MAP=cls.map)
        cls.b = cls.s.base

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def read(self, path, photo):
        code, j = call(self.b, path, {"image": b64(photo), "image_full": b64(photo)})
        self.assertEqual(code, 200, j)
        self.assertEqual(j["reader"], "vision", (photo, j))
        return j

    def test_demo_day_sequence(self):
        # 0. fresh shift: float, prices, pump Premium 3 (no readings) and the Premium logbook batch (SAMPLE sales)
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"], sh["total_liters"], sh["opening_float_preset"]),
                         (10, "35519.65", "413.500", "1000.00"))
        self.assertTrue(all("Sample sales (demo)" in x["note"] for x in sh["sales"]))
        code, pump = call(self.b, "/api/pump")
        self.assertEqual([(p["name"], p["fuel_type"]) for p in pump["pumps"]], [("Premium 3", "Premium")])
        p3 = pump["pumps"][0]

        # 1-3. Photo tab: three sale photos, saved as read. The P20 senior discount comes from the closing slip.
        sales = []
        for photo in ("meter_diesel.png", "meter_unleaded.png", "receipt_diesel.png"):
            j = self.read("/api/extract", photo)
            self.assertTrue(j["ok"], j)
            f = j["fields"]
            sales.append("%s · %s L × ₱%s = ₱%s" % (f["fuel_type"], f["liters"], f["price_per_liter"],
                                                   "{:,.2f}".format(float(f["amount_pesos"]))))
            code, saved = call(self.b, "/api/sales", dict(f, source="photo"))
            self.assertEqual(code, 200, saved)
        self.assertEqual(sales, EXPECTED_IN_SCRIPT[:3])

        # 4-7. Pump tab: Premium 3 card, Opening photo x2 then Closing photo x2 (REAL photos, explicit side;
        # the screen title picks pesos or liters)
        for photo, kind, key, want in (("real_premium3/premium3_opening_shift_pesos.png", "open", "amount", "2559778"),
                                       ("real_premium3/premium3_opening_shift_liters.png", "open", "volume", "32333.73"),
                                       ("real_premium3/premium3_closing_shift_pesos.png", "close", "amount", "2595535"),
                                       ("real_premium3/premium3_closing_shift_liters.png", "close", "volume", "32749.80")):
            j = self.read("/api/pump/extract", photo)
            other = "volume" if key == "amount" else "amount"
            self.assertEqual((j[key], j[other], j["pump_name"]), (want, None, "Premium 3"), j)
            code, r = call(self.b, "/api/pump/reading", {"pump_id": p3["id"], "kind": kind, key: j[key],
                                                         "source": "photo"})
            self.assertEqual((code, r["warnings"]), (200, []), r)
        g = r["check"]["groups"][0]
        self.assertEqual((g["fuel_type"], g["status"], g["flag"]), ("Premium", "UNACCOUNTED", True))
        self.assertIn(EXPECTED_IN_SCRIPT[HEADLINE_L], g["headline"])
        self.assertIn(EXPECTED_IN_SCRIPT[HEADLINE_P], g["headline"])
        self.assertTrue(g["price"]["ok"])
        self.assertIn("implied ₱85.94/L", g["price"]["text"])
        self.assertIn("vs posted ₱85.90/L", g["price"]["text"])

        # 8-9. Cash tab: closing slip -> review -> confirm
        j = self.read("/api/slip/extract", "closing_slip_photo.jpg")
        self.assertTrue(j["ok"])
        c = j["compare"]
        self.assertEqual([e["status"] for e in c["expenses"]], ["new", "new"])
        self.assertEqual([(x["customer"], x["fuel_type"], x["status"]) for x in c["credits"]], [("Mang Ben", "Diesel", "new")])
        self.assertEqual(c["discounts"], {"slip": "20.00", "recorded": "0.00", "match": False, "from_slip": "20.00",
                                         "after": "20.00"})
        self.assertEqual((c["preview"]["expected"], c["preview"]["diff"]), ("39219.55", "-50.00"))
        code, a = call(self.b, "/api/slip/apply", {"slip": j["slip"]})
        self.assertEqual((code, len(a["saved_expenses"]), len(a["saved_credits"]), a["errors"], a["discount_from_slip"]),
                         (200, 2, 1, [], "20.00"))
        code, cash = call(self.b, "/api/cashcheck", {"declared": j["slip"]["cash_counted"], "lang": "tl",
                                                     "opening_float": j["slip"]["opening_float"],
                                                     "noncash": j["slip"]["noncash"]})
        r = cash["result"]
        self.cash = r
        self.assertEqual((r["opening_float"], r["gross_sales"], r["discounts"], r["credit_sales"], r["expenses"],
                          r["noncash"], r["expected"], r["declared"], r["diff"], r["status"]),
                         ("1000.00", "40394.55", "20.00", "945.00", "410.00", "800.00", "39219.55", "39169.55",
                          "-50.00", "SHORT"))
        self.assertEqual(r["diff_pct"], "-0.13")

        # Pump result after everything is saved: the credit sale is diesel, so Premium 3 is unchanged
        code, pump = call(self.b, "/api/pump")
        self.assertEqual(pump["check"]["groups"][0]["headline"], g["headline"])

        # 10-14. Ask: the five chip questions are answered by code ("Computed by PumpLocal"), exact numbers
        answers = {}
        script = open(os.path.join(ROOT, "DEMO_SCRIPT.md")).read()
        for q in ("Magkano ang benta ng diesel ngayon?", "What are total sales this shift?",
                  "Ilang litro ng Premium ang nabenta?", "May kulang ba sa cash?", "May kulang ba sa premium?",
                  "What were total sales today?", "Is any premium missing?"):
            code, ans = call(self.b, "/api/ask", {"question": q})
            answers[q] = ans["answer"]
            if os.environ.get("SHOW_ANSWERS"):
                print("\n%s -> %s" % (q, ans["answer"]))
            self.assertEqual((ans["source"], ans["unverified_numbers"]), ("code", []), ans)
            if not os.environ.get("SHOW_ANSWERS"):
                self.assertIn(ans["answer"], script)
        self.assertIn("₱4,362.50", answers["Magkano ang benta ng diesel ngayon?"])
        self.assertIn("46.164", answers["Magkano ang benta ng diesel ngayon?"])
        self.assertIn("₱40,394.55", answers["What are total sales this shift?"])
        self.assertIn("465.664 L", answers["What are total sales this shift?"])
        self.assertIn("413.500 L", answers["Ilang litro ng Premium ang nabenta?"])
        self.assertIn("2.57 L", answers["May kulang ba sa premium?"])
        self.assertIn("₱237.35", answers["Is any premium missing?"])
        self.assertIn("₱39,219.55", answers["May kulang ba sa cash?"])
        self.assertIn("₱50.00", answers["May kulang ba sa cash?"])
        # 15. the free-form question goes to the local model (template in mock mode)
        code, ans = call(self.b, "/api/ask", {"question": "Bakit hindi tugma ang premium?"})
        self.assertNotEqual(ans["source"], "code")

    def test_script_lists_the_same_numbers(self):
        with open(os.path.join(ROOT, "DEMO_SCRIPT.md")) as f:
            text = f.read()
        for s in EXPECTED_IN_SCRIPT:
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()
