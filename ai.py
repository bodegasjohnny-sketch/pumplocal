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
    "kahapon", "ito", "yung", "naman", "na", "pa", "kami", "natin", "paano", "bakit",
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
    lines.append("TOTAL: %s liters, %s." % (s["total_liters"], core.peso(s["total_amount"])))
    c = s.get("last_cash_check")
    if c:
        lines.append("Last cash check: expected %s, declared %s, difference %s, status %s." % (
            core.peso(c["expected"]), core.peso(c["declared"]), core.peso(c["diff"]), c["status"]))
    else:
        lines.append("No cash check yet this shift.")
    return "\n".join(lines)


def template_answer(question, s, lang):
    q = (question or "").lower()
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
                  "Do not calculate new numbers. If DATA does not have the answer, say so." % (
                      context, question, language))
        try:
            answer, source = chat([{"role": "user", "content": prompt}], num_predict=150).strip(), "ai"
        except AIError as e:
            answer = template_answer(question, s, lang)
            source = "template-fallback"
    return {"answer": answer, "lang": lang, "source": source, "context": context,
            "unverified_numbers": unverified_numbers(answer, context)}
