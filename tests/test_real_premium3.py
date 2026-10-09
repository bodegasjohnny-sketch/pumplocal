"""REAL Premium 3 totalizer photos (samples/premium3_*.png, Johnny's station, Oct 9 2026).

This pump's blue dot-matrix LCD shows one counter per menu screen:
  "2.Money All / Volume 2559778"        -> PESO totalizer (the line says "Volume"; the title decides)
  "1.Report Oil / Volume 32333.73 lite" -> LITER totalizer, decimals kept ("liters" is cut off on screen)
  "3.Type Money / Coin 0 / Bank 2595535"-> peso totalizer again (Bank), same as the closing Money All
Tesseract on the build box reads nothing on this LCD, so tests/ocr_fixtures/real_premium3_*.json are hand-made
from the photos in Apple Vision's line format, including the pump's printed "Amount"/"Quantity" labels, the
"PREMIUM 3" sticker and the 1-2-3 buttons that a real OCR pass also sees.

Real shift: 32749.80 - 32333.73 = 416.07 L and 2595535 - 2559778 = P35,757 dispensed -> P85.94/L vs posted
P85.90 (0.05% off: the peso counter shows whole pesos). In the demo they are compared with a seeded Premium logbook
batch of SAMPLE sales (413.5 L), so the gap is a demo, not a real station shortage.
"""
import base64
import itertools
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
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import core  # noqa: E402
import totalizer  # noqa: E402
from test_flow import FAKE_OCR, Server, call  # noqa: E402

REAL = {  # photo -> (fixture, counter, expected reading, kind)
    "real_premium3/premium3_opening_shift_pesos.png": ("premium3_opening_shift_pesos.json", "amount", "2559778", "opening"),
    "real_premium3/premium3_opening_shift_liters.png": ("premium3_opening_shift_liters.json", "volume", "32333.73", "opening"),
    "real_premium3/premium3_closing_shift_pesos.png": ("premium3_closing_shift_pesos.json", "amount", "2595535", "closing"),
    "real_premium3/premium3_closing_shift_liters.png": ("premium3_closing_shift_liters.json", "volume", "32749.80", "closing"),
}
ORDER = list(REAL)  # opening pesos, opening liters (the previous day's closing = shift start), closing pesos, liters


def lines(fixture):
    with open(os.path.join(FIXTURES, fixture)) as f:
        return json.load(f)["lines"]


def L(text, x, y, w=0.2, h=0.04):
    return {"text": text, "confidence": 0.9, "x": x, "y": y, "w": w, "h": h}


class Premium3ParserTests(unittest.TestCase):
    def test_four_real_screens(self):
        for photo, (fixture, counter, want, _) in REAL.items():
            r = totalizer.parse(lines(fixture))
            other = "volume" if counter == "amount" else "amount"
            self.assertEqual((r[counter], r[other], r["unassigned"]), (want, None, None), photo)
            self.assertEqual((r["pump_name"], r["fuel_type"], r["confidence"]), ("Premium 3", "Premium", "labelled"))

    def test_money_all_is_pesos_even_though_the_line_says_volume(self):
        r = totalizer.parse(["2.Money All", "Volume 2559778", "Cancel Ok", "Amount", "PREMIUM 3", "Quantity"])
        self.assertEqual((r["amount"], r["volume"], r["screen"]), ("2559778", None, "amount"))
        self.assertIn('"Money All"', r["notes"][0])

    def test_report_oil_is_liters_with_decimals_and_truncated_lite(self):
        for title in ("1.Report Oil", "1.Report Oi!", "1. Report 0il"):
            r = totalizer.parse([title, "Volume 32749.80 lite", "Amount", "PREMIUM 3", "Quantity"])
            self.assertEqual((r["volume"], r["amount"], r["screen"]), ("32749.80", None, "volume"), title)
            self.assertIn("liter totalizer", r["notes"][0])

    def test_report_oil_split_into_boxes(self):
        # Vision may return "Volume" and the number as separate boxes, with the pump's "Amount" label to the right
        r = totalizer.parse([L("1.Report Oil", 0.25, 0.31), L("Volume", 0.19, 0.38, 0.14), L("32333.73 lite", 0.36, 0.38, 0.3),
                             L("Amount", 0.78, 0.38, 0.18), L("PREMIUM 3", 0.1, 0.75, 0.5), L("Quantity", 0.72, 0.74)])
        self.assertEqual((r["volume"], r["amount"]), ("32333.73", None))

    def test_type_money_bank_reads_as_pesos(self):
        r = totalizer.parse(lines("premium3_type_money_bank.json"))
        self.assertEqual((r["amount"], r["volume"], r["unassigned"]), ("2595535", None, None))
        self.assertEqual(r["pump_name"], "Premium 3")

    def test_diesel2_screens_unchanged(self):
        self.assertEqual(totalizer.parse(lines("real_totalizer_diesel2.json"))["amount"], "775397")
        self.assertEqual(totalizer.parse(lines("synthetic_totalizer_diesel2_close_volume.json"))["volume"], "13540")


