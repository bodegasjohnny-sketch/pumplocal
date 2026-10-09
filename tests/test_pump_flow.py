"""End-to-end tests for the Pump (Metro ng Pump) tab: real server, seeded demo data, every pump endpoint."""
import base64
import json
import os
import tempfile
import unittest

from tests.test_flow import FAKE_OCR, FIXTURES, ROOT, FakeOllama, Server, call, free_port, start_fake

REAL = os.path.join(ROOT, "samples", "real_totalizer_diesel2.png")
HEADLINE = "Pump says 181 L dispensed; recorded sales 174.216 L; 6.784 L (3.75%) unaccounted."
PESO_HEADLINE = "Pump says ₱10,390.00 dispensed; recorded sales ₱10,000.00; ₱390.00 (3.75%) unaccounted."


def real_b64():
    with open(REAL, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


class PumpFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server(MOCK_AI="1")
        cls.b = cls.s.base

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_1_seed_shows_gap_immediately(self):
        code, j = call(self.b, "/api/pump")
        self.assertEqual(code, 200)
        p = j["check"]["pumps"][0]
        self.assertEqual((p["name"], p["fuel_type"], p["amount_decimals"], p["volume_decimals"]), ("Diesel 2", "Diesel", 0, 0))
        self.assertEqual((p["opening_amount"], p["closing_amount"], p["opening_volume"], p["closing_volume"]),
                         ("775397", "785787", "13508", "13689"))
        self.assertEqual((p["dispensed_amount_text"], p["dispensed_volume_text"]), ("₱10,390.00", "181 L"))
        g = j["check"]["groups"][0]
        self.assertEqual((g["status"], g["flag"]), ("UNACCOUNTED", True))
        self.assertEqual((g["liters"]["dispensed"], g["liters"]["recorded"], g["liters"]["gap"], g["liters"]["gap_pct"]),
                         ("181.000", "174.216", "6.784", "3.75"))
        self.assertEqual((g["pesos"]["dispensed"], g["pesos"]["recorded"], g["pesos"]["gap"]),
                         ("10390.00", "10000.00", "390.00"))
        self.assertEqual(g["headline"], HEADLINE + " " + PESO_HEADLINE)
        self.assertEqual((g["price"]["implied"], g["price"]["ok"]), ("57.40", True))
        self.assertEqual(j["tolerance_pct"], "0.5")
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"]), (15, "16350.00"))  # seed sales unchanged
        code, samples = call(self.b, "/api/samples")
        self.assertEqual(samples["pump_samples"], ["real_totalizer_diesel2.png"])
        self.assertEqual(call(self.b, "/samples/real_totalizer_diesel2.png")[1][:4], b"\x89PNG")

    def test_2_extract_real_photo(self):
        code, ex = call(self.b, "/api/pump/extract", {"image": real_b64()})
        self.assertEqual(code, 200)
        self.assertEqual((ex["ok"], ex["amount"], ex["volume"], ex["pump_name"], ex["fuel_type"]),
                         (True, "775397", None, "Diesel 2", "Diesel"))
        self.assertEqual(call(self.b, "/api/pump/extract", {"image": ""})[0], 400)

    def test_3_ask_tagalog_pump_gap(self):
        code, a = call(self.b, "/api/ask", {"question": "May kulang ba sa diesel?"})
        self.assertEqual(a["lang"], "tl")
        self.assertIn("6.784 L (3.75%) ang hindi naitala", a["answer"])
        self.assertTrue(a["answer"].startswith("Oo."))
        self.assertEqual(a["unverified_numbers"], [])
        self.assertIn("Diesel pump check: " + HEADLINE, a["context"])
        self.assertIn("peso totalizer opening 775397, closing 785787", a["context"])
        self.assertIn("Price check OK: implied ₱57.40/L", a["context"])
        code, a = call(self.b, "/api/ask", {"question": "Is any diesel unaccounted at the pump?"})
        self.assertIn(HEADLINE, a["answer"])
        code, a = call(self.b, "/api/ask", {"question": "May kulang ba sa premium?"})
        self.assertIn("Wala pang reading", a["answer"])

    def test_4_cash_check_includes_pump_gap(self):
        code, c = call(self.b, "/api/cashcheck", {"declared": "12800", "lang": "en"})
        self.assertEqual(c["result"]["status"], "OK")
        self.assertEqual(c["pump_check"]["flagged"][0]["headline"], HEADLINE + " " + PESO_HEADLINE)
        self.assertIn(HEADLINE, c["pump_text"])
        code, j = call(self.b, "/api/pump/check", {"lang": "tl"})
        self.assertEqual(j["explanation_source"], "template")
        self.assertIn("hindi naitala", j["explanation"])
        self.assertIn("0.5% na palugit", j["explanation"])

    def test_5_tolerance_decimals_and_price_change(self):
        code, j = call(self.b, "/api/pump/settings", {"tolerance_pct": "5"})
        self.assertEqual(j["check"]["groups"][0]["status"], "OK")
        self.assertEqual(call(self.b, "/api/pump/settings", {"tolerance_pct": "abc"})[0], 400)
        call(self.b, "/api/pump/settings", {"tolerance_pct": "0.5"})
        pid = j["pumps"][0]["id"]
        code, j = call(self.b, "/api/pump/save", {"id": pid, "name": "Diesel 2", "fuel_type": "Diesel", "volume_decimals": 2})
        g = j["check"]["groups"][0]
        self.assertEqual((g["liters"]["dispensed"], g["liters"]["status"]), ("1.810", "OVER_RECORDED"))  # 135.08->136.89
        self.assertFalse(g["price"]["ok"])  # P10,390 / 1.81 L is not a real price: warning
        call(self.b, "/api/pump/save", {"id": pid, "name": "Diesel 2", "fuel_type": "Diesel", "volume_decimals": 0})
        code, j = call(self.b, "/api/pump/price_change", {"fuel_type": "Diesel", "old_price": "57.40", "new_price": "58.90"})
        self.assertEqual(j["check"]["groups"][0]["price_change"], {"old": "57.40", "new": "58.90"})
        self.assertEqual(call(self.b, "/api/pump/price_change", {"fuel_type": "Diesel", "old_price": "x",
                                                                 "new_price": "1"})[0], 400)
        call(self.b, "/api/pump/price_change", {"fuel_type": "Diesel", "old_price": "", "new_price": ""})
        self.assertEqual(call(self.b, "/api/pump/save", {"name": "", "fuel_type": "Diesel"})[0], 400)

    def test_6_new_pump_readings_and_rollover_guard(self):
        code, j = call(self.b, "/api/pump/save", {"name": "PREMIUM 1", "fuel_type": "Premium"})
        pid = j["pump"]["id"]
        self.assertEqual(j["pump"]["name"], "Premium 1")
        code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "open", "amount": "1,000,000",
                                                     "volume": "20000"})
        self.assertEqual(code, 200)
        prem = [g for g in j["check"]["groups"] if g["fuel_type"] == "Premium"][0]
        self.assertEqual(prem["status"], "INCOMPLETE")
        code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "close", "amount": "999999"})
        self.assertIn("lower than opening", j["warnings"][0])
        prem = [g for g in j["check"]["groups"] if g["fuel_type"] == "Premium"][0]
        self.assertEqual((prem["status"], prem["flag"]), ("ERROR", True))
        # Premium sales: 1000 + 300 + 500 + 2000 = P3,800 = 58.471 L at 64.99 (+ test_5 nothing)
        code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "close", "amount": "1003800",
                                                     "volume": "20058", "source": "photo"})
        prem = [g for g in j["check"]["groups"] if g["fuel_type"] == "Premium"][0]
        self.assertEqual((prem["pesos"]["dispensed"], prem["pesos"]["recorded"], prem["pesos"]["status"]),
                         ("3800.00", "3800.00", "OK"))
        self.assertEqual((prem["liters"]["dispensed"], prem["liters"]["status"]), ("58.000", "OVER_RECORDED"))
        self.assertEqual(prem["price"]["implied"], "65.52")
        self.assertFalse(prem["price"]["ok"])
        self.assertEqual(call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "close", "amount": "x"})[0], 400)
        self.assertEqual(call(self.b, "/api/pump/reading", {"pump_id": 999, "kind": "close", "amount": "1"})[0], 400)


