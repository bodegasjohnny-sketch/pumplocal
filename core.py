"""PumpLocal core: storage, money math, local AI calls and sync queue.

Everything here is Python 3 standard library only (works with macOS system python3).
All arithmetic on money and liters is done in this file with Decimal -- never by the model.
"""
import base64
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("MODEL", "gemma3:4b")
MOCK_AI = os.environ.get("MOCK_AI", "0") == "1"
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "pumplocal.db"))
STATION = os.environ.get("STATION_NAME", "Demo Station")
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "240"))
# Cash differences within this many pesos are treated as OK (rounding / loose coins).
CASH_TOLERANCE = Decimal(os.environ.get("CASH_TOLERANCE", "5.00"))

FUELS = ["Premium", "Unleaded", "Diesel"]
_db_lock = threading.Lock()


# ---------------------------------------------------------------- money math
def dec(value):
    """Parse a number from user / model input ("P1,234.50", "64.99", 12) -> Decimal or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        d = Decimal(str(value))
    else:
        s = str(value).strip().replace(",", "")
        s = re.sub(r"(?i)php|pesos?|liters?|litres?|ltrs?|/l\b|[₱P\sL]", "", s)
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return None
        try:
            d = Decimal(m.group(0))
        except InvalidOperation:
            return None
    if not d.is_finite():
        return None
    return d


def q2(d):
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q3(d):
    return d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def peso(d):
    d = q2(Decimal(str(d)))
    sign = "-" if d < 0 else ""
    return "%s₱%s" % (sign, "{:,.2f}".format(abs(d)))


def normalize_fuel(value):
    if not value:
        return ""
    s = str(value).strip().lower()
    if "diesel" in s or "dsl" in s:
        return "Diesel"
    if "premium" in s or "prem" in s or "super" in s or "95" in s or "97" in s:
        return "Premium"
    if "unleaded" in s or "regular" in s or "gasoline" in s or "91" in s:
        return "Unleaded"
    return str(value).strip().title()[:30]


def reconcile(liters, price, amount):
    """Fill the missing value from the other two and flag inconsistencies.

    liters = pesos / price, pesos = liters * price, price = pesos / liters.
    Returns dict with string values (or None) plus notes and warnings.
    """
    L, P, A = dec(liters), dec(price), dec(amount)
    notes, warnings = [], []
    for name, v in (("Liters", L), ("Price per liter", P), ("Amount", A)):
        if v is not None and v <= 0:
            warnings.append("%s must be greater than zero." % name)
    if L is not None and L <= 0:
        L = None
    if P is not None and P <= 0:
        P = None
    if A is not None and A <= 0:
        A = None

    if A is not None and P is not None and L is None:
        L = q3(A / P)
        notes.append("Liters computed in code: amount ÷ price.")
    elif L is not None and P is not None and A is None:
        A = q2(L * P)
        notes.append("Amount computed in code: liters × price.")
    elif A is not None and L is not None and P is None:
        P = q2(A / L)
        notes.append("Price computed in code: amount ÷ liters.")
    elif A is not None and L is not None and P is not None:
        expected = q2(L * P)
        diff = abs(expected - A)
        if diff > max(Decimal("1.00"), A * Decimal("0.005")):
            warnings.append(
                "Check values: liters × price = %s but amount says %s." % (peso(expected), peso(A)))
    if P is not None and not (Decimal("30") <= P <= Decimal("150")):
        warnings.append("Price %s/L looks unusual. Please check." % peso(P))
    if L is not None and L > Decimal("500"):
        warnings.append("%s liters looks unusually large. Please check." % L)
    return {
        "liters": str(q3(L)) if L is not None else None,
        "price_per_liter": str(q2(P)) if P is not None else None,
        "amount_pesos": str(q2(A)) if A is not None else None,
        "notes": notes,
        "warnings": warnings,
    }


def compute_cash(sales_total, declared, opening_float=0, noncash=0, tolerance=None):
    """Expected cash = opening float + sales - non-cash (GCash/card). Pure code, no AI."""
    tol = CASH_TOLERANCE if tolerance is None else Decimal(str(tolerance))
    sales_total = q2(dec(sales_total) or Decimal(0))
    declared = q2(dec(declared) or Decimal(0))
    opening_float = q2(dec(opening_float) or Decimal(0))
    noncash = q2(dec(noncash) or Decimal(0))
    expected = q2(opening_float + sales_total - noncash)
    diff = q2(declared - expected)
    pct = q2(diff / expected * 100) if expected != 0 else None
    if abs(diff) <= tol:
        status = "OK"
    elif diff < 0:
        status = "SHORT"
    else:
        status = "OVER"
    return {
        "sales_total": str(sales_total), "opening_float": str(opening_float),
        "noncash": str(noncash), "expected": str(expected), "declared": str(declared),
        "diff": str(diff), "diff_pct": str(pct) if pct is not None else None,
        "status": status, "tolerance": str(q2(tol)),
    }


# ---------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  attendant TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  created_at TEXT NOT NULL,
  fuel_type TEXT NOT NULL,
  liters TEXT NOT NULL,
  price_per_liter TEXT NOT NULL,
  amount_pesos TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  note TEXT DEFAULT '',
  voided INTEGER NOT NULL DEFAULT 0,
  synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cash_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  created_at TEXT NOT NULL,
  sales_total TEXT NOT NULL,
  opening_float TEXT NOT NULL,
  noncash TEXT NOT NULL,
  expected TEXT NOT NULL,
  declared TEXT NOT NULL,
  diff TEXT NOT NULL,
  diff_pct TEXT,
  status TEXT NOT NULL,
  explanation TEXT DEFAULT '',
  explanation_source TEXT DEFAULT '',
  synced INTEGER NOT NULL DEFAULT 0
);
"""

