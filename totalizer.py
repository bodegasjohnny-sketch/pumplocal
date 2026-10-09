"""Turn OCR text from a pump's totalizer screen into a reading -- plain code, no AI.

A totalizer is the pump's lifetime counter (it never resets per sale). Staff photograph it at shift start
(OPENING) and shift end (CLOSING); PumpLocal computes dispensed = closing - opening in core.py.

Input: OCR lines [{"text", "x", "y", "w", "h"}] (boxes optional) or plain strings.
Output: {"reading": "775397", "pump_name": "Diesel 2", "fuel_type": "Diesel", "unit_hint": "L", ...}

How the reading is found:
  1. Menu numbers such as the "2." in "2.Money All", dates, times and the number in a pump label
     ("DIESEL 2", "PUMP 3") are removed first.
  2. Numbers on a line with a totalizer label (Volume/Vol/Total/Money/Amount/Liters/Qty) win; then numbers
     right next to (or just below) such a label; then any other number with 3+ digits.
  3. Within the best group, the LARGEST number is the reading (a totalizer is a big counter).
The reading keeps the digits exactly as shown; hidden decimal places are applied per pump in core.py.
"""
import re
from decimal import Decimal, InvalidOperation

import core
import meterparse

LABEL = re.compile(
    r"\b(V[O0]L(?:UME)?|V[O0]1UME|TOTAL(?:IZER)?|TOT|MONEY|AMOUNT|AMT|LITERS?|LITRES?|LITRO|LTRS?|QTY|QUANTITY|"
    r"PESOS?|PHP|SALES?)\b", re.I)
LITER_WORDS = re.compile(r"^(V[O0]L|V[O0]1|LIT|LTR|QTY|QUANT)", re.I)
PESO_WORDS = re.compile(r"^(MONEY|AMOUNT|AMT|PESO|PHP|SALE)", re.I)

FUEL_LABEL = re.compile(
    r"\b(DIESEL|PREMIUM|UNLEADED|SUPER|REGULAR|GASOLINE|KEROSENE|DSL)\b(?:\s*[-#]?\s*(?:N[O0]\.?\s*)?(\d{1,2})\b)?", re.I)
PUMP_LABEL = re.compile(r"\b(PUMP|NOZZLE|NOZ|DISPENSER|ISLAND)\s*[-#:]?\s*(?:N[O0]\.?\s*)?(\d{1,2})\b", re.I)
MENU_NUMBER = re.compile(r"(?<![\d.,])\b\d{1,2}\s*[.)]\s*(?=[A-Za-z])")


def _text(ln):
    return (ln.get("text") if isinstance(ln, dict) else str(ln or "")) or ""


def pump_label(lines):
    """Pump name and fuel from text like 'DIESEL 2', 'PREMIUM 1', 'UNLEADED', 'PUMP 3'. Returns (name, fuel)."""
    fuel_hit, pump_no = None, None
    for ln in lines:
        t = _text(ln)
        m = FUEL_LABEL.search(t)
        if m and fuel_hit is None:
            fuel_hit = m
        p = PUMP_LABEL.search(t)
        if p and pump_no is None:
            pump_no = p.group(2)
    if fuel_hit:
        word = fuel_hit.group(1)
        fuel = core.normalize_fuel(word)
        base = fuel if fuel in core.FUELS else word.title()
        no = fuel_hit.group(2) or pump_no
        return ("%s %s" % (base, int(no)) if no else base), fuel
    if pump_no:
        return "Pump %d" % int(pump_no), ""
    return "", ""


def _clean(text):
    """Remove the parts of a line whose numbers are never the reading."""
    t = FUEL_LABEL.sub(" ", text)
    t = PUMP_LABEL.sub(" ", t)
    t = MENU_NUMBER.sub(" ", t)
    t = meterparse.DATE_TIME.sub(" ", t)
    return t


def _numbers(text, labelled):
    """[(Decimal, digits-as-shown)] in a cleaned line."""
    t = meterparse._fix_digits(_clean(text))
    if labelled:  # LCD digits are often split by OCR: "775 397" -> "775397"
        t = re.sub(r"(?<=\d) (?=\d)", "", t)
    out = []
    for m in meterparse.NUMBER.finditer(t):
        s = m.group(0)
        if re.fullmatch(r"\d+,\d{1,3}", s) and not re.fullmatch(r"\d{1,3},\d{3}", s):
            s = s.replace(",", ".")  # decimal comma
        s = s.replace(",", "")
        try:
            out.append((Decimal(s), s))
        except InvalidOperation:
            pass
    return out


def _has_box(ln):
    return isinstance(ln, dict) and all(k in ln for k in ("x", "y", "w", "h"))


def _label_words(text):
    return " ".join(m.group(1) for m in LABEL.finditer(_clean(text)))


def _unit_of(label_words):
    """'Volume' -> L, 'Money'/'Amount' -> PHP, 'Total' -> unknown. Only a hint: staff set the unit per pump."""
    for w in (label_words or "").split():
        if LITER_WORDS.match(w):
            return "L"
        if PESO_WORDS.match(w):
            return "PHP"
    return None


def parse(lines):
    lines = [ln for ln in (lines or []) if _text(ln).strip()]
    name, fuel = pump_label(lines)
    labels = []  # (index, line, label word)
    for i, ln in enumerate(lines):
        if LABEL.search(_clean(_text(ln))):
            labels.append((i, ln, _label_words(_text(ln))))
    cands = []  # (tier, -value, cost, digits, label word)
    for i, ln in enumerate(lines):
        t = _text(ln)
        own = LABEL.search(_clean(t))
        nums = _numbers(t, bool(own))
        if not nums:
            continue
        if own:
            tier, cost, word = 0, 0.0, _label_words(t)
        else:
            tier, cost, word = 2, None, None
            for j, lab, lw in labels:
                c = None
                if _has_box(lab) and _has_box(ln):
                    c = meterparse._link_cost(lab, ln)
                elif not (_has_box(lab) or _has_box(ln)) and i == j + 1:
                    c = 0.5  # plain text: number on the line right after the label
                if c is not None and (cost is None or c < cost):
                    tier, cost, word = 1, c, lw
        for value, digits in nums:
            if tier == 2 and len(digits.replace(".", "")) < 3:
                continue  # short unlabelled numbers are menu items, button numbers, etc.
            cands.append((tier, -value, cost or 0.0, digits, word))
    if not cands:
        return {"reading": None, "value": None, "pump_name": name, "fuel_type": fuel, "unit_hint": None,
                "label": None, "confidence": "none", "notes": ["No totalizer number found in the photo text."]}
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    tier, negv, _, digits, word = cands[0]
    notes = []
    if tier == 2:
        notes.append("No Volume/Total label found next to the number; used the largest number. Please check it.")
    conf = ("labelled", "nearby", "guess")[tier]
    return {"reading": digits, "value": str(-negv), "pump_name": name, "fuel_type": fuel,
            "unit_hint": _unit_of(word), "label": word, "confidence": conf, "notes": notes}