class AnyOrderTests(unittest.TestCase):
    """Johnny: the lower reading is the opening, the higher the closing, whatever order the photos come in.
    The photo flow saves with kind 'auto'. A lower number after a higher one is NOT swapped silently: the pump
    shows 'Opening is higher than closing. Swap them?' with a Swap button, and nothing negative is ever counted."""

    def setUp(self):
        self.old = core.DB_PATH
        core.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        core.init_db()
        core.new_shift()
        self.pid = core.save_pump({"name": "Premium 3", "fuel_type": "Premium"})[0]["id"]

    def tearDown(self):
        core.DB_PATH = self.old

    def row(self):
        return [p for p in core.pump_check()["pumps"] if p["id"] == self.pid][0]

    def test_all_24_upload_orders(self):
        for order in itertools.permutations(REAL):
            self.setUp()
            for photo in order:
                fixture, counter, want, _ = REAL[photo]
                read = totalizer.parse(lines(fixture))[counter]
                rec, errors, warnings = core.save_reading(self.pid, "auto", source="photo", **{counter: read})
                self.assertEqual(errors, [], order)
                r = self.row()
                for c in ("amount", "volume"):  # never a negative dispensed amount
                    self.assertFalse(r["dispensed_" + c] and r["dispensed_" + c].startswith("-"), (order, r))
                if warnings:
                    self.assertIn("Opening is higher than closing. Swap them?", warnings[0])
            r = self.row()
            for c in list(r["swap"]):  # the higher photo came first: staff tap Swap
                self.assertIn("rolled over or was reset", r["error"])
                self.assertEqual(core.swap_reading(self.pid, c), [])
            r = self.row()
            self.assertEqual((r["opening_amount"], r["closing_amount"], r["opening_volume"], r["closing_volume"]),
                             ("2559778", "2595535", "32333.73", "32749.80"), order)
            self.assertEqual((r["status"], r["dispensed_volume_text"], r["dispensed_amount_text"], r["swap"]),
                             ("OK", "416.07 L", "₱35,757.00", []), order)
            self.tearDown()

    def test_in_order_needs_no_swap(self):
        for photo in ORDER:
            fixture, counter, want, kind = REAL[photo]
            rec, errors, warnings = core.save_reading(self.pid, "auto", source="photo", **{counter: want})
            self.assertEqual((errors, warnings, rec["placed"][counter]), ([], [], kind[:-3] if kind == "opening" else "close"))
        self.assertEqual(self.row()["swap"], [])

    def test_closing_first_then_opening_prompts_not_swaps(self):
        core.save_reading(self.pid, "auto", amount="2595535")
        rec, errors, warnings = core.save_reading(self.pid, "auto", amount="2559778")
        r = self.row()
        self.assertEqual((r["opening_amount"], r["closing_amount"]), ("2595535", "2559778"))  # as entered, not swapped
        self.assertEqual((r["status"], r["dispensed_amount"], r["swap"]), ("ERROR", None, ["amount"]))
        self.assertEqual(warnings[0], "Peso totalizer: " + core.dispensed("2595535", "2559778")["error"])
        self.assertIn(core.SWAP_PROMPT, warnings[0])
        self.assertEqual(core.swap_reading(self.pid, "amount"), [])
        self.assertEqual(self.row()["dispensed_amount_text"], "₱35,757.00")

    def test_rereading_the_opening_stays_opening(self):
        core.save_reading(self.pid, "auto", amount="2559778")
        rec, errors, warnings = core.save_reading(self.pid, "auto", amount="2559778")
        self.assertEqual((rec["placed"], self.row()["closing_amount"]), ({"amount": "open"}, None))

    def test_swap_needs_both(self):
        core.save_reading(self.pid, "auto", amount="2559778")
        self.assertTrue(core.swap_reading(self.pid, "amount"))
        self.assertTrue(core.swap_reading(self.pid, "bogus"))


