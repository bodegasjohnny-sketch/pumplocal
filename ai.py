"""Local AI layer: talks to Ollama on this machine (default gemma3:4b).

The model only (a) reads photos into fields and (b) words short text.
It never does the math: numbers are computed in core.py and passed in as context.
Set MOCK_AI=1 to run without Ollama (used for testing).
"""
import base64
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request

import core
import meterparse
import totalizer
import vision

EXTRACT_PROMPT = (
    "Read this fuel pump meter or fuel receipt. Reply with JSON only: "
    '{"fuel_type": "Premium|Unleaded|Diesel or null", "liters": number or null, '
    '"price_per_liter": number or null, "amount_pesos": number or null}. '
    "Use null for anything not clearly visible. Do not guess."
)

TAGALOG_WORDS = {
    "magkano", "ilan", "ilang", "ang", "ng", "ngayon", "benta", "litro", "mga", "ba", "po", "sa",
    "kabuuan", "lahat", "kulang", "sobra", "pera", "naibenta", "nabenta", "anong", "ano", "kita",
    "kahapon", "ito", "yung", "naman", "na", "pa", "kami", "natin", "paano", "bakit", "utang", "gastos",
    "magkanong", "sino", "diskwento",
}


class AIError(Exception):
    pass


class AIStatus(object):
    checked_at = 0.0
    ok = False
    busy = False
    detail = ""
    last_good_at = 0.0  # last time /api/tags answered and the model was present


_inflight = [0]
_inflight_lock = threading.Lock()


def inflight():
    return _inflight[0]


def model_label():
    """Human name of the Ollama model, e.g. 'Gemma 3 4B via Ollama (gemma3:4b)'."""
    if core.MOCK_AI:
        return "Mock reader (MOCK_AI=1)"
    if core.MODEL == "gemma3:4b":
        return "Gemma 3 4B via Ollama (gemma3:4b)"
    return "%s via Ollama" % core.MODEL


def _is_timeout(e):
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True
    reason = getattr(e, "reason", None)
    return isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower()


def detect_lang(text):
    words = re.findall(r"[a-zA-Z]+", (text or "").lower())
    hits = sum(1 for w in words if w in TAGALOG_WORDS)
    return "tl" if hits >= 2 or (hits == 1 and len(words) <= 3) else "en"


def status(force=False):
    """Is Ollama reachable and is the model pulled? Cached for STATUS_CACHE (5 s).

    While Ollama is running a model on an 8 GB Mac, /api/tags can be slow to answer. A timeout while we
    have a request in flight (or shortly after a good check) means "busy", not "not running".
    Only a refused connection (nothing listening) is reported as "Ollama not running".
    """
    if core.MOCK_AI:
        return {"ok": True, "mock": True, "busy": False, "model": core.MODEL,
                "detail": "MOCK_AI=1 (no Ollama needed)"}
    if force or time.time() - AIStatus.checked_at > core.STATUS_CACHE:
        AIStatus.checked_at = time.time()
        busy_now = inflight() > 0
        try:
            with urllib.request.urlopen(core.OLLAMA_URL + "/api/tags", timeout=core.STATUS_TIMEOUT) as r:
                names = [m.get("name", "") for m in json.loads(r.read().decode()).get("models", [])]
            want = core.MODEL if ":" in core.MODEL else core.MODEL + ":latest"
            if want in names:
                AIStatus.ok, AIStatus.busy = True, busy_now
                AIStatus.detail = "Local AI busy" if busy_now else "Local AI ready"
                AIStatus.last_good_at = time.time()
            else:
                AIStatus.ok, AIStatus.busy = False, False
                AIStatus.detail = "Model not found. Run: ollama pull %s" % core.MODEL
        except Exception as e:
            recent_good = time.time() - AIStatus.last_good_at < 300
            if _is_timeout(e) and (busy_now or recent_good):
                # Ollama is up but busy running the model: keep the last good status.
                AIStatus.ok, AIStatus.busy, AIStatus.detail = True, True, "Local AI busy"
            elif busy_now and recent_good and not isinstance(getattr(e, "reason", None), ConnectionRefusedError):
                AIStatus.ok, AIStatus.busy, AIStatus.detail = True, True, "Local AI busy"
            else:
                AIStatus.ok, AIStatus.busy = False, False
                AIStatus.detail = "Ollama not running. Open the Ollama app or run: ollama serve"
    return {"ok": AIStatus.ok, "mock": False, "busy": AIStatus.busy, "model": core.MODEL,
            "detail": AIStatus.detail}


