"""Turn OCR text lines (from Apple Vision) into sale fields -- plain code, no AI.

Input: list of {"text", "x", "y", "w", "h"} with boxes normalized 0..1, origin top-left
(the format printed by ocr/ocr.swift). Output: fuel_type, amount_pesos, liters, price_per_liter.

How values are found:
  1. Each line is classified as a label (AMOUNT/PESOS/TOTAL, LITERS/LITRO/VOLUME, PRICE/PER LITER,
     or an ignored label such as CASH/CHANGE/DATE/PUMP) and/or as holding a number.
  2. A number belongs to the closest label on the same row (label on the left) or just above it.
     Unit hints help too: "34.843 L" is liters, "P 57.40/L" is a price.
  3. Sanity check in code: liters x price must equal amount within 1%. If the labelled values
     disagree, we search the numbers on the image for a combination that is consistent.
  4. If only two of the three values are found, the third is computed in code (Decimal).
"""
import re
from decimal import Decimal, InvalidOperation

import core

TOLERANCE = Decimal("0.01")  # liters x price within 1% of amount
PRICE_RANGE = (Decimal("30"), Decimal("150"))
LITERS_RANGE = (Decimal("0.1"), Decimal("500"))
AMOUNT_RANGE = (Decimal("1"), Decimal("100000"))

FUEL_WORDS = [
    (re.compile(r"\bDIESEL\b|\bDSL\b|\bKRUDO\b", re.I), "Diesel"),
    (re.compile(r"\bPREMIUM\b|\bSUPER\b", re.I), "Premium"),
    (re.compile(r"\bUNLEADED\b|\bREGULAR\b|\bGASOLINE\b", re.I), "Unleaded"),
]

# Order matters: "PRICE PER LITER" must be a price label, not a liters label.
LABELS = [
    ("price", re.compile(r"PRICE|PRESYO|PER\s*LIT|PER\s*L\b|/\s*L(?:I?TR?)?\b|UNIT\s*PRICE|P/L\b|\bPPL\b", re.I)),
    ("ignore", re.compile(r"CASH|CHANGE|SUKLI|TENDER|VAT|DISCOUNT|DATE|TIME|PUMP|NOZZLE|INVOICE|\bOR\b|\bNO\.|"
                          r"TIN\b|TEL|PHONE|RECEIPT|TRANS|SALE\s*#|SERIAL|ODO|TOTALIZER", re.I)),
    ("amount", re.compile(r"AMOUNT|PESOS?\b|TOTAL|HALAGA|\bSALE\b|\bPHP\b|\bDUE\b", re.I)),
    ("liters", re.compile(r"LITERS?|LITRES?|LITRO|VOLUME|\bVOL\b|\bLTRS?\b|\bQTY\b|DAMI", re.I)),
]

DATE_TIME = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?")
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?")


def _fix_digits(text):
    """Common OCR confusions inside numbers: O/o->0, I/|->1 next to digits; l/S->1/5 only between digits."""
    t = re.sub(r"(?<=[\d.,])[OoI|]+(?=[\d.,]|\s|$)|(?<![A-Za-z])[OoI|]+(?=\d*[.,]\d)",
               lambda m: m.group(0).translate(str.maketrans("OoI|", "0011")), text)
    return re.sub(r"(?<=\d)[lS](?=[\d.,])", lambda m: "1" if m.group(0) == "l" else "5", t)


def numbers_in(text):
    """Decimal values in a line, ignoring dates and times. '2,000.00' -> 2000.00, '57,40' -> 57.40."""
    t = DATE_TIME.sub(" ", _fix_digits(text or ""))
    out = []
    for m in NUMBER.finditer(t):
        s = m.group(0)
        if re.fullmatch(r"\d+,\d{2}", s):  # decimal comma
            s = s.replace(",", ".")
        try:
            out.append(Decimal(s.replace(",", "")))
        except InvalidOperation:
            pass
    return out


def label_of(text):
    for kind, rx in LABELS:
        if rx.search(text or ""):
            return kind
    return None


