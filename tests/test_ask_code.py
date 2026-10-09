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
        pid = core.list_pumps()[0]["id"]  # Premium 3, REAL readings
        core.save_reading(pid, "open", "2559778", "32333.73", "photo")
        core.save_reading(pid, "close", "2595535", "32749.80", "photo")
        slip = slipparse.parse(fixture("closing_slip_photo.json"))
        core.slip_apply(slip)
        s = core.shift_summary()
        r = core.compute_cash(s["total_amount"], "39169.55", "1000", "800", discounts=s["discounts_total"],
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
                         "Ang benta ng Diesel ngayong shift ay ₱4,362.50 (46.164 L, 3 benta).")
        self.assertEqual(self.ask("What are total sales this shift?"),
                         "Total sales this shift: ₱40,394.55 (465.664 L, 14 sales).")
        self.assertEqual(self.ask("Ilang litro ng Premium ang nabenta?"),
                         "413.500 L ang nabentang Premium ngayong shift (₱35,519.65, 10 benta).")
        self.assertEqual(self.ask("May kulang ba sa cash?"),
                         "May kulang na ₱50.00 (-0.13%) ang cash. Bilangin ulit ang pera at tingnan ang GCash/card "
                         "slips at mga benta na hindi pa naitala. (Inaasahan: ₱39,219.55, nabilang: ₱39,169.55.)")
        a = self.ask("May kulang ba sa premium?")
        self.assertTrue(a.startswith("Oo. Ayon sa metro ng pump, 416.07 L ang nailabas; 413.5 L ang naitalang benta; "
                                     "2.57 L (0.62%) ang hindi naitala."), a)
        self.assertNotIn("32749", a)  # never the raw counter
        self.assertIn("Wala pang reading", self.ask("May kulang ba sa diesel?"))

    def test_close_matches_in_english_and_tagalog(self):
        self.assertIn("465.664 L", self.ask("Magkano ang kabuuang benta ngayon?"))
        self.assertNotIn("465,664", self.ask("What were total sales today?"))
        self.assertEqual(self.ask("How many liters of premium were sold?"),
                         "413.500 L of Premium sold this shift (₱35,519.65, 10 sales).")
        self.assertIn("₱4,362.50", self.ask("How much diesel did we sell?"))
        self.assertIn("Cash is short by ₱50.00", self.ask("Is the cash short?"))
        self.assertIn("2.57 L (0.62%) unaccounted", self.ask("Is any premium missing?"))
        self.assertEqual(self.ask("How much were discounts?"), "Discounts this shift: ₱20.00 on 1 sale.")

    def test_free_form_goes_to_the_model_without_raw_counters(self):
        seen = {}

        def fake_chat(messages, json_mode=False, num_predict=200):
            seen["prompt"] = messages[-1]["content"]
            return "Kulang ng 2.57 L ang naitalang premium kumpara sa metro."
        with mock.patch.object(ai, "chat", fake_chat):
            r = ai.ask("Bakit hindi tugma ang premium?", core.shift_summary())
        self.assertEqual((r["source"], r["lang"]), ("ai", "tl"))
        p = seen["prompt"]
        self.assertIn("Premium 3 (Premium): dispensed this shift 416.07 L and ₱35,757.00.", p)
        self.assertIn("TOTAL (gross sales): 465.664 L, ₱40,394.55.", p)
        for raw in ("2559778", "2595535", "32333.73", "32749.8"):
            self.assertNotIn(raw, p)


    def test_why_and_opinion_questions_go_to_the_model(self):
        self.assertIn("2.57 L (0.62%)", self.ask("May kulang ba sa premium?"))  # plain question stays on code
        for q in ("ano sa tingin mo ang reason bakit may kulang sa premium?", "Bakit may kulang sa premium?",
                  "Why is premium short?", "What's the reason premium is missing liters?", "Ano ang dahilan ng kulang?",
                  "What do you think about the premium gap?", "Explain the premium shortage",
                  "Ipaliwanag ang kulang sa premium", "Paano nagkaroon ng kulang sa premium?",
                  "How come premium is short?"):
            seen = {}

            def fake_chat(messages, json_mode=False, num_predict=200):
                seen["prompt"] = messages[-1]["content"]
                return "Maaaring may benta na hindi naitala; 2.57 L ang hindi naitala."
            with mock.patch.object(ai, "chat", fake_chat):
                r = ai.ask(q, core.shift_summary())
            self.assertEqual(r["source"], "ai", q)
            self.assertIn("416.07 L", seen["prompt"], q)  # computed numbers still given as context
        self.assertEqual(self.ask("How many liters of premium were sold?"),
                         "413.500 L of Premium sold this shift (₱35,519.65, 10 sales).")  # "how" alone stays on code


    def test_why_prompt_neutral_causes_and_blame_filter(self):
        seen = {}

        def fake_chat(messages, json_mode=False, num_predict=200):
            seen["prompt"] = messages[-1]["content"]
            return seen.get("reply", "Kulang ng 2.57 L. Posibleng may benta na hindi naitala.")
        with mock.patch.object(ai, "chat", fake_chat):
            r = ai.ask("Bakit may kulang sa premium?", core.shift_summary())
            p = seen["prompt"]
            for want in ("neutral possible causes", "test pour", "calibration", "counting error",
                         "Never blame staff", "Never mention theft"):
                self.assertIn(want, p)
            self.assertEqual(r["source"], "ai")
            for bad in ("Baka ninakaw ng staff ang 2.57 L.", "Maybe someone stole 2.57 L.", "Possible theft of fuel.",
                        "Baka may nagnakaw.", "Baka may magnanakaw."):
                seen["reply"] = bad
                r = ai.ask("Bakit may kulang sa premium?", core.shift_summary())
                self.assertEqual(r["source"], "ai-filtered", bad)
                self.assertFalse(ai.BLAME_WORDS.search(r["answer"]), r["answer"])
                self.assertIn("2.57 L", r["answer"])
                self.assertIn("calibrat", r["answer"])
                self.assertEqual(r["unverified_numbers"], [])
            # plain questions keep the short prompt
            seen["reply"] = "OK"
            ai.ask("Ano ang lagay ng shift?", core.shift_summary())
            self.assertNotIn("neutral possible causes", seen["prompt"])


if __name__ == "__main__":
    unittest.main()