def chat(messages, json_mode=False, num_predict=200):
    payload = {
        "model": core.MODEL, "messages": messages, "stream": False, "keep_alive": "15m",
        # Small context + short outputs keep memory and latency low on an 8 GB Mac.
        "options": {"temperature": 0, "num_ctx": 2048, "num_predict": num_predict},
    }
    if json_mode:
        payload["format"] = "json"
    req = urllib.request.Request(core.OLLAMA_URL + "/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with _inflight_lock:
        _inflight[0] += 1
    try:
        with urllib.request.urlopen(req, timeout=core.AI_TIMEOUT) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AIError("Ollama error %s: %s" % (e.code, e.read().decode(errors="replace")[:200]))
    except Exception as e:
        raise AIError("Local AI not reachable (%s). Is Ollama running?" % e.__class__.__name__)
    finally:
        with _inflight_lock:
            _inflight[0] -= 1
    return (body.get("message") or {}).get("content", "")


# ---------------------------------------------------------------- 1) photo -> fields
def parse_json_text(text):
    """Pull the first JSON object out of model text (handles ```json fences, chatter)."""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    for candidate in (chunk, re.sub(r",\s*}", "}", chunk).replace("'", '"')):
                        try:
                            obj = json.loads(candidate)
                            if isinstance(obj, dict):
                                return obj
                        except ValueError:
                            pass
                    break
        start = text.find("{", start + 1)
    return None


def fields_from_obj(obj):
    def pick(*keys):
        for k in keys:
            for actual in obj:
                if actual.lower().replace(" ", "_") == k:
                    v = obj[actual]
                    if v not in (None, "", "null"):
                        return v
        return None
    fuel = core.normalize_fuel(pick("fuel_type", "fuel", "product"))
    liters = core.dec(pick("liters", "litres", "volume", "liter"))
    price = core.dec(pick("price_per_liter", "price", "unit_price", "price_per_litre"))
    amount = core.dec(pick("amount_pesos", "amount", "total", "sale", "pesos"))
    return {"fuel_type": fuel or "", "liters": liters, "price_per_liter": price, "amount_pesos": amount}


MOCK_EXTRACT = ('Sure! Here is what I can read from the meter:\n```json\n'
                '{"fuel_type": "PREMIUM", "liters": null, "price_per_liter": "64.99", '
                '"amount_pesos": "₱1,000.00"}\n```')


def extract(image_b64):
    if "," in image_b64[:100] and image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]
    t0 = time.time()
    if core.MOCK_AI:
        raw = MOCK_EXTRACT
    else:
        raw = chat([{"role": "user", "content": EXTRACT_PROMPT, "images": [image_b64]}], json_mode=True,
                   num_predict=120)
    obj = parse_json_text(raw)
    if obj is None:
        return {"ok": False, "fields": {"fuel_type": "", "liters": None, "price_per_liter": None,
                                        "amount_pesos": None},
                "raw": raw[:500], "notes": [], "warnings": [],
                "message": "Could not read values from the photo. Please type them in.",
                "seconds": round(time.time() - t0, 1), "reader": "gemma", "source": model_label()}
    f = fields_from_obj(obj)
    rec = core.reconcile(f["liters"], f["price_per_liter"], f["amount_pesos"])
    fields = {"fuel_type": f["fuel_type"], "liters": rec["liters"], "price_per_liter": rec["price_per_liter"],
              "amount_pesos": rec["amount_pesos"]}
    missing = [k for k, v in fields.items() if not v]
    msg = "Read from photo. Please check every value before saving."
    if missing:
        msg = "Some values were not readable (%s). Please fill them in." % ", ".join(missing)
    return {"ok": True, "fields": fields, "raw": raw[:500], "notes": rec["notes"], "warnings": rec["warnings"],
            "message": msg, "seconds": round(time.time() - t0, 1), "reader": "gemma", "source": model_label()}


VISION_SOURCE = "Apple Vision (on-device)"


def _decode_image(image):
    suffix = ".jpg"
    if image.startswith("data:"):
        head, image = image.split(",", 1)
        if "png" in head:
            suffix = ".png"
    return base64.b64decode(image), suffix


