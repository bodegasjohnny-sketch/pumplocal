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
import vision

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
STATIC = os.path.join(core.HERE, "static")
SAMPLES = os.path.join(core.HERE, "samples")
MAX_BODY = 20 * 1024 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}


def pump_payload(tolerance_pct=None):
    check = core.pump_check(tolerance_pct=tolerance_pct)
    return {"check": check, "pumps": core.list_pumps(), "tolerance_pct": check["tolerance_pct"],
            "fuels": core.FUELS}


def status_payload():
    return {"ai": ai.status(), "vision": vision.status(), "reader": core.READER, "sync": sync.status(),
            "station": core.STATION}


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
            pump = [f for f in files if "totalizer" in f.lower()]
            other = [f for f in files if f.lower().startswith("closing_sheet")]
            # "samples" = sale photos (Photo tab); "pump_samples" = totalizer photos (Pump tab);
            # "other_samples" = e.g. the closing-sheet form images (not a single sale)
            return self.send_json({"samples": [f for f in files if f not in pump and f not in other],
                                   "pump_samples": pump, "other_samples": other})
        if path == "/api/status":
            return self.send_json(status_payload())
        if path == "/api/shift":
            return self.send_json(core.shift_summary())
        if path == "/api/pump":
            return self.send_json(pump_payload())
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
                    return self.send_json(ai.read_photo(img, data.get("image_full") or None))
                except ai.AIError as e:
                    return self.send_json({
                        "ok": False, "error": str(e), "reader": "gemma", "source": ai.model_label(),
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
            if path == "/api/cash/count":
                count, errors = core.count_cash(data.get("counts") or data)
                if errors:
                    return self.send_json({"error": " ".join(errors)}, 400)
                return self.send_json(count)
            if path == "/api/cashcheck":
                count = None
                if data.get("counts"):
                    count, errors = core.count_cash(data["counts"])
                    if errors:
                        return self.send_json({"error": " ".join(errors)}, 400)
                declared = data.get("declared")
                if core.dec(declared) is None and count is not None:
                    declared = count["total"]  # the denomination count fills in declared cash
                if core.dec(declared) is None:
                    return self.send_json({"error": "Enter the declared cash amount (or count the bills)."}, 400)
                s = core.shift_summary()
                result = core.compute_cash(s["total_amount"], declared, data.get("opening_float"),
                                           data.get("noncash"), discounts=s["discounts_total"],
                                           credit=s["credit_total"], expenses=s["expenses_total"])
                lang = "tl" if data.get("lang") == "tl" else "en"
                text, source = ai.cash_explanation(result, lang)
                saved = core.save_cash_check(result, text, source, s["shift"]["id"],
                                             json.dumps(count) if count else "")
                if count is not None:
                    result["count"] = count
                pump = s["pump_check"]  # pump meters vs sales, shown in the same summary (code + template)
                return self.send_json({"result": result, "explanation": text, "explanation_source": source,
                                       "record": saved, "sync": sync.status(), "pump_check": pump,
                                       "pump_text": ai.template_pump_text(pump, lang)})
            if path == "/api/expenses":
                rec, errors = core.save_expense(data)
                if errors:
                    return self.send_json({"error": " ".join(errors)}, 400)
                return self.send_json({"expense": rec, "sync": sync.status()})
            if path == "/api/expenses/void":
                core.void_expense(data.get("id"))
                return self.send_json({"ok": True, "sync": sync.status()})
            if path == "/api/expense/extract":
                img = data.get("image") or ""
                if len(img) < 100:
                    return self.send_json({"error": "No image received."}, 400)
                try:
                    return self.send_json(ai.read_expense(img, data.get("image_full") or None))
                except ai.AIError as e:
                    return self.send_json({"ok": False, "error": str(e), "amount_pesos": None, "reader": "gemma",
                                           "message": "Local AI unavailable. Please type the amount."}, 503)
            if path == "/api/credit":
                rec, errors, calc = core.save_sale(dict(data, payment="credit"))
                if errors:
                    return self.send_json({"error": " ".join(errors), "calc": calc}, 400)
                return self.send_json({"sale": rec, "calc": calc, "sync": sync.status()})
            if path == "/api/pump/extract":
                img = data.get("image") or ""
                if len(img) < 100:
                    return self.send_json({"error": "No image received."}, 400)
                try:
                    return self.send_json(ai.read_totalizer(img, data.get("image_full") or None))
                except ai.AIError as e:
                    return self.send_json({
                        "ok": False, "error": str(e), "reader": "gemma", "source": ai.model_label(), "reading": None,
                        "message": "Local AI unavailable. You can still type the reading and save."}, 503)
            if path == "/api/pump/save":
                pump, errors = core.save_pump(data)
                if errors:
                    return self.send_json({"error": " ".join(errors)}, 400)
                return self.send_json(dict(pump_payload(), pump=pump))
            if path == "/api/pump/reading":
                rec, errors, warnings = core.save_reading(data.get("pump_id"), data.get("kind"), data.get("amount"),
                                                          data.get("volume"), data.get("source") or "manual")
                if errors:
                    return self.send_json({"error": " ".join(errors)}, 400)
                return self.send_json(dict(pump_payload(), reading=rec, warnings=warnings))
            if path == "/api/pump/settings":
                tol = core.dec(data.get("tolerance_pct"))
                if tol is None or tol < 0 or tol > 100:
                    return self.send_json({"error": "Tolerance must be a percent between 0 and 100."}, 400)
                core.set_setting("pump_tolerance_pct", str(tol))
                return self.send_json(pump_payload())
            if path == "/api/pump/price_change":
                errors = core.set_price_change(data.get("fuel_type"), data.get("old_price"), data.get("new_price"))
                if errors:
                    return self.send_json({"error": " ".join(errors)}, 400)
                return self.send_json(pump_payload())
            if path == "/api/pump/check":
                payload = pump_payload()
                lang = "tl" if data.get("lang") == "tl" else "en"
                text, source = ai.pump_explanation(payload["check"], lang)
                return self.send_json(dict(payload, explanation=text, explanation_source=source))
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
    else:
        seed.seed_pumps_if_demo()  # older demo databases get the Diesel 2 totalizer demo too
    sync.start_background()
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        sys.exit("Port %d is busy (%s). Try: PORT=8081 python3 app.py" % (PORT, e))
    url = "http://127.0.0.1:%d" % PORT
    a = ai.status(force=True)
    print("PumpLocal running at %s" % url)
    print("  Local AI: %s (%s)" % (a["detail"], core.MODEL))
    print("  Photo reader: READER=%s" % core.READER)
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
    if core.READER != "gemma":
        # Check / compile the Apple Vision OCR helper now (first run compiles Swift) so photos are fast.
        threading.Thread(target=vision.ensure, daemon=True).start()
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
