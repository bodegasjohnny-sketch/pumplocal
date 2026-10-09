"""Fixed Ask questions are answered by code (Oct 9, 2026 live Mac test: gemma3:4b read the raw counter 13540 as
liters, wrote "49,164 liters" and answered "Isa. 1 sales" for Premium liters). Free-form questions still go to the
model, and the model never sees raw totalizer readings."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import ai
import core
import slipparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_slip import fixture, seed_demo_day  # noqa: E402


class AskCodeTests(unittest.TestCase):
    def setUp(self):
        self.old, self.mock_ai = core.DB_PATH, core.MOCK_AI
        self.tmp = tempfile.mkdtemp()
        core.DB_PATH = os.path.join(self.tmp, "t.db")
        core.MOCK_AI = False  # real path: a model call here would be a bug for fixed questions
        seed_demo_day()
        pid = core.list_pumps()[0]["id"]
        core.save_reading(pid, "close", "778421", "13540", "photo")
        slip = slipparse.parse(fixture("closing_slip_photo.json"))
        core.slip_apply(slip)
        s = core.shift_summary()
        r = core.compute_cash(s["total_amount"], "3263.20", "1000", "800", discounts=s["discounts_total"],
                              credit=s["credit_total"], expenses=s["expenses_total"])
        core.save_cash_check(r, "", "template", s["shift"]["id"])

    def tearDown(self):
        core.DB_PATH, core.MOCK_AI = self.old, self.mock_ai
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, q):
        def no_model(*a, **k):
            raise AssertionError("the model must not be called for: " + q)
        with mock.patch.object(ai, "chat", no_model):
            t = time.time()
            r = ai.ask(q, core.shift_summary())
            self.assertLess(time.time() - t, 1.0)
        self.assertEqual((r["source"], r["unverified_numbers"]), ("code", []), r)
        return r["answer"]

    def test_the_five_chips(self):
        self.assertEqual(self.ask("Magkano ang benta ng diesel ngayon?"),
                         "Ang benta ng Diesel ngayong shift ay ₱2,945.00 (31.164 L, 2 benta).")
        self.assertEqual(self.ask("What are total sales this shift?"),
                         "Total sales this shift: ₱4,488.20 (49.164 L, 4 sales).")
        self.assertEqual(self.ask("Ilang litro ng Premium ang nabenta?"),
                         "12.000 L ang nabentang Premium ngayong shift (₱1,030.80, 1 benta).")
        self.assertEqual(self.ask("May kulang ba sa cash?"),
                         "May kulang na ₱50.00 (-1.51%) ang cash. Bilangin ulit ang pera at tingnan ang GCash/card "
                         "slips at mga benta na hindi pa naitala. (Inaasahan: ₱3,313.20, nabilang: ₱3,263.20.)")
        a = self.ask("May kulang ba sa diesel?")
        self.assertTrue(a.startswith("Oo. Ayon sa metro ng pump, 32 L ang nailabas; 31.164 L ang naitalang benta; "
                                     "0.836 L (2.61%) ang hindi naitala."), a)
        self.assertNotIn("13540", a)  # never the raw counter

    def test_close_matches_in_english_and_tagalog(self):
        self.assertIn("49.164 L", self.ask("Magkano ang kabuuang benta ngayon?"))
        self.assertNotIn("49,164", self.ask("What were total sales today?"))
        self.assertEqual(self.ask("How many liters of premium were sold?"),
                         "12.000 L of Premium sold this shift (₱1,030.80, 1 sale).")
        self.assertIn("₱2,945.00", self.ask("How much diesel did we sell?"))
        self.assertIn("Cash is short by ₱50.00", self.ask("Is the cash short?"))
        self.assertIn("0.836 L (2.61%) unaccounted", self.ask("Is any diesel missing?"))
        self.assertEqual(self.ask("How much were discounts?"), "Discounts this shift: ₱20.00 on 1 sale.")

    def test_free_form_goes_to_the_model_without_raw_counters(self):
        seen = {}

        def fake_chat(messages, json_mode=False, num_predict=200):
            seen["prompt"] = messages[-1]["content"]
            return "Kulang ng 0.836 L ang naitalang diesel kumpara sa metro."
        with mock.patch.object(ai, "chat", fake_chat):
            r = ai.ask("Bakit hindi tugma ang diesel?", core.shift_summary())
        self.assertEqual((r["source"], r["lang"]), ("ai", "tl"))
        p = seen["prompt"]
        self.assertIn("Diesel 2 (Diesel): dispensed this shift 32 L and ₱3,024.00.", p)
        self.assertIn("TOTAL (gross sales): 49.164 L, ₱4,488.20.", p)
        for raw in ("775397", "778421", "13508", "13540"):
            self.assertNotIn(raw, p)


if __name__ == "__main__":
    unittest.main()