def _vision_result(p, lines, seconds):
    f = p["fields"]
    rec = core.reconcile(f["liters"], f["price_per_liter"], f["amount_pesos"])
    fields = {"fuel_type": f["fuel_type"], "liters": f["liters"], "price_per_liter": f["price_per_liter"],
              "amount_pesos": f["amount_pesos"]}
    found_any = any(fields[k] for k in ("liters", "price_per_liter", "amount_pesos"))
    if p["consistent"]:
        msg = "Read from photo. Please check every value before saving."
    elif found_any:
        msg = "Some values were not readable or don't add up. Please check and fill them in."
    else:
        msg = "Could not read values from the photo. Please type them in."
    if p["consistent"] and not fields["fuel_type"]:
        msg += " Choose the fuel type."
    notes = p["notes"] + [n for n in rec["notes"] if n not in p["notes"]]
    return {"ok": found_any, "fields": fields, "raw": "\n".join(ln.get("text", "") for ln in lines)[:500],
            "notes": notes, "warnings": rec["warnings"], "message": msg, "seconds": round(seconds, 1),
            "reader": "vision", "source": VISION_SOURCE, "consistent": p["consistent"]}


def _report_result(lines, why, seconds):
    return {"ok": False, "not_single": True, "reason": why,
            "fields": {"fuel_type": "", "liters": None, "price_per_liter": None, "amount_pesos": None},
            "raw": "\n".join(ln.get("text", "") for ln in lines)[:500], "notes": [], "warnings": [],
            "message": meterparse.REPORT_MESSAGE + " · " + meterparse.REPORT_MESSAGE_TL,
            "message_en": meterparse.REPORT_MESSAGE, "message_tl": meterparse.REPORT_MESSAGE_TL,
            "seconds": round(seconds, 1), "reader": "vision", "source": VISION_SOURCE}


def read_photo(image, image_full=None):
    """Photo -> fields. READER=auto: Apple Vision OCR + code parsing first; Gemma only if OCR is
    unavailable or its numbers don't add up. READER=vision: OCR only. READER=gemma: Gemma only."""
    reader = core.READER
    fallback_reason, partial = None, None
    if reader in ("auto", "vision"):
        t0 = time.time()
        try:
            data, suffix = _decode_image(image_full or image)
            lines = vision.run_image_bytes(data, suffix)
            why = meterparse.report_reason(lines)
            if why:  # a whole closing sheet / report: don't guess a sale, and don't hand it to Gemma either
                return _report_result(lines, why, time.time() - t0)
            p = meterparse.parse(lines)
            res = _vision_result(p, lines, time.time() - t0)
            if p["consistent"] or reader == "vision":
                return res
            partial = res
            fallback_reason = ("Apple Vision read the photo in %.1fs but the numbers were incomplete or didn't "
                               "add up (liters × price ≠ amount), so Gemma read it instead." % (time.time() - t0))
        except vision.OCRError as e:
            if reader == "vision":
                return {"ok": False, "fields": {"fuel_type": "", "liters": None, "price_per_liter": None,
                                                "amount_pesos": None},
                        "raw": "", "notes": [], "warnings": [], "reader": "vision", "source": VISION_SOURCE,
                        "message": "Apple Vision OCR could not read the photo (%s). Please type the values." % e,
                        "seconds": round(time.time() - t0, 1)}
            if vision.status()["available"]:  # OCR exists but failed on this photo: tell staff why it's slow
                fallback_reason = "Apple Vision OCR failed (%s), so Gemma read it instead." % e
        except (ValueError, TypeError) as e:
            fallback_reason = "Could not decode the image for OCR (%s)." % e
    try:
        res = extract(image)
    except AIError as e:
        if partial and partial["ok"]:
            partial["warnings"].append("Gemma fallback unavailable (%s). These values are from Apple Vision only "
                                       "and don't add up. Please check them." % e)
            return partial
        raise
    if fallback_reason:
        res["fallback_reason"] = fallback_reason
    return res


# ---------------------------------------------------------------- 1b) totalizer photo -> reading
TOTALIZER_PROMPT = (
    "This photo shows a fuel pump's totalizer (lifetime counter) screen. A pump has a MONEY (pesos) counter and a "
    "VOLUME (liters) counter. Reply with JSON only: "
    '{"screen_title": "the title line, e.g. 2.Money All, or null", '
    '"money": "the money/peso counter digits exactly as shown, or null", '
    '"volume": "the volume/liter counter digits exactly as shown, or null", '
    '"pump_label": "pump or fuel label such as DIESEL 2, or null"}. '
    "If only one counter is shown, use the screen title to decide which it is. Use null if not clearly visible. "
    "Do not guess."
)
MOCK_TOTALIZER = ('Here is the totalizer:\n```json\n{"screen_title": "2.Money All", "money": "775397", '
                  '"volume": null, "pump_label": "DIESEL 2"}\n```')