class Premium3DemoTests(unittest.TestCase):
    """seed.py --demo-empty: pump Premium 3 (no readings) and the Premium logbook batch (SAMPLE sales). The four
    REAL photos read through the Opening / Closing photo buttons give a small demo gap and a passing price check."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        mapping = {}
        for photo, (fixture, _, _, _) in REAL.items():
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

    def test_demo_script_lists_premium3(self):
        with open(os.path.join(ROOT, "DEMO_SCRIPT.md")) as f:
            script = f.read()
        for want in ("2559778", "32333.73", "2595535", "32749.80", "416.07 L", "₱35,757", "₱85.94/L", "₱85.90/L",
                     "Opening is higher than closing. Swap them?"):
            self.assertIn(want, script)

    def test_samples_listed_apart(self):
        code, sm = call(self.b, "/api/samples")
        # Pump tab: no thumbnails. Johnny uploads samples/real_premium3/*.png with the Opening/Closing photo buttons.
        self.assertEqual((sm["real_shift_samples"], sm["pump_samples"]), ([], []))
        self.assertEqual(sm["samples"], ["meter_diesel.png", "meter_unleaded.png", "receipt_diesel.png"])
        for photo in REAL:
            self.assertTrue(os.path.exists(os.path.join(SAMPLES, photo)), photo)

    def test_b_stage_flow_opening_then_closing_buttons(self):
        """DEMO_SCRIPT steps 4-7: nothing pre-filled; Opening photo x2, then Closing photo x2 (explicit side; the
        screen title picks pesos or liters). Premium 3 vs the seeded Premium logbook batch (SAMPLE sales)."""
        code, j = call(self.b, "/api/pump")
        p3 = j["check"]["pumps"][0]
        self.assertEqual((p3["name"], p3["opening_amount"], p3["opening_volume"], p3["status"]),
                         ("Premium 3", None, None, "NONE"))
        for photo in ORDER:
            _, counter, want, kind = REAL[photo]
            with open(os.path.join(SAMPLES, photo), "rb") as f:
                img = "data:image/png;base64," + base64.b64encode(f.read()).decode()
            code, x = call(self.b, "/api/pump/extract", {"image": img, "image_full": img})
            other = "volume" if counter == "amount" else "amount"
            self.assertEqual((x[counter], x[other], x["pump_name"]), (want, None, "Premium 3"))
            code, j = call(self.b, "/api/pump/reading", {"pump_id": p3["id"], "kind": kind, "source": "photo",
                                                          counter: x[counter], other: x[other] or ""})
            self.assertEqual((code, j["warnings"]), (200, []), j)
        p = j["check"]["pumps"][0]
        self.assertEqual((p["dispensed_volume_text"], p["dispensed_amount_text"], p["status"]), ("416.07 L", "₱35,757.00", "OK"))
        g = j["check"]["groups"][0]
        self.assertEqual((g["fuel_type"], g["status"], g["liters"]["gap"], g["pesos"]["gap"]),
                         ("Premium", "UNACCOUNTED", "2.570", "237.35"))
        self.assertEqual((g["price"]["implied"], g["price"]["ok"]), ("85.94", True))

    def test_each_real_photo_reads_back(self):
        for photo, (_, counter, want, kind) in REAL.items():
            with open(os.path.join(SAMPLES, photo), "rb") as f:
                img = "data:image/png;base64," + base64.b64encode(f.read()).decode()
            code, j = call(self.b, "/api/pump/extract", {"image": img, "image_full": img})
            self.assertEqual((code, j["reader"], j[counter], j["pump_name"]), (200, "vision", want, "Premium 3"), photo)

    def test_z_api_reverse_order_then_swap(self):
        code, j = call(self.b, "/api/pump/save", {"name": "Premium 9", "fuel_type": "Premium"})
        pid = [p for p in j["pumps"] if p["name"] == "Premium 9"][0]["id"]
        for photo in reversed(ORDER):  # closing photos first
            _, counter, want, _ = REAL[photo]
            code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "auto", counter: want, "source": "photo"})
            self.assertEqual(code, 200, j)
        row = [p for p in j["check"]["pumps"] if p["id"] == pid][0]
        self.assertEqual(sorted(row["swap"]), ["amount", "volume"])
        self.assertIsNone(row["dispensed_amount"])
        for c in ("amount", "volume"):
            code, j = call(self.b, "/api/pump/swap", {"pump_id": pid, "counter": c})
            self.assertEqual(code, 200, j)
        row = [p for p in j["check"]["pumps"] if p["id"] == pid][0]
        self.assertEqual((row["dispensed_volume_text"], row["dispensed_amount_text"], row["swap"]), ("416.07 L", "₱35,757.00", []))

    def test_a_fresh_demo_shift(self):  # runs first: test_z_api_reverse_order_then_swap adds a pump
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"], sh["total_liters"]), (10, "35519.65", "413.500"))
        code, pump = call(self.b, "/api/pump")
        self.assertEqual([p["name"] for p in pump["pumps"]], ["Premium 3"])
        self.assertEqual((pump["check"]["counters_only"], pump["check"]["groups"]), ([], []))


if __name__ == "__main__":
    unittest.main()
