"""Shift Closing Slip: OCR lines -> structured values. Pure code, no AI.

The slip (static/closing_slip_template.html) is one column: a label on the left, one amount on the right.
OCR engines often return the label and the amount as separate pieces, and a phone photo is slightly tilted,
so pieces are first joined into rows (by vertical position), then each row is read by its label keyword:

    DATE / SHIFT                     text
    OPENING FLOAT                    amount
    EXPENSES  (section)              item rows: "ICE AND WATER 150.00"
    DISCOUNTS                        amount (or item rows that are added up)
    CREDIT / UTANG  (section)        item rows: "MANG BEN - DIESEL 3,000.00"
    GCASH / CARD / CASH COUNTED      amount

Totals (expenses, credit, GCash + card) are computed here with Decimal. Nothing is guessed: a label with no
readable amount is reported as unreadable, and a label that is not found is reported as missing.
"""
import re
from decimal import Decimal, InvalidOperation

import core
import meterparse

MAX_AMOUNT = Decimal("1000000")

# Order matters: the first matching rule wins.
RULES = [
    ("title", re.compile(r"CLOSING\s*SLIP|SHIFT\s*CLOSING", re.I)),
    ("hint", re.compile(r"^\W*(?:ONE\s*AMOUNT|PUMPLOCAL|ITEM\s*\+|NAME\s*-\s*FUEL)", re.I)),
    ("date", re.compile(r"^\W*(?:DATE|PETSA)\b", re.I)),
    ("shift", re.compile(r"^\W*(?:SHIFT|TURNO)\b", re.I)),
    ("opening_float", re.compile(r"FLOAT|PANUKLI|OPENING\s*CASH", re.I)),
    ("cash_counted", re.compile(r"CASH\s*COUNT|COUNTED|ACTUAL\s*CASH|NABILANG", re.I)),
    ("gcash", re.compile(r"^\W*G\s*[-.]?\s*CASH|^\W*6\s*CASH|MAYA|E-?WALLET", re.I)),
    ("card", re.compile(r"^\W*(?:CARD|DEBIT|CREDIT\s*CARD)\b", re.I)),
    ("expenses", re.compile(r"^\W*(?:EXPENSES?|GASTOS|PETTY\s*CASH)\b", re.I)),
    ("discounts", re.compile(r"^\W*(?:DISCOUNTS?|DISKWENTO|DISC)\b", re.I)),
    ("credits", re.compile(r"^\W*(?:CREDIT|UTANG|CHARGE)\b", re.I)),
    ("total", re.compile(r"^\W*TOTAL\b", re.I)),
]
SINGLE = ("opening_float", "gcash", "card", "cash_counted")
SECTIONS = ("expenses", "discounts", "credits")
EXPECTED = ("date", "shift", "opening_float", "expenses", "discounts", "credits", "gcash", "card", "cash_counted")
LABELS = {"date": "Date", "shift": "Shift", "opening_float": "Opening float", "expenses": "Expenses",
          "discounts": "Discounts", "credits": "Credit / utang", "gcash": "GCash", "card": "Card",
          "cash_counted": "Cash counted"}
# An amount at the END of a row: "1,000.00", "P 350", "₱12,950.00", "PHP 50.00", "3 000.00"
AMOUNT_AT_END = re.compile(r"(?:(?:₱|PHP|PhP|Php|P)\s*)?(\d{1,3}(?:[,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
                           r"\s*[-=/|]?\s*$")


def _num(token):
    """'12,950.00' / '12 950.00' / '50,00' / '1.000.00'-ish OCR text -> Decimal or None."""
    t = meterparse._fix_digits(token.strip())
    if re.fullmatch(r"\d+,\d{2}", t):  # decimal comma
        t = t.replace(",", ".")
    t = re.sub(r"[,\s]", "", t)
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return d if Decimal("0") <= d < MAX_AMOUNT else None


def split_amount(text):
    """'NOZZLE O-RING 350.00' -> ('NOZZLE O-RING', Decimal('350.00')); no amount at the end -> (text, None)."""
    t = meterparse._fix_digits(re.sub(r"[_—–]{2,}", " ", text or "")).strip()
    m = AMOUNT_AT_END.search(t)
    if not m:
        return t, None
    value = _num(m.group(1))
    return t[:m.start()].strip(" -:=.|·"), value


def rows_from_lines(lines):
    """Join OCR pieces that sit on the same printed row (tolerates a tilted photo). Pieces without boxes
    (e.g. a transcription) are rows on their own."""
    pieces = [ln for ln in (lines or []) if (ln.get("text") or "").strip()]
    if not pieces or any("y" not in ln for ln in pieces):
        return [(ln.get("text") or "").strip() for ln in pieces]
    boxed = []
    for ln in pieces:
        x, y = float(ln.get("x", 0)), float(ln.get("y", 0))
        w, h = float(ln.get("w", 0)), float(ln.get("h", 0.02)) or 0.02
        boxed.append({"text": ln["text"].strip(), "x": x, "yc": y + h / 2, "h": h})
    rows = []
    for p in sorted(boxed, key=lambda b: b["x"]):
        best, best_dy = None, None
        for r in rows:
            last = r[-1]
            if last["x"] >= p["x"]:
                continue
            dy = abs(p["yc"] - last["yc"])
            if dy <= 0.9 * max(p["h"], last["h"]) and (best_dy is None or dy < best_dy):
                best, best_dy = r, dy
        if best is None:
            rows.append([p])
        else:
            best.append(p)
    rows.sort(key=lambda r: r[0]["yc"])
    return [" ".join(p["text"] for p in r) for r in rows]