def _totalizer_result(p, raw, seconds, reader, source):
    ok = p["reading"] is not None
    if ok:
        msg = "Read from photo. Check the numbers, choose the pump and Opening/Closing, then save."
    else:
        msg = "Could not find a totalizer number in the photo. Please type it in."
    return {"ok": ok, "reading": p["reading"], "amount": p["amount"], "volume": p["volume"],
            "unassigned": p["unassigned"], "screen": p["screen"], "pump_name": p["pump_name"],
            "fuel_type": p["fuel_type"], "confidence": p["confidence"], "notes": p["notes"],
            "raw": (raw or "")[:500], "message": msg, "seconds": round(seconds, 1), "reader": reader,
            "source": source}


def extract_totalizer(image_b64):
    """Gemma reads the totalizer; the reply is still parsed and checked by totalizer.py (code)."""
    if image_b64.startswith("data:") and "," in image_b64[:100]:
        image_b64 = image_b64.split(",", 1)[1]
    t0 = time.time()
    if core.MOCK_AI:
        raw = MOCK_TOTALIZER
    else:
        raw = chat([{"role": "user", "content": TOTALIZER_PROMPT, "images": [image_b64]}], json_mode=True,
                   num_predict=80)
    obj = parse_json_text(raw) or {}
    lines = []
    have = lambda k: obj.get(k) not in (None, "", "null")
    if have("money"):
        lines.append("Money %s" % obj["money"])
    if have("volume"):
        lines.append("Volume %s" % obj["volume"])
    if have("reading"):  # older reply shape
        lines.append("Total %s" % obj["reading"])
    if obj.get("pump_label") not in (None, "", "null"):
        lines.append(str(obj["pump_label"]))
    p = totalizer.parse(lines)
    return _totalizer_result(p, raw, time.time() - t0, "gemma", model_label())


def read_totalizer(image, image_full=None):
    """Totalizer photo -> reading. Same OCR-first pipeline as sales photos: Apple Vision + code first; Gemma only
    if OCR is unavailable or finds no labelled number (READER=auto|vision|gemma)."""
    reader = core.READER
    fallback_reason, partial = None, None
    if reader in ("auto", "vision"):
        t0 = time.time()
        try:
            data, suffix = _decode_image(image_full or image)
            lines = vision.run_image_bytes(data, suffix)
            p = totalizer.parse(lines)
            res = _totalizer_result(p, "\n".join(ln.get("text", "") for ln in lines), time.time() - t0,
                                    "vision", VISION_SOURCE)
            if (p["reading"] and p["confidence"] in ("labelled", "nearby")) or reader == "vision":
                return res
            partial = res
            fallback_reason = ("Apple Vision read the photo in %.1fs but found no Volume/Total number, so Gemma read "
                               "it instead." % (time.time() - t0))
        except vision.OCRError as e:
            if reader == "vision":
                p = totalizer.parse([])
                res = _totalizer_result(p, "", time.time() - t0, "vision", VISION_SOURCE)
                res["message"] = "Apple Vision OCR could not read the photo (%s). Please type the reading." % e
                return res
            if vision.status()["available"]:
                fallback_reason = "Apple Vision OCR failed (%s), so Gemma read it instead." % e
        except (ValueError, TypeError) as e:
            fallback_reason = "Could not decode the image for OCR (%s)." % e
    try:
        res = extract_totalizer(image)
    except AIError:
        if partial and partial["ok"]:
            partial["notes"].append("Gemma fallback unavailable; this number is the OCR's best guess. Please check it.")
            return partial
        raise
    if fallback_reason:
        res["fallback_reason"] = fallback_reason
    return res


# ---------------------------------------------------------------- 1c) expense receipt photo -> TOTAL amount
EXPENSE_PROMPT = (
    "This is a store receipt for a small expense. Reply with JSON only: "
    '{"total": the TOTAL amount paid in pesos as a number (not cash tendered, not change, not subtotal), '
    '"store": "store name or null"}. Use null if not clearly visible. Do not guess.'
)
MOCK_EXPENSE = 'Receipt:\n```json\n{"total": "₱350.00", "store": "Demo Hardware"}\n```'


