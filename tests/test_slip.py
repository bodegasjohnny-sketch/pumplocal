"""Shift Closing Slip: OCR text -> Cash tab values (slipparse.py), comparison with the shift (core.slip_compare /
slip_apply), and the /api/slip endpoints.

tests/ocr_fixtures/closing_slip_photo.json and closing_slip_clean.json are the OCR text of
samples/closing_slip_photo.jpg and samples/closing_slip_clean.png (synthetic), recorded with tesseract on Linux as a
stand-in for Apple Vision, in the same line format. Tesseract returns labels and amounts as separate pieces, and the
photo is tilted, so these also exercise the row joining.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

import core
import meterparse
import seed
import slipparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "ocr_fixtures")
with open(os.path.join(ROOT, "samples", "closing_slip_answer.json")) as _f:
    ANSWER = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

sys.path.insert(0, HERE)

# The three sale photos of DEMO_SCRIPT.md (Premium with a 20 peso senior discount)
DEMO_SALES = [
    {"fuel_type": "Premium", "liters": "12.000", "price_per_liter": "85.90", "amount_pesos": "1030.80",
     "discount_pesos": "20", "discount_reason": "senior", "source": "photo"},
    {"fuel_type": "Unleaded", "liters": "6.000", "price_per_liter": "85.40", "amount_pesos": "512.40", "source": "photo"},
    {"fuel_type": "Diesel", "liters": "21.164", "price_per_liter": "94.50", "amount_pesos": "2000.00", "source": "photo"},
]


def seed_demo_day():
    """seed.py --demo-empty plus the three sale photos."""
    with contextlib.redirect_stdout(io.StringIO()):
        seed.seed_demo_empty()
    for sale in DEMO_SALES:
        rec, errors, _ = core.save_sale(dict(sale))
        assert not errors, errors
from test_flow import FAKE_OCR, FakeOllama, Server, call, start_fake  # noqa: E402


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)["lines"]


class SlipParseTests(unittest.TestCase):
    def check(self, r):
        for k, v in ANSWER.items():
            self.assertEqual(r[k], v, k)
        self.assertEqual((r["is_slip"], r["missing"], r["unreadable"], r["notes"]), (True, [], [], []))

    def test_photo_fixture_every_value(self):
        self.check(slipparse.parse(fixture("closing_slip_photo.json")))

    def test_clean_fixture_every_value(self):
        self.check(slipparse.parse(fixture("closing_slip_clean.json")))

    def test_tilted_pieces_are_joined_into_rows(self):
        rows = slipparse.parse(fixture("closing_slip_photo.json"))["rows"]
        for row in ("OPENING FLOAT 1,000.00", "NOZZLE O-RING 350.00", "MANG BEN - DIESEL 945.00",
                    "CASH COUNTED 3,263.20"):
            self.assertIn(row, rows)

    def test_ocr_noise_case_peso_signs_and_no_boxes(self):
        r = slipparse.parse([{"text": t} for t in [
            "jcb shift closing slip", "Date: 09 Oct 2026", "Opening float: P1,OOO.OO", "Expenses", "ice 60",
            "load ₱ 100", "Discounts ₱50", "Credit / Utang", "Aling Nena (unleaded) PHP 612.50",
            "Gcash PHP 1,280", "card - 0", "Cash counted ₱ 12 950.00"]])
        self.assertEqual((r["opening_float"], r["discounts"], r["gcash"], r["card"], r["cash_counted"]),
                         ("1000.00", "50.00", "1280.00", "0.00", "12950.00"))
        self.assertEqual(r["expenses"], [{"description": "Ice", "amount_pesos": "60.00"},
                                         {"description": "Load", "amount_pesos": "100.00"}])
        self.assertEqual(r["credits"], [{"customer": "Aling Nena", "fuel_type": "Unleaded", "amount_pesos": "612.50"}])
        self.assertEqual((r["expenses_total"], r["credit_total"], r["noncash"]), ("160.00", "612.50", "1280.00"))
        self.assertEqual((r["missing"], r["unreadable"]), (["shift"], []))

    def test_missing_and_unreadable_are_reported_not_guessed(self):
        lines = [dict(ln, text="CARD") if ln["text"] == "CARD 300.00" else ln  # amount smudged
                 for ln in fixture("closing_slip_clean.json") if ln["text"] != "GCASH 500.00"]  # row left out
        r = slipparse.parse(lines)
        self.assertEqual((r["gcash"], r["card"], r["noncash"]), (None, None, None))
        self.assertEqual((r["missing"], r["unreadable"]), (["gcash"], ["card"]))
        self.assertEqual((r["missing_labels"], r["unreadable_labels"]), (["GCash"], ["Card"]))

    def test_discount_items_are_added_up_and_written_total_is_checked(self):
        r = slipparse.parse([{"text": t} for t in ["SHIFT CLOSING SLIP", "DISCOUNTS", "SENIOR 30.00", "PWD 20.00",
                                                   "EXPENSES", "ICE 60", "LOAD 100", "TOTAL 170.00"]])
        self.assertEqual(r["discounts"], "50.00")
        self.assertEqual(r["expenses_total"], "160.00")
        self.assertEqual(len(r["notes"]), 1)
        self.assertIn("written total", r["notes"][0])

    def test_a_pump_meter_is_not_a_slip(self):
        r = slipparse.parse(fixture("meter_premium.json"))
        self.assertFalse(r["is_slip"])

    def test_slip_is_well_under_the_photo_tab_report_guard(self):
        for name in ("closing_slip_photo.json", "closing_slip_clean.json"):
            self.assertIsNone(meterparse.report_reason(fixture(name)), name)


class SlipShiftTests(unittest.TestCase):
    """Demo Day shift (demo-empty + 3 sales): the slip's expenses and credit are new, the discount matches."""

    def setUp(self):
        self.old = core.DB_PATH
        self.tmp = tempfile.mkdtemp()
        core.DB_PATH = os.path.join(self.tmp, "t.db")
        seed_demo_day()
        self.slip = slipparse.parse(fixture("closing_slip_photo.json"))

    def tearDown(self):
        core.DB_PATH = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compare_finds_new_items_and_previews_a_50_peso_shortage(self):
        c = core.slip_compare(self.slip)
        self.assertEqual([e["status"] for e in c["expenses"]], ["new", "new"])
        self.assertEqual([(x["customer"], x["status"]) for x in c["credits"]], [("Mang Ben", "new")])
        self.assertEqual(c["discounts"], {"slip": "20.00", "recorded": "20.00", "match": True})
        p = c["preview"]
        self.assertEqual((p["expected"], p["declared"], p["diff"], p["status"]), ("3313.20", "3263.20", "-50.00", "SHORT"))

    def test_apply_saves_only_new_items_once(self):
        before = core.shift_summary()
        r = core.slip_apply(self.slip)
        self.assertEqual((len(r["saved_expenses"]), len(r["saved_credits"]), len(r["skipped"]), r["errors"]), (2, 1, 0, []))
        after = core.shift_summary()
        self.assertEqual(core.Decimal(after["expenses_total"]) - core.Decimal(before["expenses_total"]), 410)
        self.assertEqual(core.Decimal(after["credit_total"]) - core.Decimal(before["credit_total"]), 945)
        self.assertEqual(after["total_amount"], "4488.20")  # the credit sale is a diesel sale at the posted price
        again = core.slip_apply(self.slip)  # confirm pressed twice: nothing new
        self.assertEqual((again["saved_expenses"], again["saved_credits"], len(again["skipped"])), ([], [], 3))
        recheck = core.slip_compare(self.slip)
        self.assertEqual([x["status"] for x in recheck["credits"]], ["recorded"])
        self.assertEqual(recheck["preview"]["diff"], "-50.00")

    def test_credit_without_fuel_is_an_error_not_a_guess(self):
        slip = dict(self.slip, expenses=[], credits=[{"customer": "Aling Nena", "fuel_type": "", "amount_pesos": "500"}])
        r = core.slip_apply(slip)
        self.assertEqual(r["saved_credits"], [])
        self.assertTrue(r["errors"])


class SlipEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = start_fake(FakeOllama)
        cls.tmp = tempfile.mkdtemp()
        cls.wrapper = os.path.join(cls.tmp, "ocr_wrapper.sh")
        with open(cls.wrapper, "w") as f:
            f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE_OCR))
        os.chmod(cls.wrapper, 0o755)
        with open(os.path.join(ROOT, "samples", "closing_slip_photo.jpg"), "rb") as f:
            import base64
            cls.img = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_scan_review_confirm_cash_check(self):
        db = os.path.join(self.tmp, "demo.db")
        old = core.DB_PATH
        core.DB_PATH = db
        try:
            seed_demo_day()
        finally:
            core.DB_PATH = old
        s = Server(MOCK_AI="0", DB_PATH=db, OLLAMA_URL=self.url, OCR_BIN=self.wrapper,
                   FAKE_OCR_JSON=os.path.join(FIXTURES, "closing_slip_photo.json"))
        try:
            before = FakeOllama.image_chats
            code, j = call(s.base, "/api/slip/extract", {"image": self.img, "image_full": self.img})
            self.assertEqual(code, 200)
            self.assertEqual((j["ok"], j["reader"], j["source"]), (True, "vision", "Apple Vision (on-device)"))
            for k, v in ANSWER.items():
                self.assertEqual(j["slip"][k], v, k)
            self.assertEqual(j["compare"]["preview"]["diff"], "-50.00")
            time.sleep(0.2)
            self.assertEqual(FakeOllama.image_chats - before, 0, "Gemma must not be called when OCR worked")
            code, a = call(s.base, "/api/slip/apply", {"slip": j["slip"]})
            self.assertEqual((code, len(a["saved_expenses"]), len(a["saved_credits"]), len(a["skipped"])), (200, 2, 1, 0))
            code, c = call(s.base, "/api/cashcheck", {"declared": j["slip"]["cash_counted"],
                                                      "opening_float": j["slip"]["opening_float"],
                                                      "noncash": j["slip"]["noncash"]})
            self.assertEqual((c["result"]["expected"], c["result"]["diff"], c["result"]["status"]),
                             ("3313.20", "-50.00", "SHORT"))
            code, sm = call(s.base, "/api/samples")
            self.assertEqual((sm["slip_samples"], len(sm["samples"])),
                             (["closing_slip_photo.jpg", "closing_slip_clean.png"], 3))
            code, html = call(s.base, "/closing-slip")
            self.assertEqual(code, 200)
            self.assertIn("JCB SHIFT CLOSING SLIP", html.decode())
        finally:
            s.stop()

    def test_not_a_slip_does_not_go_to_gemma(self):
        s = Server(MOCK_AI="0", OLLAMA_URL=self.url, OCR_BIN=self.wrapper,
                   FAKE_OCR_JSON=os.path.join(FIXTURES, "meter_premium.json"))
        try:
            before = FakeOllama.image_chats
            code, j = call(s.base, "/api/slip/extract", {"image": self.img})
            self.assertEqual((code, j["ok"], j["reader"], j["compare"]), (200, False, "vision", None))
            self.assertIn("doesn't look like a shift closing slip", j["message"])
            time.sleep(0.2)
            self.assertEqual(FakeOllama.image_chats - before, 0)
        finally:
            s.stop()

    def test_gemma_fallback_when_ocr_fails_only_transcribes(self):
        s = Server(MOCK_AI="1", OCR_BIN=self.wrapper, FAKE_OCR_JSON="x", FAKE_OCR_FAIL="1")
        try:
            code, j = call(s.base, "/api/slip/extract", {"image": self.img})
            self.assertEqual((code, j["ok"], j["reader"]), (200, True, "gemma"))
            self.assertIn("Apple Vision OCR failed", j["fallback_reason"])
            self.assertTrue(j["warnings"])
            for k, v in ANSWER.items():
                if k in ("date", "shift"):
                    continue
                self.assertEqual(j["slip"][k], v, k)
            # every amount in the result appears in Gemma's own transcription
            for v in (j["slip"]["opening_float"], j["slip"]["cash_counted"], j["slip"]["gcash"]):
                self.assertIn("{:,.2f}".format(float(v)), j["raw"])
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
