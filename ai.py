"""Local AI layer: talks to Ollama on this machine (default gemma3:4b).

The model only (a) reads photos into fields and (b) words short text.
It never does the math: numbers are computed in core.py and passed in as context.
Set MOCK_AI=1 to run without Ollama (used for testing).
"""
import json
import re
import time
import urllib.error
import urllib.request

import core

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
    detail = ""


def detect_lang(text):
    words = re.findall(r"[a-zA-Z]+", (text or "").lower())
    hits = sum(1 for w in words if w in TAGALOG_WORDS)
    return "tl" if hits >= 2 or (hits == 1 and len(words) <= 3) else "en"


def status(force=False):
    """Is Ollama reachable and is the model pulled? Cached for 10 s."""
    if core.MOCK_AI:
        return {"ok": True, "mock": True, "model": core.MODEL, "detail": "MOCK_AI=1 (no Ollama needed)"}
    if force or time.time() - AIStatus.checked_at > 10:
        AIStatus.checked_at = time.time()
        try:
            with urllib.request.urlopen(core.OLLAMA_URL + "/api/tags", timeout=2) as r:
                names = [m.get("name", "") for m in json.loads(r.read().decode()).get("models", [])]
            want = core.MODEL if ":" in core.MODEL else core.MODEL + ":latest"
            if want in names:
                AIStatus.ok, AIStatus.detail = True, "Local AI ready"
            else:
                AIStatus.ok, AIStatus.detail = False, "Model not found. Run: ollama pull %s" % core.MODEL
        except Exception:
            AIStatus.ok, AIStatus.detail = False, "Ollama not running. Open the Ollama app or run: ollama serve"
    return {"ok": AIStatus.ok, "mock": False, "model": core.MODEL, "detail": AIStatus.detail}


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
    try:
        with urllib.request.urlopen(req, timeout=core.AI_TIMEOUT) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AIError("Ollama error %s: %s" % (e.code, e.read().decode(errors="replace")[:200]))
    except Exception as e:
        raise AIError("Local AI not reachable (%s). Is Ollama running?" % e.__class__.__name__)
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
                "seconds": round(time.time() - t0, 1)}
    f = fields_from_obj(obj)
    rec = core.reconcile(f["liters"], f["price_per_liter"], f["amount_pesos"])
    fields = {"fuel_type": f["fuel_type"], "liters": rec["liters"], "price_per_liter": rec["price_per_liter"],
              "amount_pesos": rec["amount_pesos"]}
    missing = [k for k, v in fields.items() if not v]
    msg = "Read from photo. Please check every value before saving."
    if missing:
        msg = "Some values were not readable (%s). Please fill them in." % ", ".join(missing)
    return {"ok": True, "fields": fields, "raw": raw[:500], "notes": rec["notes"], "warnings": rec["warnings"],
            "message": msg, "seconds": round(time.time() - t0, 1)}


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