def _expense_result(amount, store, raw, seconds, reader, source, label=None):
    ok = amount is not None
    return {"ok": ok, "amount_pesos": amount, "description": store or "", "label": label,
            "raw": (raw or "")[:500], "seconds": round(seconds, 1), "reader": reader, "source": source,
            "message": ("Read the TOTAL from the receipt. Check it and add what it was for, then save." if ok else
                        "Could not find the TOTAL on the receipt. Please type the amount.")}


def extract_expense(image_b64):
    if image_b64.startswith("data:") and "," in image_b64[:100]:
        image_b64 = image_b64.split(",", 1)[1]
    t0 = time.time()
    raw = MOCK_EXPENSE if core.MOCK_AI else chat(
        [{"role": "user", "content": EXPENSE_PROMPT, "images": [image_b64]}], json_mode=True, num_predict=60)
    obj = parse_json_text(raw) or {}
    total = core.dec(obj.get("total"))
    amount = str(core.q2(total)) if total is not None and total > 0 else None
    store = obj.get("store") if obj.get("store") not in (None, "", "null") else ""
    return _expense_result(amount, str(store)[:60], raw, time.time() - t0, "gemma", model_label())


def read_expense(image, image_full=None):
    """Expense receipt -> TOTAL amount. Apple Vision OCR + code first, Gemma only as fallback."""
    reader = core.READER
    fallback_reason, partial = None, None
    if reader in ("auto", "vision"):
        t0 = time.time()
        try:
            data, suffix = _decode_image(image_full or image)
            lines = vision.run_image_bytes(data, suffix)
            r = meterparse.receipt_total(lines)
            res = _expense_result(r["amount"], r["store"], "\n".join(ln.get("text", "") for ln in lines),
                                  time.time() - t0, "vision", VISION_SOURCE, r["label"])
            if r["amount"] or reader == "vision":
                return res
            partial = res
            fallback_reason = "Apple Vision found no TOTAL on the receipt, so Gemma read it instead."
        except vision.OCRError as e:
            if reader == "vision":
                return _expense_result(None, "", "", time.time() - t0, "vision", VISION_SOURCE)
            if vision.status()["available"]:
                fallback_reason = "Apple Vision OCR failed (%s), so Gemma read it instead." % e
        except (ValueError, TypeError) as e:
            fallback_reason = "Could not decode the image for OCR (%s)." % e
    try:
        res = extract_expense(image)
    except AIError:
        if partial:
            return partial
        raise
    if fallback_reason:
        res["fallback_reason"] = fallback_reason
    return res


# ---------------------------------------------------------------- 1d) pump vs sales wording
def template_pump_text(check, lang="en"):
    """Short wording for the pump check. All numbers come from core.pump_check."""
    groups = [g for g in (check or {}).get("groups", []) if g.get("status") != "INCOMPLETE"]
    if not groups:
        waiting = (check or {}).get("groups")
        if waiting:
            return " ".join(g["headline_tl" if lang == "tl" else "headline"] for g in waiting)
        return ("Wala pang reading ng metro ng pump ngayong shift. Ilagay ang opening at closing sa Pump tab."
                if lang == "tl" else
                "No pump meter readings yet this shift. Record opening and closing readings on the Pump tab.")
    out = []
    for g in groups:
        head = g["headline_tl" if lang == "tl" else "headline"]
        tol = g.get("tolerance_pct")
        pc = g.get("price")
        if pc and pc.get("ok") is False:
            head += " " + pc["text_tl" if lang == "tl" else "text"]
        if g["status"] == "UNACCOUNTED":
            head += (" Lampas sa %s%% na palugit: tingnan kung may bentang hindi pa naitala (o utang), at basahin ulit "
                     "ang metro." % tol if lang == "tl" else
                     " Over the %s%% tolerance: look for sales not yet recorded (or credit fill-ups), then re-read the "
                     "meter." % tol)
        elif g["status"] == "OVER_RECORDED":
            head += (" Lampas sa %s%% na palugit: tingnan kung may dobleng benta, at kung tama ang reading at decimals."
                     % tol if lang == "tl" else
                     " Over the %s%% tolerance: check for duplicate sales, and that the reading and decimals are "
                     "right." % tol)
        out.append(("%s: " % g["fuel_type"]) + head)
    return " ".join(out)