SYNC_TABLES = ("shifts", "sales", "cash_checks")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock, connect() as conn:
        conn.executescript(SCHEMA)


def rows(sql, args=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def execute(sql, args=()):
    with _db_lock, connect() as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid


def is_empty():
    return rows("SELECT COUNT(*) AS n FROM shifts")[0]["n"] == 0


def current_shift():
    r = rows("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1")
    if r:
        return r[0]
    sid = execute("INSERT INTO shifts (opened_at) VALUES (?)", (now(),))
    return rows("SELECT * FROM shifts WHERE id=?", (sid,))[0]


def new_shift(attendant=""):
    with _db_lock, connect() as conn:
        conn.execute("UPDATE shifts SET status='closed', closed_at=?, synced=0 WHERE status='open'", (now(),))
        cur = conn.execute("INSERT INTO shifts (opened_at, attendant) VALUES (?, ?)", (now(), attendant[:60]))
        conn.commit()
        sid = cur.lastrowid
    return rows("SELECT * FROM shifts WHERE id=?", (sid,))[0]


def save_sale(data, shift_id=None, created_at=None):
    fuel = normalize_fuel(data.get("fuel_type"))
    rec = reconcile(data.get("liters"), data.get("price_per_liter"), data.get("amount_pesos"))
    errors = []
    if not fuel:
        errors.append("Fuel type is required.")
    if not (rec["liters"] and rec["price_per_liter"] and rec["amount_pesos"]):
        errors.append("Enter at least two of liters, price per liter and amount.")
    if errors:
        return None, errors, rec
    shift_id = shift_id or current_shift()["id"]
    note = "; ".join(rec["warnings"] + rec["notes"] + ([data.get("note")] if data.get("note") else []))[:300]
    source = data.get("source") if data.get("source") in ("photo", "manual", "seed") else "manual"
    sid = execute(
        "INSERT INTO sales (shift_id, created_at, fuel_type, liters, price_per_liter, amount_pesos, source, note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (shift_id, created_at or now(), fuel, rec["liters"], rec["price_per_liter"], rec["amount_pesos"], source, note))
    return rows("SELECT * FROM sales WHERE id=?", (sid,))[0], [], rec


def void_sale(sale_id):
    execute("UPDATE sales SET voided=1, synced=0 WHERE id=?", (int(sale_id),))


def shift_summary(shift_id=None):
    shift = rows("SELECT * FROM shifts WHERE id=?", (shift_id,))[0] if shift_id else current_shift()
    sales = rows("SELECT * FROM sales WHERE shift_id=? AND voided=0 ORDER BY id", (shift["id"],))
    by_fuel = {}
    total_amount, total_liters = Decimal(0), Decimal(0)
    for s in sales:
        f = by_fuel.setdefault(s["fuel_type"], {"count": 0, "liters": Decimal(0), "amount": Decimal(0)})
        f["count"] += 1
        f["liters"] += Decimal(s["liters"])
        f["amount"] += Decimal(s["amount_pesos"])
        total_amount += Decimal(s["amount_pesos"])
        total_liters += Decimal(s["liters"])
    fuels = []
    order = FUELS + sorted(k for k in by_fuel if k not in FUELS)
    for name in order:
        if name in by_fuel:
            f = by_fuel[name]
            fuels.append({"fuel_type": name, "count": f["count"], "liters": str(q3(f["liters"])),
                          "amount": str(q2(f["amount"]))})
    checks = rows("SELECT * FROM cash_checks WHERE shift_id=? ORDER BY id DESC LIMIT 1", (shift["id"],))
    return {
        "shift": shift, "fuels": fuels, "count": len(sales),
        "total_amount": str(q2(total_amount)), "total_liters": str(q3(total_liters)),
        "sales": list(reversed(sales)), "last_cash_check": checks[0] if checks else None,
    }


def save_cash_check(result, explanation, source, shift_id):
    cid = execute(
        "INSERT INTO cash_checks (shift_id, created_at, sales_total, opening_float, noncash, expected, declared, "
        "diff, diff_pct, status, explanation, explanation_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (shift_id, now(), result["sales_total"], result["opening_float"], result["noncash"], result["expected"],
         result["declared"], result["diff"], result["diff_pct"], result["status"], explanation, source))
    return rows("SELECT * FROM cash_checks WHERE id=?", (cid,))[0]


def queued_count():
    return sum(rows("SELECT COUNT(*) AS n FROM %s WHERE synced=0" % t)[0]["n"] for t in SYNC_TABLES)
