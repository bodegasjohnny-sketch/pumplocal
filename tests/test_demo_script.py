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
    "meter_premium.png": "meter_premium.json",
    "meter_unleaded.png": "meter_unleaded.json",
    "receipt_diesel.png": "receipt_diesel.json",
    "real_totalizer_diesel2.png": "real_totalizer_diesel2.json",
    "synthetic_totalizer_diesel2_close_money.png": "synthetic_totalizer_diesel2_close_money.json",
    "synthetic_totalizer_diesel2_close_volume.png": "synthetic_totalizer_diesel2_close_volume.json",
    "closing_slip_photo.jpg": "closing_slip_photo.json",
}
# Every one of these must appear in DEMO_SCRIPT.md.
EXPECTED_IN_SCRIPT = [
    "Premium · 12.000 L × ₱85.90 = ₱1,030.80", "Unleaded · 6.000 L × ₱85.40 = ₱512.40",
    "Diesel · 21.164 L × ₱94.50 = ₱2,000.00", "775397", "778421", "13540",
    "Pump says 32 L dispensed; recorded sales 31.164 L; 0.836 L (2.61%) unaccounted",
    "Pump says ₱3,024.00 dispensed; recorded sales ₱2,945.00; ₱79.00 (2.61%) unaccounted",
    "₱94.50/L", "₱3,313.20", "₱3,263.20", "-₱50.00", "-1.51%", "₱4,488.20", "49.164", "₱2,945.00", "31.164",
]


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
        # 0. empty shift with presets
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"], sh["opening_float_preset"]), (0, "0.00", "1000.00"))
        code, pump = call(self.b, "/api/pump")
        diesel2 = pump["pumps"][0]
        self.assertEqual(diesel2["name"], "Diesel 2")

        # 1-3. Photo tab: three sale photos (20 peso senior discount typed on the Premium sale)
        sales = []
        for photo, extra in (("meter_premium.png", {"discount_pesos": "20", "discount_reason": "senior"}),
                             ("meter_unleaded.png", {}), ("receipt_diesel.png", {})):
            j = self.read("/api/extract", photo)
            self.assertTrue(j["ok"], j)
            f = j["fields"]
            sales.append("%s · %s L × ₱%s = ₱%s" % (f["fuel_type"], f["liters"], f["price_per_liter"],
                                                   "{:,.2f}".format(float(f["amount_pesos"]))))
            code, saved = call(self.b, "/api/sales", dict(f, source="photo", **extra))
            self.assertEqual(code, 200, saved)
        self.assertEqual(sales, EXPECTED_IN_SCRIPT[:3])

        # 4-6. Pump tab: REAL photo = opening (peso), synthetic closings (peso, then liter)
        for photo, kind, key, want in (("real_totalizer_diesel2.png", "open", "amount", "775397"),
                                       ("synthetic_totalizer_diesel2_close_money.png", "close", "amount", "778421"),
                                       ("synthetic_totalizer_diesel2_close_volume.png", "close", "volume", "13540")):
            j = self.read("/api/pump/extract", photo)
            self.assertEqual((j[key], j["pump_name"]), (want, "Diesel 2"), j)
            code, r = call(self.b, "/api/pump/reading", {"pump_id": diesel2["id"], "kind": kind, key: j[key],
                                                         "source": "photo"})
            self.assertEqual(code, 200, r)

        # 7-8. Cash tab: closing slip -> review -> confirm
        j = self.read("/api/slip/extract", "closing_slip_photo.jpg")
        self.assertTrue(j["ok"])
        c = j["compare"]
        self.assertEqual([e["status"] for e in c["expenses"]], ["new", "new"])
        self.assertEqual([(x["customer"], x["fuel_type"], x["status"]) for x in c["credits"]], [("Mang Ben", "Diesel", "new")])
        self.assertEqual(c["discounts"], {"slip": "20.00", "recorded": "20.00", "match": True})
        self.assertEqual((c["preview"]["expected"], c["preview"]["diff"]), ("3313.20", "-50.00"))
        code, a = call(self.b, "/api/slip/apply", {"slip": j["slip"]})
        self.assertEqual((code, len(a["saved_expenses"]), len(a["saved_credits"]), a["errors"]), (200, 2, 1, []))
        code, cash = call(self.b, "/api/cashcheck", {"declared": j["slip"]["cash_counted"], "lang": "tl",
                                                     "opening_float": j["slip"]["opening_float"],
                                                     "noncash": j["slip"]["noncash"]})
        r = cash["result"]
        self.assertEqual((r["opening_float"], r["gross_sales"], r["discounts"], r["credit_sales"], r["expenses"],
                          r["noncash"], r["expected"], r["declared"], r["diff"], r["diff_pct"], r["status"]),
                         ("1000.00", "4488.20", "20.00", "945.00", "410.00", "800.00", "3313.20", "3263.20",
                          "-50.00", "-1.51", "SHORT"))

        # Pump result after everything is saved (the credit sale is diesel too)
        code, pump = call(self.b, "/api/pump")
        g = pump["check"]["groups"][0]
        self.assertEqual(g["status"], "UNACCOUNTED")
        self.assertIn(EXPECTED_IN_SCRIPT[6], g["headline"])
        self.assertIn(EXPECTED_IN_SCRIPT[7], g["headline"])
        self.assertTrue(g["price"]["ok"])
        self.assertIn("implied ₱94.50/L", g["price"]["text"])

        # 9-12. Ask
        answers = {}
        for q in ("Magkano ang benta ng diesel ngayon?", "What were total sales today?", "Is any diesel missing?",
                  "May kulang ba sa cash?"):
            code, ans = call(self.b, "/api/ask", {"question": q})
            answers[q] = ans["answer"]
            self.assertEqual(ans["unverified_numbers"], [], ans)
        self.assertIn("₱2,945.00", answers["Magkano ang benta ng diesel ngayon?"])
        self.assertIn("31.164", answers["Magkano ang benta ng diesel ngayon?"])
        self.assertIn("₱4,488.20", answers["What were total sales today?"])
        self.assertIn("49.164", answers["What were total sales today?"])
        self.assertIn("0.836 L", answers["Is any diesel missing?"])
        self.assertIn("₱79.00", answers["Is any diesel missing?"])
        self.assertIn("₱3,313.20", answers["May kulang ba sa cash?"])
        self.assertIn("₱50.00", answers["May kulang ba sa cash?"])
        self.assertIn("kulang", answers["May kulang ba sa cash?"].lower())

    def test_script_lists_the_same_numbers(self):
        with open(os.path.join(ROOT, "DEMO_SCRIPT.md")) as f:
            text = f.read()
        for s in EXPECTED_IN_SCRIPT:
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()