def pump_explanation(check, lang="en"):
    """Returns (text, source). The local model only words the code-computed result; template if unavailable."""
    if core.MOCK_AI:
        return template_pump_text(check, lang), "template"
    groups = [g for g in (check or {}).get("groups", []) if g.get("status") not in ("INCOMPLETE",)]
    if not groups:
        return template_pump_text(check, lang), "template"
    language = "Tagalog" if lang == "tl" else "English"
    facts = " ".join("%s: %s Status: %s (tolerance %s%%). %s" % (
        g["fuel_type"], g["headline"], g["status"], g["tolerance_pct"],
        (g["price"] or {}).get("text", "")) for g in groups)
    prompt = ("Gas station pump meter (totalizer) check vs recorded sales, numbers computed by the system: %s\n"
              "Write 1-2 short sentences in %s for the station owner: what this means and one practical next step. "
              "Use only these numbers. Do not calculate anything new." % (facts, language))
    try:
        text = chat([{"role": "user", "content": prompt}], num_predict=100).strip()
        if text:
            return text, "ai"
    except AIError:
        pass
    return template_pump_text(check, lang), "template-fallback"


# ---------------------------------------------------------------- 2) cash check wording
def template_cash_text(r, lang):
    diff = core.Decimal(r["diff"])
    pct = (" (%s%%)" % r["diff_pct"]) if r["diff_pct"] is not None else ""
    if r["status"] == "OK":
        return ("Tugma ang cash sa benta. Walang kailangang gawin." if lang == "tl"
                else "Cash matches recorded sales. No action needed.")
    amt = core.peso(abs(diff))
    if lang == "tl":
        word = "kulang" if r["status"] == "SHORT" else "sobra"
        return ("May %s na %s%s ang cash. Bilangin ulit ang pera at tingnan ang GCash/card slips "
                "at mga benta na hindi pa naitala." % (word, amt, pct))
    word = "short" if r["status"] == "SHORT" else "over"
    return ("Cash is %s by %s%s. Recount the cash and check GCash/card slips and any sales not yet "
            "recorded." % (word, amt, pct))


def cash_explanation(r, lang="en"):
    """Returns (text, source). Source is 'ai', 'template' (mock) or 'template-fallback'."""
    if core.MOCK_AI:
        return template_cash_text(r, lang), "template"
    language = "Tagalog" if lang == "tl" else "English"
    facts = ("Expected cash %s. Declared cash %s. Difference %s%s. Status: %s." % (
        core.peso(r["expected"]), core.peso(r["declared"]), core.peso(r["diff"]),
        (" (%s%%)" % r["diff_pct"]) if r["diff_pct"] else "", r["status"]))
    prompt = ("Gas station shift cash check (numbers computed by the system): %s\n"
              "Write 1-2 short sentences in %s for the station owner: what this means and one practical next "
              "step. Use only these numbers. Do not calculate anything new." % (facts, language))
    try:
        text = chat([{"role": "user", "content": prompt}], num_predict=90).strip()
        if text:
            return text, "ai"
    except AIError:
        pass
    return template_cash_text(r, lang), "template-fallback"


