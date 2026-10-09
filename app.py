#!/usr/bin/env python3
"""PumpLocal - offline AI assistant for small Filipino gas stations.

Run:  python3 app.py      -> http://localhost:8080
Standard library only. Needs Ollama with gemma3:4b for the AI parts (or MOCK_AI=1).
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai
import core
import seed
import sync

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
STATIC = os.path.join(core.HERE, "static")
SAMPLES = os.path.join(core.HERE, "samples")
MAX_BODY = 20 * 1024 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}


def status_payload():
    return {"ai": ai.status(), "sync": sync.status(), "station": core.STATION}


class Handler(BaseHTTPRequestHandler):
    server_version = "PumpLocal/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("QUIET") != "1":
            sys.stderr.write("[%s] %s\n" % (core.now(), fmt % args))

    # ------------------------------------------------------------ helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in TYPES or not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES[ext])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Request too large")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    # ------------------------------------------------------------ routes
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC, "index.html"))
        if path.startswith("/samples/"):
            name = os.path.basename(path)
            return self.send_file(os.path.join(SAMPLES, name))
        if path == "/api/samples":
            files = sorted(f for f in os.listdir(SAMPLES) if f.lower().endswith((".png", ".jpg")))
            return self.send_json({"samples": files})
        if path == "/api/status":
            return self.send_json(status_payload())
        if path == "/api/shift":
            return self.send_json(core.shift_summary())
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            data = self.read_json()
        except ValueError as e:
            return self.send_json({"error": "Bad request: %s" % e}, 400)
        try:
            if path == "/api/extract":
                img = data.get("image") or ""
                if len(img) < 100:
                    return self.send_json({"error": "No image received."}, 400)
                try:
                    return self.send_json(ai.extract(img))
                except ai.AIError as e:
                    return self.send_json({
                        "ok": False, "error": str(e),
                        "fields": {"fuel_type": "", "liters": None, "price_per_liter": None, "amount_pesos": None},
                        "message": "Local AI unavailable. You can still type the values and save."}, 503)
            if path == "/api/reconcile":
                return self.send_json(core.reconcile(data.get("liters"), data.get("price_per_liter"),
                                                     data.get("amount_pesos")))
            if path == "/api/sales":
                rec, errors, calc = core.save_sale(data)
                if errors:
                    return self.send_json({"error": " ".join(errors), "calc": calc}, 400)
                return self.send_json({"sale": rec, "calc": calc, "sync": sync.status()})
            if path == "/api/sales/void":
                core.void_sale(data.get("id"))
                return self.send_json({"ok": True, "sync": sync.status()})
            if path == "/api/cashcheck":
                if core.dec(data.get("declared")) is None:
                    return self.send_json({"error": "Enter the declared cash amount."}, 400)
                s = core.shift_summary()
                result = core.compute_cash(s["total_amount"], data.get("declared"), data.get("opening_float"),
                                           data.get("noncash"))
                lang = "tl" if data.get("lang") == "tl" else "en"
                text, source = ai.cash_explanation(result, lang)
                saved = core.save_cash_check(result, text, source, s["shift"]["id"])
                return self.send_json({"result": result, "explanation": text, "explanation_source": source,
                                       "record": saved, "sync": sync.status()})
            if path == "/api/ask":
                q = (data.get("question") or "").strip()[:500]
                if not q:
                    return self.send_json({"error": "Type a question."}, 400)
                return self.send_json(ai.ask(q, core.shift_summary()))
            if path == "/api/sync":
                return self.send_json(sync.sync_now())
            if path == "/api/shift/new":
                shift = core.new_shift(data.get("attendant") or "")
                return self.send_json({"shift": shift, "sync": sync.status()})
        except Exception as e:  # never crash the server during a demo
            return self.send_json({"error": "Server error: %s" % e}, 500)
        return self.send_json({"error": "not found"}, 404)


def main():
    core.init_db()
    if core.is_empty():
        seed.seed()
    sync.start_background()
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        sys.exit("Port %d is busy (%s). Try: PORT=8081 python3 app.py" % (PORT, e))
    url = "http://127.0.0.1:%d" % PORT
    a = ai.status(force=True)
    print("PumpLocal running at %s" % url)
    print("  Local AI: %s (%s)" % (a["detail"], core.MODEL))
    print("  Cloud sync: %s" % sync.status()["label"])
    print("  Press Ctrl+C to stop.", flush=True)
    if a.get("ok") and not a.get("mock"):
        def _warm():
            try:
                print("  Warming up the local model (first load can take a few minutes)...", flush=True)
                ai.chat([{"role": "user", "content": "hi"}], num_predict=1)
                print("  Local model loaded and ready.", flush=True)
            except Exception as e:
                print("  Warm-up failed: %s" % e, flush=True)
        threading.Thread(target=_warm, daemon=True).start()
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