def unit_hint(text):
    t = (text or "").strip()
    if re.search(r"/\s*(?:L|LI?TR?|LITER|LITRE)\b", t, re.I):
        return "price"
    if re.search(r"\d\s*(?:L|LTRS?|LITERS?|LITRES?|LITRO)\b\.?$", t, re.I):
        return "liters"
    if re.search(r"^\s*(?:₱|P|PHP)\s*\d", t, re.I):
        return "pesos"  # a peso value: amount or price, not liters
    return None


def fuel_type(lines):
    for ln in lines:
        for rx, name in FUEL_WORDS:
            if rx.search(ln.get("text") or ""):
                return name
    return ""


def _box(ln):
    x, y = float(ln.get("x", 0)), float(ln.get("y", 0))
    w, h = float(ln.get("w", 0)), float(ln.get("h", 0.03)) or 0.03
    return x, y, w, h


def _link_cost(label, value):
    """How well does `label` describe `value`? Lower is better; None = not related."""
    lx, ly, lw, lh = _box(label)
    vx, vy, vw, vh = _box(value)
    h = max(lh, vh)
    dyc = (vy + vh / 2) - (ly + lh / 2)
    if abs(dyc) <= 0.6 * h:  # same row: label should sit to the left
        gap = vx - (lx + lw)
        if gap < -0.02:
            return None
        return 0.1 + max(gap, 0) + abs(dyc)
    gap_below = vy - (ly + lh)  # label directly above the value
    if -0.5 * h <= gap_below <= max(2.5 * h, 0.12):
        overlap = min(lx + lw, vx + vw) - max(lx, vx)
        misalign = 0 if overlap > 0 else -overlap
        return 0.3 + 3 * max(gap_below, 0) + misalign
    return None


def _consistent(L, P, A):
    if None in (L, P, A) or A <= 0:
        return False
    return abs(L * P - A) <= A * TOLERANCE


def _in(v, rng):
    return v is not None and rng[0] <= v <= rng[1]


def parse(lines):
    """Returns {"fields": {...}, "consistent": bool, "found": [...], "computed": [...], "notes": [...]}"""
    lines = [ln for ln in (lines or []) if (ln.get("text") or "").strip()]
    fuel = fuel_type(lines)
    labels, values = [], []
    for ln in lines:
        text = ln["text"]
        kind = label_of(text)
        nums = numbers_in(text)
        if kind:
            labels.append((kind, ln))
        if nums:
            values.append((ln, nums))

    # 1) label proximity (a line with both label and number links to itself at cost 0)
    best = {}  # kind -> (cost, Decimal)
    candidates = []  # every usable number, for the consistency search
    for ln, nums in values:
        own = label_of(ln["text"])
        hint = unit_hint(ln["text"])
        if own:
            kind, cost = own, 0.0
        else:
            kind, cost = None, None
            for lk, lab in labels:
                if lab is ln:
                    continue
                c = _link_cost(lab, ln)
                if c is not None and (cost is None or c < cost):
                    kind, cost = lk, c
            if hint in ("price", "liters") and kind in (None, "amount", "liters", "price") and kind != hint:
                if kind is None or hint == "price":
                    kind, cost = hint, 0.5 if cost is None else cost
        if kind == "liters" and hint == "pesos":
            kind = None  # "P 57.40" is never liters
        if kind == "ignore":
            continue
        value = max(nums) if kind == "amount" else nums[-1]
        candidates.extend(nums)
        if kind in ("amount", "liters", "price") and (kind not in best or cost < best[kind][0]):
            best[kind] = (cost, value)

    A = best.get("amount", (0, None))[1]
    L = best.get("liters", (0, None))[1]
    P = best.get("price", (0, None))[1]
    notes = []

    # 2) labelled values disagree or are implausible -> look for a consistent combination
    labelled_ok = _consistent(L, P, A) and _in(P, PRICE_RANGE) and _in(L, LITERS_RANGE)
    if not labelled_ok:
        combo = _search(candidates, A, L, P)
        if combo:
            if (A, L, P) != combo and all(v is not None for v in (A, L, P)):
                notes.append("Labelled numbers disagreed; used the combination where liters × price = amount.")
            A, L, P = combo

    found = [k for k, v in (("amount_pesos", A), ("liters", L), ("price_per_liter", P)) if v is not None]
    computed = []
    if len(found) == 2:
        rec = core.reconcile(L, P, A)
        A, L, P = core.dec(rec["amount_pesos"]), core.dec(rec["liters"]), core.dec(rec["price_per_liter"])
        computed = [k for k in ("amount_pesos", "liters", "price_per_liter") if k not in found]
        notes.extend(rec["notes"])

    complete = None not in (A, L, P)
    consistent = (complete and _in(P, PRICE_RANGE) and _in(L, LITERS_RANGE) and _in(A, AMOUNT_RANGE)
                  and (bool(computed) or _consistent(L, P, A)))
    fields = {
        "fuel_type": fuel,
        "liters": str(core.q3(L)) if L is not None else None,
        "price_per_liter": str(core.q2(P)) if P is not None else None,
        "amount_pesos": str(core.q2(A)) if A is not None else None,
    }
    return {"fields": fields, "consistent": bool(consistent), "found": found, "computed": computed, "notes": notes}