# ---------------------------------------------------------------- 3) ask
def summary_context(s):
    lines = ["Shift started %s. Sales recorded: %d." % (s["shift"]["opened_at"], s["count"])]
    for f in s["fuels"]:
        lines.append("%s: %d sales, %s liters, %s." % (f["fuel_type"], f["count"], f["liters"],
                                                        core.peso(f["amount"])))
    lines.append("TOTAL (gross sales): %s liters, %s." % (s["total_liters"], core.peso(s["total_amount"])))
    if "discounts_total" in s:
        reasons = ", ".join("%s %s" % (k or "other", core.peso(v)) for k, v in s["discounts_by_reason"].items())
        lines.append("Discounts: %s on %d sales%s." % (core.peso(s["discounts_total"]), s["discount_count"],
                                                       (" (" + reasons + ")") if reasons else ""))
        lines.append("Credit (utang) sales, not in the drawer: %s%s." % (core.peso(s["credit_total"]), "".join(
            "; %s: %s %s L, %s" % (c["customer"], c["fuel_type"], c["liters"], core.peso(c["amount"]))
            for c in s["credit_sales"])))
        lines.append("Expenses (petty cash out): %s%s." % (core.peso(s["expenses_total"]), "".join(
            "; %s %s" % (e["description"], core.peso(e["amount_pesos"])) for e in s["expenses"])))
    c = s.get("last_cash_check")
    if c:
        lines.append("Last cash check: expected %s (opening float %s + gross sales %s - discounts %s - credit %s - "
                     "expenses %s - GCash/card %s), declared %s, difference %s, status %s." % (
                         core.peso(c["expected"]), core.peso(c["opening_float"]), core.peso(c["sales_total"]),
                         core.peso(c.get("discounts") or 0), core.peso(c.get("credit_sales") or 0),
                         core.peso(c.get("expenses") or 0), core.peso(c["noncash"]), core.peso(c["declared"]),
                         core.peso(c["diff"]), c["status"]))
    else:
        lines.append("No cash check yet this shift.")
    p = s.get("pump_check")
    if p and p.get("groups"):
        lines.append("Pump meters (totalizer readings vs recorded sales; tolerance %s%%):" % p["tolerance_pct"])
        for r in p["pumps"]:
            if r["status"] == "NONE":
                continue
            lines.append("- %s (%s): peso totalizer opening %s, closing %s, dispensed %s; liter totalizer opening %s, "
                         "closing %s, dispensed %s." % (
                             r["name"], r["fuel_type"], r["opening_amount_value"] or "not recorded",
                             r["closing_amount_value"] or "not recorded", r["dispensed_amount_text"] or "n/a",
                             r["opening_volume_value"] or "not recorded", r["closing_volume_value"] or "not recorded",
                             r["dispensed_volume_text"] or "n/a"))
        for g in p["groups"]:
            lines.append("- %s pump check: %s Status: %s." % (g["fuel_type"], g["headline"], g["status"]))
            if g.get("price"):
                lines.append("- %s %s" % (g["fuel_type"], g["price"]["text"]))
    else:
        lines.append("No pump meter readings yet this shift.")
    return "\n".join(lines)


PUMP_WORDS = ("pump", "metro", "totalizer", "meter", "dispens", "nailabas", "lumabas")
GAP_WORDS = ("kulang", "sobra", "short", "missing", "nawawala", "unaccounted", "gap", "naitala", "nawala", "leak")


def _fuel_in(q):
    for word, name in (("diesel", "Diesel"), ("krudo", "Diesel"), ("premium", "Premium"), ("unleaded", "Unleaded"),
                       ("regular", "Unleaded"), ("gas", "Unleaded")):
        if word in q:
            return name
    return None


def pump_answer(question, s, lang):
    """Answer pump-meter questions like 'May kulang ba sa diesel?' from code-computed data, or None."""
    q = (question or "").lower()
    if any(w in q for w in ("cash", "pera", "kaha", "drawer")):
        return None
    pumpy, gappy = any(w in q for w in PUMP_WORDS), any(w in q for w in GAP_WORDS)
    fuel = _fuel_in(q)
    if not (pumpy or (gappy and fuel)):
        return None
    groups = ((s.get("pump_check") or {}).get("groups")) or []
    if fuel:
        groups = [g for g in groups if g["fuel_type"] == fuel]
    if not groups:
        return ("Wala pang reading ng metro ng pump%s ngayong shift. Ilagay ang opening at closing sa Pump tab." % (
            " para sa " + fuel if fuel else "") if lang == "tl" else
            "No pump meter readings%s this shift yet. Record opening and closing readings on the Pump tab." % (
                " for " + fuel if fuel else ""))
    out = []
    for g in groups:
        head = g["headline_tl" if lang == "tl" else "headline"]
        st = g["status"]
        if st == "UNACCOUNTED":
            out.append(("Oo. %s Lampas sa %s%% na palugit." if lang == "tl" else "Yes. %s Over the %s%% tolerance.")
                       % (head, g["tolerance_pct"]))
        elif st == "OVER_RECORDED":
            out.append(("%s Lampas sa %s%% na palugit; tingnan kung may dobleng benta." if lang == "tl" else
                        "%s Over the %s%% tolerance; check for duplicate sales.") % (head, g["tolerance_pct"]))
        elif st == "OK":
            out.append(("Wala. %s" if lang == "tl" else "No. %s") % head)
        else:
            out.append(head)
    return " ".join(out)