def kind_of(row):
    for kind, rx in RULES:
        if rx.search(row):
            return kind
    return None


def _clean_label(text, rx):
    return rx.sub("", text, count=1).strip(" -:=.|·")


def _title(text):
    t = re.sub(r"\s+", " ", text).strip(" -:=.|·")
    if t.isupper():
        return t.capitalize()
    return t[:1].upper() + t[1:]


def _customer_and_fuel(desc):
    fuel = meterparse.fuel_type([{"text": desc}])
    name = desc
    if fuel:
        name = re.sub(r"(?i)\b(?:DIESEL|DSL|PREMIUM|PREM|UNLEADED|UNL|GAS(?:OLINE)?|REGULAR)\b", " ", name)
    name = re.sub(r"[()\[\]]", " ", name)
    name = re.sub(r"\s*[-/,]\s*$|^\s*[-/,]\s*", "", re.sub(r"\s+", " ", name)).strip(" -/,")
    return (" ".join(w.capitalize() for w in name.split()) if name.isupper() else name), fuel


def parse(lines):
    rows = rows_from_lines(lines)
    out = {"is_slip": False, "date": None, "shift": None, "opening_float": None, "expenses": [], "discounts": None,
           "discount_items": [], "credits": [], "gcash": None, "card": None, "cash_counted": None,
           "written_totals": {}, "rows": rows}
    seen, unreadable, notes = set(), [], []
    section = None
    for row in rows:
        kind = kind_of(row)
        if kind == "title":
            out["is_slip"] = True
            continue
        if kind == "hint":
            continue
        if kind in ("date", "shift"):
            section = None
            seen.add(kind)
            value = _clean_label(row, dict(RULES)[kind])
            out[kind] = value or None
            if not value:
                unreadable.append(kind)
            continue
        if kind in SINGLE:
            section = None
            seen.add(kind)
            _, amount = split_amount(_clean_label(row, dict(RULES)[kind]))
            if amount is None:
                unreadable.append(kind)
            else:
                out[kind] = amount
            continue
        if kind in SECTIONS:
            section = kind
            seen.add(kind)
            _, amount = split_amount(_clean_label(row, dict(RULES)[kind]))
            if kind == "discounts" and amount is not None:
                out["discounts"] = amount
            elif amount is not None:
                out["written_totals"][kind] = amount  # e.g. "EXPENSES 500.00" with no items: checked below
            continue
        if kind == "total":
            _, amount = split_amount(_clean_label(row, dict(RULES)["total"]))
            if section and amount is not None:
                out["written_totals"][section] = amount
            continue
        if section:
            desc, amount = split_amount(row)
            if amount is None or amount <= 0:
                continue  # blank guide line or unreadable scribble
            if section == "expenses":
                out["expenses"].append({"description": _title(desc) or "Expense", "amount_pesos": amount})
            elif section == "credits":
                customer, fuel = _customer_and_fuel(desc)
                out["credits"].append({"customer": customer, "fuel_type": fuel, "amount_pesos": amount})
            elif section == "discounts":
                out["discount_items"].append({"description": _title(desc), "amount_pesos": amount})

    # ---- totals in code
    if out["discounts"] is None and out["discount_items"]:
        out["discounts"] = sum((d["amount_pesos"] for d in out["discount_items"]), Decimal(0))
    if not out["expenses"] and "expenses" in out["written_totals"]:
        out["expenses"].append({"description": "Expenses (total from slip)",
                                "amount_pesos": out["written_totals"]["expenses"]})
    exp_total = sum((e["amount_pesos"] for e in out["expenses"]), Decimal(0))
    cr_total = sum((c["amount_pesos"] for c in out["credits"]), Decimal(0))
    for sec, total in (("expenses", exp_total), ("credits", cr_total)):
        written = out["written_totals"].get(sec)
        if written is not None and written != total and (sec != "expenses" or len(out["expenses"]) > 1):
            notes.append("%s: the slip's written total %s doesn't match the items (%s)." % (
                LABELS[sec], core.peso(written), core.peso(total)))
    noncash = None
    if out["gcash"] is not None or out["card"] is not None:
        noncash = (out["gcash"] or Decimal(0)) + (out["card"] or Decimal(0))
    found = seen | {k for k in SINGLE if out[k] is not None}
    out["is_slip"] = out["is_slip"] or len(found & set(EXPECTED)) >= 3
    missing = [k for k in EXPECTED if k not in seen]
    unreadable = [k for k in EXPECTED if k in unreadable and k not in missing]

    def money(d):
        return str(core.q2(d)) if d is not None else None

    for e in out["expenses"]:
        e["amount_pesos"] = money(e["amount_pesos"])
    for c in out["credits"]:
        c["amount_pesos"] = money(c["amount_pesos"])
    for d in out["discount_items"]:
        d["amount_pesos"] = money(d["amount_pesos"])
    result = {k: out[k] for k in ("is_slip", "date", "shift", "expenses", "credits", "discount_items", "rows")}
    result.update({k: money(out[k]) for k in ("opening_float", "discounts", "gcash", "card", "cash_counted")})
    result.update(expenses_total=money(exp_total), credit_total=money(cr_total), noncash=money(noncash),
                  written_totals={k: money(v) for k, v in out["written_totals"].items()},
                  missing=missing, unreadable=unreadable, notes=notes,
                  missing_labels=[LABELS[k] for k in missing], unreadable_labels=[LABELS[k] for k in unreadable])
    return result