def _search(nums, A, L, P):
    """Find (amount, liters, price) among the numbers with liters x price = amount (within 1%).
    Prefers combinations that keep the most label-matched values."""
    uniq = sorted(set(n for n in nums if n > 0))
    best, best_score = None, -1
    for a in uniq:
        if not _in(a, AMOUNT_RANGE):
            continue
        for p in uniq:
            if not _in(p, PRICE_RANGE) or p == a:
                continue
            for l in uniq:
                if l in (a, p) or not _in(l, LITERS_RANGE) or not _consistent(l, p, a):
                    continue
                score = (a == A) + (l == L) + (p == P)
                if score > best_score:
                    best, best_score = (a, l, p), score
    return best


# ---------------------------------------------------------------- expense receipts: TOTAL amount only
TOTAL_LABEL = re.compile(r"GRAND\s*TOTAL|TOTAL\s*(?:AMOUNT|DUE|SALES?)?|AMOUNT\s*DUE|AMOUNT\s*PAYABLE|HALAGA|\bDUE\b|"
                         r"\bAMOUNT\b", re.I)
NOT_TOTAL = re.compile(r"SUB\s*-?\s*TOTAL|CASH|CHANGE|SUKLI|TENDER|VAT|VATABLE|DISCOUNT|DATE|TIME|TIN\b|\bNO\.|"
                       r"QTY|ITEMS?\b|INVOICE|TEL|PHONE|SERIAL|CARD|GCASH", re.I)


def receipt_total(lines):
    """Expense receipt -> {"amount": "350.00" or None, "store": first text line, "label": ...}. Plain code.

    The amount is the number next to (same line, right of, or just below) a TOTAL / AMOUNT DUE / GRAND TOTAL
    label; SUBTOTAL, CASH, CHANGE, VAT and DISCOUNT lines are skipped. GRAND TOTAL beats TOTAL beats AMOUNT.
    """
    lines = [ln for ln in (lines or []) if (ln.get("text") or "").strip()]
    store = ""
    for ln in lines:
        t = ln["text"].strip()
        if re.search(r"[A-Za-z]{3}", t) and not NOT_TOTAL.search(t) and not TOTAL_LABEL.search(t):
            store = t[:60]
            break
    labels = []
    for ln in lines:
        t = ln["text"]
        m = TOTAL_LABEL.search(t)
        if m and not NOT_TOTAL.search(t):
            rank = 0 if re.search(r"GRAND", m.group(0), re.I) else (1 if re.search(r"TOTAL|DUE|PAYABLE", m.group(0), re.I)
                                                                     else 2)
            labels.append((rank, ln, m.group(0)))
    best = None  # (rank, cost, value, label)
    for ln in lines:
        nums = [n for n in numbers_in(ln["text"]) if n > 0]
        if not nums or NOT_TOTAL.search(ln["text"]):
            continue
        for rank, lab, word in labels:
            if lab is ln:
                cost = 0.0
            else:
                cost = _link_cost(lab, ln) if all(k in lab and k in ln for k in ("x", "y")) else None
            if cost is None:
                continue
            cand = (rank, cost, max(nums), word)
            if best is None or cand[:2] < best[:2]:
                best = cand
    if best is None or not _in(best[2], AMOUNT_RANGE):
        return {"amount": None, "store": store, "label": None}
    return {"amount": str(core.q2(best[2])), "store": store, "label": best[3]}