def template_answer(question, s, lang):
    q = (question or "").lower()
    pa = pump_answer(question, s, lang)
    if pa:
        return pa
    if "credit_total" in s:
        if any(w in q for w in ("utang", "credit", "charge", "pautang")):
            names = ", ".join("%s %s" % (c["customer"], core.peso(c["amount"])) for c in s["credit_sales"])
            if lang == "tl":
                return "Utang ngayong shift: %s (%d)%s." % (core.peso(s["credit_total"]), len(s["credit_sales"]),
                                                            (": " + names) if names else "")
            return "Credit (utang) sales this shift: %s (%d)%s." % (core.peso(s["credit_total"]),
                                                                     len(s["credit_sales"]), (": " + names) if names else "")
        if any(w in q for w in ("gastos", "expense", "petty", "ginastos", "nagastos")):
            items = ", ".join("%s %s" % (e["description"], core.peso(e["amount_pesos"])) for e in s["expenses"])
            if lang == "tl":
                return "Gastos ngayong shift: %s%s." % (core.peso(s["expenses_total"]), (": " + items) if items else "")
            return "Expenses this shift: %s%s." % (core.peso(s["expenses_total"]), (": " + items) if items else "")
        if any(w in q for w in ("discount", "diskwento", "senior", "pwd", "suki")):
            if lang == "tl":
                return "Diskwento ngayong shift: %s sa %d benta." % (core.peso(s["discounts_total"]), s["discount_count"])
            return "Discounts this shift: %s on %d sales." % (core.peso(s["discounts_total"]), s["discount_count"])
    if any(w in q for w in ("cash", "kulang", "sobra", "pera", "short", "over")):
        c = s.get("last_cash_check")
        if not c:
            return ("Wala pang cash check ngayong shift." if lang == "tl" else "No cash check yet this shift.")
        return template_cash_text(c, lang) + (
            " (Inaasahan: %s, nabilang: %s)" % (core.peso(c["expected"]), core.peso(c["declared"])) if lang == "tl"
            else " (Expected %s, declared %s.)" % (core.peso(c["expected"]), core.peso(c["declared"])))
    target = None
    for f in s["fuels"]:
        if f["fuel_type"].lower() in q:
            target = f
    if target is None:
        for word, name in (("gas", "Unleaded"), ("regular", "Unleaded"), ("krudo", "Diesel")):
            if word in q:
                target = next((f for f in s["fuels"] if f["fuel_type"] == name), None)
    if target:
        if lang == "tl":
            return "Ang benta ng %s ngayong shift ay %s (%s litro, %d benta)." % (
                target["fuel_type"], core.peso(target["amount"]), target["liters"], target["count"])
        return "%s sales this shift: %s (%s liters, %d sales)." % (
            target["fuel_type"], core.peso(target["amount"]), target["liters"], target["count"])
    if lang == "tl":
        return "Kabuuang benta ngayong shift: %s (%s litro, %d benta)." % (
            core.peso(s["total_amount"]), s["total_liters"], s["count"])
    return "Total sales this shift: %s (%s liters, %d sales)." % (
        core.peso(s["total_amount"]), s["total_liters"], s["count"])


def _norm_numbers(text):
    out = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            d = core.Decimal(m.replace(",", ""))
        except Exception:
            continue
        out.add(d.normalize())
        for places in ("1", "0.1", "0.01"):
            out.add(d.quantize(core.Decimal(places), rounding=core.ROUND_HALF_UP).normalize())
    return out


def unverified_numbers(answer, context):
    """Numbers in the answer that do not appear in the code-computed context (hallucination guard)."""
    allowed = _norm_numbers(context)
    bad = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", answer or ""):
        try:
            d = core.Decimal(m.replace(",", "")).normalize()
        except Exception:
            continue
        if d not in allowed and d > 3:
            bad.append(m)
    return bad


def ask(question, s):
    lang = detect_lang(question)
    context = summary_context(s)
    if core.MOCK_AI:
        answer, source = template_answer(question, s, lang), "template"
    else:
        language = "Tagalog" if lang == "tl" else "English"
        prompt = ("You help staff at a small Philippine gas station. DATA (computed by the system, trust it):\n"
                  "%s\n\nQUESTION: %s\n\nAnswer in %s in 1-3 short sentences using ONLY numbers from DATA. "
                  "Do not calculate new numbers. For questions about missing or unrecorded fuel (e.g. 'may kulang ba sa "
                  "diesel?'), use the Pump meters lines. If DATA does not have the answer, say so." % (
                      context, question, language))
        try:
            answer, source = chat([{"role": "user", "content": prompt}], num_predict=150).strip(), "ai"
        except AIError as e:
            answer = template_answer(question, s, lang)
            source = "template-fallback"
    return {"answer": answer, "lang": lang, "source": source, "context": context,
            "unverified_numbers": unverified_numbers(answer, context)}
