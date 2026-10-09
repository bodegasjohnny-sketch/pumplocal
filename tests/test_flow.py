"""End-to-end tests: start the real server and exercise every endpoint over HTTP.

Uses MOCK_AI=1, a fake sync receiver, and a fake Ollama server, so no internet or model is needed.
Run: python3 -m unittest discover -s tests -v
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "samples", "meter_premium.png")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, (json.loads(raw) if "json" in ctype else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class FakeSync(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeSync.received.append(body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self):
        self.send_response(405)
        self.end_headers()


class FakeOllama(BaseHTTPRequestHandler):
    """Mimics Ollama's /api/tags and /api/chat with realistic messy model output."""
    last_chat = None

    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"models": [{"name": "gemma3:4b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.last_chat = body
        msg = body["messages"][-1]
        if msg.get("images"):
            content = 'Here you go:\n{"fuel_type":"diesel","liters":"34.843","price_per_liter":"P57.40","amount_pesos":"2,000.00"}'
        elif "QUESTION:" in msg["content"]:
            content = "Ang benta ng Diesel ngayon ay ₱10,000.00, at may ₱99,999.00 pa."  # one hallucinated number
        else:
            content = "Kulang ang cash. Bilangin ulit."
        self._send({"message": {"role": "assistant", "content": content}, "done": True})


def start_fake(handler):
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % port


class Server(object):
    def __init__(self, **env):
        self.port = free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.tmp = tempfile.mkdtemp()
        e = dict(os.environ, PORT=str(self.port), NO_BROWSER="1", QUIET="1",
                 DB_PATH=os.path.join(self.tmp, "test.db"), SYNC_INTERVAL="3600")
        e.pop("SYNC_URL", None)
        e.update(env)
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py")], env=e,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/api/status", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("server did not start")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)
        self.proc.stdout.close()


def image_b64():
    with open(SAMPLE, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


class MockFlowTest(unittest.TestCase):
    """Full staff flow in MOCK_AI mode, no sync URL (fully offline)."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server(MOCK_AI="1")
        cls.b = cls.s.base

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_1_pages_load(self):
        code, html = call(self.b, "/")
        self.assertEqual(code, 200)
        html = html.decode()
        self.assertIn("PumpLocal", html)
        for bad in ("http://", "https://", "<link rel=\"stylesheet\"", "<script src"):
            self.assertNotIn(bad, html.replace("http://www.w3.org/2000/svg", ""), "external resource: " + bad)
        code, samples = call(self.b, "/api/samples")
        self.assertEqual(len(samples["samples"]), 3)
        code, png = call(self.b, "/samples/" + samples["samples"][0])
        self.assertEqual((code, png[:4]), (200, b"\x89PNG"))
        self.assertEqual(call(self.b, "/samples/../app.py")[0], 404)

    def test_2_autoseed_and_offline_status(self):
        code, st = call(self.b, "/api/status")
        self.assertTrue(st["ai"]["ok"] and st["ai"]["mock"])
        self.assertFalse(st["sync"]["online"])
        self.assertEqual(st["sync"]["queued"], 16)  # 1 shift + 15 seeded sales
        self.assertTrue(st["sync"]["label"].startswith("Offline, 16 records queued"))
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["total_amount"]), (15, "16350.00"))
        self.assertEqual([f["fuel_type"] for f in sh["fuels"]], ["Premium", "Unleaded", "Diesel"])

    def test_3_photo_extract_then_correct_and_save(self):
        code, ex = call(self.b, "/api/extract", {"image": image_b64()})
        self.assertEqual(code, 200)
        self.assertTrue(ex["ok"])
        self.assertEqual(ex["fields"], {"fuel_type": "Premium", "liters": "15.387", "price_per_liter": "64.99",
                                        "amount_pesos": "1000.00"})
        self.assertEqual(call(self.b, "/api/extract", {"image": ""})[0], 400)
        # staff corrects the amount to 1,500 and clears liters -> code recomputes liters
        fields = dict(ex["fields"], amount_pesos="1500", liters="", source="photo")
        code, saved = call(self.b, "/api/sales", fields)
        self.assertEqual(code, 200)
        self.assertEqual((saved["sale"]["liters"], saved["sale"]["amount_pesos"], saved["sale"]["synced"]),
                         ("23.080", "1500.00", 0))
        self.assertEqual(saved["sync"]["queued"], 17)
        code, err = call(self.b, "/api/sales", {"fuel_type": "Diesel", "amount_pesos": "100"})
        self.assertEqual(code, 400)

    def test_4_reconcile_preview(self):
        code, r = call(self.b, "/api/reconcile", {"amount_pesos": "1000", "price_per_liter": "64.99"})
        self.assertEqual(r["liters"], "15.387")

    def test_5_cash_check_math(self):
        # after test_3: sales total = 16350 + 1500 = 17850
        code, c = call(self.b, "/api/cashcheck", {"declared": "17000", "opening_float": "1000", "noncash": "500",
                                                  "lang": "tl"})
        r = c["result"]
        self.assertEqual((r["sales_total"], r["expected"], r["diff"], r["diff_pct"], r["status"]),
                         ("17850.00", "18350.00", "-1350.00", "-7.36", "SHORT"))
        self.assertIn("kulang", c["explanation"])
        self.assertIn("₱1,350.00", c["explanation"])
        code, c = call(self.b, "/api/cashcheck", {"declared": "17852"})
        self.assertEqual(c["result"]["status"], "OK")
        self.assertEqual(call(self.b, "/api/cashcheck", {"declared": ""})[0], 400)

    def test_6_ask_tagalog_and_english(self):
        code, a = call(self.b, "/api/ask", {"question": "Magkano ang benta ng diesel ngayon?"})
        self.assertEqual(a["lang"], "tl")
        self.assertIn("Diesel", a["answer"])
        self.assertIn("₱10,000.00", a["answer"])  # 1000+2500+1500+3000+800+1200
        self.assertEqual(a["unverified_numbers"], [])
        code, a = call(self.b, "/api/ask", {"question": "What are total sales this shift?"})
        self.assertEqual(a["lang"], "en")
        self.assertIn("₱17,850.00", a["answer"])
        code, a = call(self.b, "/api/ask", {"question": "May kulang ba sa cash?"})
        self.assertIn("Inaasahan", a["answer"])

    def test_7_void_and_sync_offline(self):
        code, sh = call(self.b, "/api/shift")
        sid = sh["sales"][0]["id"]
        call(self.b, "/api/sales/void", {"id": sid})
        code, sh = call(self.b, "/api/shift")
        self.assertEqual(sh["total_amount"], "16350.00")
        code, s = call(self.b, "/api/sync", {})
        self.assertFalse(s["online"])
        self.assertTrue(s["label"].startswith("Offline"))
        self.assertGreater(s["queued"], 0)

    def test_8_new_shift(self):
        code, j = call(self.b, "/api/shift/new", {"attendant": "Juan"})
        code, sh = call(self.b, "/api/shift")
        self.assertEqual((sh["count"], sh["shift"]["attendant"]), (0, "Juan"))
        self.assertEqual(call(self.b, "/api/nope", {})[0], 404)


class SyncTest(unittest.TestCase):
    def test_sync_success_then_offline(self):
        srv, url = start_fake(FakeSync)
        s = Server(MOCK_AI="1", SYNC_URL=url + "/ingest")
        try:
            code, st = call(s.base, "/api/sync", {})
            self.assertTrue(st["online"])
            self.assertEqual(st["queued"], 0)
            self.assertEqual(st["label"], "Online, all synced")
            recs = FakeSync.received[-1]["records"]
            self.assertEqual((len(recs["sales"]), len(recs["shifts"])), (15, 1))
            call(s.base, "/api/sales", {"fuel_type": "Diesel", "amount_pesos": "500", "price_per_liter": "57.40"})
            srv.shutdown()
            srv.server_close()
            code, st = call(s.base, "/api/sync", {})
            self.assertFalse(st["online"])
            self.assertEqual(st["label"], "Offline, 1 records queued")
        finally:
            s.stop()


class RealAIPathTest(unittest.TestCase):
    """MOCK_AI off: the real Ollama HTTP code path against a fake Ollama, then with Ollama down."""

    def test_fake_ollama(self):
        srv, url = start_fake(FakeOllama)
        s = Server(MOCK_AI="0", OLLAMA_URL=url)
        try:
            code, st = call(s.base, "/api/status")
            self.assertTrue(st["ai"]["ok"])
            code, ex = call(s.base, "/api/extract", {"image": image_b64()})
            self.assertEqual(ex["fields"], {"fuel_type": "Diesel", "liters": "34.843", "price_per_liter": "57.40",
                                            "amount_pesos": "2000.00"})
            sent = FakeOllama.last_chat
            self.assertEqual((sent["model"], sent["format"], sent["stream"]), ("gemma3:4b", "json", False))
            self.assertFalse(sent["messages"][0]["images"][0].startswith("data:"))
            code, a = call(s.base, "/api/ask", {"question": "Magkano ang benta ng diesel ngayon?"})
            self.assertEqual(a["source"], "ai")
            self.assertIn("Diesel: 6 sales", FakeOllama.last_chat["messages"][0]["content"])
            self.assertEqual(a["unverified_numbers"], ["99,999.00"])
            code, c = call(s.base, "/api/cashcheck", {"declared": "16000", "lang": "tl"})
            self.assertEqual((c["explanation_source"], c["result"]["diff"]), ("ai", "-350.00"))
        finally:
            s.stop()
            srv.shutdown()
            srv.server_close()

    def test_ollama_down_nothing_breaks(self):
        s = Server(MOCK_AI="0", OLLAMA_URL="http://127.0.0.1:%d" % free_port())
        try:
            code, st = call(s.base, "/api/status")
            self.assertFalse(st["ai"]["ok"])
            self.assertIn("Ollama", st["ai"]["detail"])
            code, ex = call(s.base, "/api/extract", {"image": image_b64()})
            self.assertEqual(code, 503)
            self.assertEqual(ex["fields"]["liters"], None)
            code, a = call(s.base, "/api/ask", {"question": "Total sales?"})
            self.assertEqual((a["source"], "₱16,350.00" in a["answer"]), ("template-fallback", True))
            code, c = call(s.base, "/api/cashcheck", {"declared": "16350"})
            self.assertEqual((c["result"]["status"], c["explanation_source"]), ("OK", "template-fallback"))
            code, saved = call(s.base, "/api/sales", {"fuel_type": "Premium", "liters": "5", "price_per_liter": "64.99"})
            self.assertEqual(saved["sale"]["amount_pesos"], "324.95")
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
