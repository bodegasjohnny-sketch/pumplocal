"""End-to-end tests for the Pump (Metro ng Pump) tab: real server, seeded demo data, every pump endpoint."""
import base64
import json
import os
import tempfile
import unittest

from tests.test_flow import FAKE_OCR, FIXTURES, ROOT, FakeOllama, Server, call, free_port, start_fake

REAL = os.path.join(ROOT, "samples", "real_totalizer_diesel2.png")
HEADLINE = "Pump says 181 L dispensed; recorded sales 174.216 L; 6.784 L (3.75%) unaccounted."


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
        self.assertEqual((p["name"], p["fuel_type"], p["unit"], p["decimals"], p["opening"], p["closing"]),
                         ("Diesel 2", "Diesel", "L", 0, "775397", "775578"))
        g = j["check"]["groups"][0]
        self.assertEqual((g["status"], g["flag"], g["dispensed"], g["recorded"], g["gap"], g["gap_pct"]),
                         ("UNACCOUNTED", True, "181.000", "174.216", "6.784", "3.75"))
        self.assertEqual(g["headline"], HEADLINE)
        self.assertEqual(j["tolerance_pct"], "0.5")
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"]), (15, "16350.00"))  # seed sales unchanged
        code, samples = call(self.b, "/api/samples")
        self.assertEqual(samples["pump_samples"], ["real_totalizer_diesel2.png"])
        self.assertEqual(call(self.b, "/samples/real_totalizer_diesel2.png")[1][:4], b"\x89PNG")

    def test_2_extract_real_photo(self):
        code, ex = call(self.b, "/api/pump/extract", {"image": real_b64()})
        self.assertEqual(code, 200)
        self.assertEqual((ex["ok"], ex["reading"], ex["pump_name"], ex["fuel_type"]), (True, "775397", "Diesel 2",
                                                                                       "Diesel"))
        self.assertEqual(call(self.b, "/api/pump/extract", {"image": ""})[0], 400)

    def test_3_ask_tagalog_pump_gap(self):
        code, a = call(self.b, "/api/ask", {"question": "May kulang ba sa diesel?"})
        self.assertEqual(a["lang"], "tl")
        self.assertIn("6.784 L (3.75%) ang hindi naitala", a["answer"])
        self.assertTrue(a["answer"].startswith("Oo."))
        self.assertEqual(a["unverified_numbers"], [])
        self.assertIn("Diesel pump check: " + HEADLINE, a["context"])
        code, a = call(self.b, "/api/ask", {"question": "Is any diesel unaccounted at the pump?"})
        self.assertIn(HEADLINE, a["answer"])
        code, a = call(self.b, "/api/ask", {"question": "May kulang ba sa premium?"})
        self.assertIn("Wala pang reading", a["answer"])

    def test_4_cash_check_includes_pump_gap(self):
        code, c = call(self.b, "/api/cashcheck", {"declared": "16350", "lang": "en"})
        self.assertEqual(c["result"]["status"], "OK")
        self.assertEqual(c["pump_check"]["flagged"][0]["headline"], HEADLINE)
        self.assertIn(HEADLINE, c["pump_text"])
        code, j = call(self.b, "/api/pump/check", {"lang": "tl"})
        self.assertEqual(j["explanation_source"], "template")
        self.assertIn("hindi naitala", j["explanation"])
        self.assertIn("0.5% na palugit", j["explanation"])

    def test_5_tolerance_and_decimals(self):
        code, j = call(self.b, "/api/pump/settings", {"tolerance_pct": "5"})
        self.assertEqual(j["check"]["groups"][0]["status"], "OK")
        self.assertEqual(call(self.b, "/api/pump/settings", {"tolerance_pct": "abc"})[0], 400)
        call(self.b, "/api/pump/settings", {"tolerance_pct": "0.5"})
        pid = j["pumps"][0]["id"]
        code, j = call(self.b, "/api/pump/save", {"id": pid, "name": "Diesel 2", "fuel_type": "Diesel", "unit": "L",
                                                  "decimals": 2})
        g = j["check"]["groups"][0]
        self.assertEqual((g["dispensed"], g["status"]), ("1.810", "OVER_RECORDED"))  # 775397 -> 7753.97
        call(self.b, "/api/pump/save", {"id": pid, "name": "Diesel 2", "fuel_type": "Diesel", "unit": "L",
                                        "decimals": 0})
        self.assertEqual(call(self.b, "/api/pump/save", {"name": "", "fuel_type": "Diesel"})[0], 400)

    def test_6_new_pump_readings_and_rollover_guard(self):
        code, j = call(self.b, "/api/pump/save", {"name": "PREMIUM 1", "fuel_type": "Premium", "unit": "PHP"})
        pid = j["pump"]["id"]
        self.assertEqual(j["pump"]["name"], "Premium 1")
        code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "open", "reading": "1,000,000.00"})
        self.assertEqual(code, 200)
        prem = [g for g in j["check"]["groups"] if g["fuel_type"] == "Premium"][0]
        self.assertEqual(prem["status"], "INCOMPLETE")
        code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "close", "reading": "999999"})
        self.assertIn("lower than opening", j["warnings"][0])
        prem = [g for g in j["check"]["groups"] if g["fuel_type"] == "Premium"][0]
        self.assertEqual((prem["status"], prem["flag"]), ("ERROR", True))
        code, j = call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "close", "reading": "1003800",
                                                     "source": "photo"})
        prem = [g for g in j["check"]["groups"] if g["fuel_type"] == "Premium"][0]
        # 3,800 pesos on the meter vs P3,800 of recorded Premium sales
        self.assertEqual((prem["unit"], prem["dispensed"], prem["recorded"], prem["status"]),
                         ("PHP", "3800.00", "3800.00", "OK"))
        self.assertEqual(call(self.b, "/api/pump/reading", {"pump_id": pid, "kind": "close", "reading": "x"})[0], 400)
        self.assertEqual(call(self.b, "/api/pump/reading", {"pump_id": 999, "kind": "close", "reading": "1"})[0], 400)


class PumpPipelineTest(unittest.TestCase):
    def test_vision_reads_real_totalizer(self):
        s = Server(MOCK_AI="1", OCR_BIN=FAKE_OCR, FAKE_OCR_JSON=os.path.join(FIXTURES, "real_totalizer_diesel2.json"))
        try:
            code, ex = call(s.base, "/api/pump/extract", {"image": real_b64()})
            self.assertEqual((ex["reader"], ex["reading"], ex["pump_name"], ex["unit_hint"]),
                             ("vision", "775397", "Diesel 2", "L"))
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
            self.assertEqual((ex["reader"], ex["reading"], ex["pump_name"]), ("gemma", "775397", "Diesel 2"))
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