class PumpPipelineTest(unittest.TestCase):
    def test_vision_reads_real_totalizer(self):
        s = Server(MOCK_AI="1", OCR_BIN=FAKE_OCR, FAKE_OCR_JSON=os.path.join(FIXTURES, "real_totalizer_diesel2.json"))
        try:
            code, ex = call(s.base, "/api/pump/extract", {"image": real_b64()})
            self.assertEqual((ex["reader"], ex["amount"], ex["volume"], ex["pump_name"], ex["screen"]),
                             ("vision", "775397", None, "Diesel 2", "amount"))
            self.assertNotIn("fallback_reason", ex)
            self.assertIn("2.Money All", ex["raw"])
        finally:
            s.stop()

    def test_vision_without_number_falls_back_to_gemma(self):
        tmp = os.path.join(tempfile.mkdtemp(), "glare.json")
        with open(tmp, "w") as f:  # glare: OCR only catches the buttons
            json.dump({"lines": [{"text": "Cancel", "x": 0.2, "y": 0.5, "w": 0.2, "h": 0.05},
                                 {"text": "Ok", "x": 0.6, "y": 0.5, "w": 0.1, "h": 0.05}]}, f)
        s = Server(MOCK_AI="1", OCR_BIN=FAKE_OCR, FAKE_OCR_JSON=tmp)
        try:
            code, ex = call(s.base, "/api/pump/extract", {"image": real_b64()})
            self.assertEqual((ex["reader"], ex["amount"], ex["pump_name"]), ("gemma", "775397", "Diesel 2"))
            self.assertIn("found no Volume/Total number", ex["fallback_reason"])
        finally:
            s.stop()

    def test_gemma_wording_and_ollama_down(self):
        srv, url = start_fake(FakeOllama)
        s = Server(MOCK_AI="0", OLLAMA_URL=url, READER="gemma")
        try:
            code, j = call(s.base, "/api/pump/check", {"lang": "tl"})
            self.assertEqual(j["explanation_source"], "ai")
            self.assertIn("6.784 L (3.75%) unaccounted", FakeOllama.last_chat["messages"][-1]["content"])
        finally:
            s.stop()
            srv.shutdown()
            srv.server_close()
        s = Server(MOCK_AI="0", OLLAMA_URL="http://127.0.0.1:%d" % free_port(), READER="gemma")
        try:
            code, j = call(s.base, "/api/pump/check", {"lang": "en"})
            self.assertEqual(j["explanation_source"], "template-fallback")
            self.assertIn(HEADLINE, j["explanation"])
            code, ex = call(s.base, "/api/pump/extract", {"image": real_b64()})
            self.assertEqual((code, ex["ok"]), (503, False))
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
