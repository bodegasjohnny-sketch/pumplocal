"""PumpLocal core: storage, money math, local AI calls and sync queue.

Everything here is Python 3 standard library only (works with macOS system python3).
All arithmetic on money and liters is done in this file with Decimal -- never by the model.
"""
import contextlib
import os
import re
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("MODEL", "gemma3:4b")
MOCK_AI = os.environ.get("MOCK_AI", "0") == "1"
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "pumplocal.db"))
STATION = os.environ.get("STATION_NAME", "Demo Station")
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "600"))
# Short timeout for the header status check; a timeout during inference counts as "busy".
STATUS_TIMEOUT = float(os.environ.get("STATUS_TIMEOUT", "1.5"))
STATUS_CACHE = float(os.environ.get("STATUS_CACHE", "5"))
# Photo reader: auto = Apple Vision OCR first, Gemma fallback; vision = OCR only; gemma = Gemma only.
READER = os.environ.get("READER", "auto").strip().lower()
if READER not in ("auto", "vision", "gemma"):
    READER = "auto"
# Cash differences within this many pesos are treated as OK (rounding / loose coins).
CASH_TOLERANCE = Decimal(os.environ.get("CASH_TOLERANCE", "5.00"))
# Pump totalizer vs recorded sales: a gap within this percent of the dispensed amount is OK (default 0.5%).
PUMP_TOLERANCE_PCT = os.environ.get("PUMP_TOLERANCE_PCT", "0.5")
PUMP_DECIMALS = (0, 1, 2, 3)

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


DENOMS = ("1000", "500", "200", "100", "50", "20")


def count_cash(counts):
    """Cash drawer count: number of 1000/500/200/100/50/20 bills plus coins in pesos -> total. Pure code.
    Returns (result, errors)."""
    counts = counts or {}
    lines, errors, total = [], [], Decimal(0)
    for d in DENOMS:
        raw = counts.get(d, counts.get(int(d), ""))
        raw = "" if raw is None else str(raw).strip()
        if raw == "":
            n = 0
        elif re.fullmatch(r"\d{1,6}", raw):
            n = int(raw)
        else:
            errors.append("Number of ₱%s bills must be a whole number." % d)
            continue
        sub = Decimal(d) * n
        total += sub
        lines.append({"denom": d, "count": n, "subtotal": str(q2(sub))})
    coins = counts.get("coins")
    c = dec(coins) if coins not in (None, "") else Decimal(0)
    if c is None or c < 0:
        errors.append("Coins must be a peso amount, e.g. 135.50.")
        c = Decimal(0)
    total += c
    return {"lines": lines, "coins": str(q2(c)), "total": str(q2(total))}, errors


def compute_cash(sales_total, declared, opening_float=0, noncash=0, tolerance=None, discounts=0, credit=0,
                 expenses=0):
    """Expected cash = opening float + gross sales - discounts - credit (utang) sales - expenses - GCash/card.
    Pure code, no AI. Credit sales are counted net of their own discount, so nothing is subtracted twice."""
    tol = CASH_TOLERANCE if tolerance is None else Decimal(str(tolerance))
    sales_total = q2(dec(sales_total) or Decimal(0))
    declared = q2(dec(declared) or Decimal(0))
    opening_float = q2(dec(opening_float) or Decimal(0))
    noncash = q2(dec(noncash) or Decimal(0))
    discounts = q2(dec(discounts) or Decimal(0))
    credit = q2(dec(credit) or Decimal(0))
    expenses = q2(dec(expenses) or Decimal(0))
    expected = q2(opening_float + sales_total - discounts - credit - expenses - noncash)
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
        "gross_sales": str(sales_total), "discounts": str(discounts), "credit_sales": str(credit),
        "expenses": str(expenses),
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
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  created_at TEXT NOT NULL,
  amount_pesos TEXT NOT NULL,
  description TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  voided INTEGER NOT NULL DEFAULT 0,
  synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pumps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  fuel_type TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT 'L',
  decimals INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pump_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  pump_id INTEGER NOT NULL REFERENCES pumps(id),
  kind TEXT NOT NULL CHECK (kind IN ('open', 'close')),
  reading TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL,
  UNIQUE (shift_id, pump_id, kind)
);
CREATE TABLE IF NOT EXISTS totalizer_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  pump_id INTEGER NOT NULL REFERENCES pumps(id),
  kind TEXT NOT NULL CHECK (kind IN ('open', 'close')),
  amount TEXT,
  volume TEXT,
  amount_source TEXT,
  volume_source TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (shift_id, pump_id, kind)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Tables with a synced flag. A row is queued (synced=0) when it is created or changed, and is marked synced=1
# after the sync server accepts it (sync.py). Pumps and their totalizer readings sync the same way as expenses.
SYNC_TABLES = ("shifts", "sales", "cash_checks", "expenses", "pumps", "totalizer_readings")
# Columns added after v1; init_db adds them to older databases.
MIGRATIONS = {
    "sales": [("discount_pesos", "TEXT NOT NULL DEFAULT '0.00'"), ("discount_reason", "TEXT NOT NULL DEFAULT ''"),
              ("payment", "TEXT NOT NULL DEFAULT 'cash'"), ("customer", "TEXT NOT NULL DEFAULT ''")],
    "pumps": [("amount_decimals", "INTEGER NOT NULL DEFAULT 0"), ("volume_decimals", "INTEGER NOT NULL DEFAULT 0"),
              ("synced", "INTEGER NOT NULL DEFAULT 0")],
    "totalizer_readings": [("synced", "INTEGER NOT NULL DEFAULT 0")],
    "cash_checks": [("discounts", "TEXT NOT NULL DEFAULT '0.00'"), ("credit_sales", "TEXT NOT NULL DEFAULT '0.00'"),
                    ("expenses", "TEXT NOT NULL DEFAULT '0.00'"), ("cash_count", "TEXT NOT NULL DEFAULT ''")],
}
DISCOUNT_REASONS = ("suki", "senior", "pwd", "other")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextlib.contextmanager
def connect():
    """Open, commit and always close a SQLite connection."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db_lock, connect() as conn:
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
            for name, decl in cols:
                if name not in have:
                    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))
        # v2 pump readings (one counter per pump, unit L or PHP) -> peso + liter totalizers. Seeded demo rows are
        # dropped (seed.seed_pumps_if_demo re-seeds them with both counters); real rows keep their number.
        if conn.execute("SELECT COUNT(*) FROM totalizer_readings").fetchone()[0] == 0:
            for r in conn.execute("SELECT r.*, p.unit FROM pump_readings r JOIN pumps p ON p.id=r.pump_id "
                                  "WHERE r.source != 'seed'").fetchall():
                col = "amount" if r["unit"] == "PHP" else "volume"
                conn.execute("INSERT OR IGNORE INTO totalizer_readings (shift_id, pump_id, kind, %s, %s_source, "
                             "created_at) VALUES (?,?,?,?,?,?)" % (col, col),
                             (r["shift_id"], r["pump_id"], r["kind"], r["reading"], r["source"], r["created_at"]))


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
        old = conn.execute("SELECT id FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
        conn.execute("UPDATE shifts SET status='closed', closed_at=?, synced=0 WHERE status='open'", (now(),))
        cur = conn.execute("INSERT INTO shifts (opened_at, attendant) VALUES (?, ?)", (now(), attendant[:60]))
        sid = cur.lastrowid
        if old:  # last shift's CLOSING totalizer is this shift's OPENING (staff can still re-read it)
            conn.execute("INSERT INTO totalizer_readings (shift_id, pump_id, kind, amount, volume, amount_source, "
                         "volume_source, created_at) SELECT ?, pump_id, 'open', amount, volume, "
                         "CASE WHEN amount IS NULL THEN NULL ELSE 'carried' END, "
                         "CASE WHEN volume IS NULL THEN NULL ELSE 'carried' END, ? FROM totalizer_readings "
                         "WHERE shift_id=? AND kind='close'", (sid, now(), old["id"]))
        conn.commit()
    return rows("SELECT * FROM shifts WHERE id=?", (sid,))[0]


def last_price(fuel):
    r = rows("SELECT price_per_liter FROM sales WHERE fuel_type=? AND voided=0 ORDER BY id DESC LIMIT 1", (fuel,))
    return r[0]["price_per_liter"] if r else None


def save_sale(data, shift_id=None, created_at=None):
    """A sale. Optional discount (pesos + reason suki/senior/pwd/other) and payment 'credit' (utang, with customer).
    amount_pesos is the gross pump amount; the discount is what the customer did not pay."""
    fuel = normalize_fuel(data.get("fuel_type"))
    payment = "credit" if str(data.get("payment") or "").lower() in ("credit", "utang", "charge") else "cash"
    customer = re.sub(r"\s+", " ", str(data.get("customer") or "")).strip()[:60]
    price = data.get("price_per_liter")
    if payment == "credit" and dec(price) is None and dec(data.get("liters")) is None and fuel:
        price = last_price(fuel)  # credit form: customer, fuel, amount; price from the last sale of that fuel
    rec = reconcile(data.get("liters"), price, data.get("amount_pesos"))
    errors = []
    if not fuel:
        errors.append("Fuel type is required.")
    if not (rec["liters"] and rec["price_per_liter"] and rec["amount_pesos"]):
        errors.append("Enter at least two of liters, price per liter and amount." if payment == "cash" else
                      "Enter the amount (and the price per liter if this fuel has no sale yet).")
    if payment == "credit" and not customer:
        errors.append("Customer name is required for a credit (utang) sale.")
    disc_raw = data.get("discount_pesos")
    disc = dec(disc_raw) if disc_raw not in (None, "") else Decimal(0)
    reason = str(data.get("discount_reason") or "").strip().lower()
    if disc is None or disc < 0:
        errors.append("Discount must be a peso amount of zero or more.")
        disc = Decimal(0)
    elif disc > 0:
        if reason not in DISCOUNT_REASONS:
            errors.append("Choose the discount reason: suki, senior, PWD or other.")
        if rec["amount_pesos"] and disc > Decimal(rec["amount_pesos"]):
            errors.append("Discount can't be more than the sale amount.")
    else:
        reason = ""
    if errors:
        return None, errors, rec
    shift_id = shift_id or current_shift()["id"]
    note = "; ".join(rec["warnings"] + rec["notes"] + ([data.get("note")] if data.get("note") else []))[:300]
    source = data.get("source") if data.get("source") in ("photo", "manual", "seed") else "manual"
    sid = execute(
        "INSERT INTO sales (shift_id, created_at, fuel_type, liters, price_per_liter, amount_pesos, source, note, "
        "discount_pesos, discount_reason, payment, customer) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (shift_id, created_at or now(), fuel, rec["liters"], rec["price_per_liter"], rec["amount_pesos"], source, note,
         str(q2(disc)), reason, payment, customer if payment == "credit" else ""))
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
    discounts, credit = Decimal(0), Decimal(0)
    disc_by_reason, credit_list = {}, []
    for s in sales:
        d = Decimal(s.get("discount_pesos") or "0")
        if d:
            discounts += d
            disc_by_reason[s["discount_reason"]] = disc_by_reason.get(s["discount_reason"], Decimal(0)) + d
        if s.get("payment") == "credit":
            net = Decimal(s["amount_pesos"]) - d
            credit += net
            credit_list.append({"id": s["id"], "customer": s["customer"], "fuel_type": s["fuel_type"],
                                "liters": s["liters"], "amount": str(q2(net)), "created_at": s["created_at"]})
    expenses = rows("SELECT * FROM expenses WHERE shift_id=? AND voided=0 ORDER BY id", (shift["id"],))
    exp_total = sum((Decimal(e["amount_pesos"]) for e in expenses), Decimal(0))
    return {
        "discounts_total": str(q2(discounts)),
        "discounts_by_reason": {k: str(q2(v)) for k, v in sorted(disc_by_reason.items())},
        "discount_count": sum(1 for s in sales if Decimal(s.get("discount_pesos") or "0")),
        "credit_total": str(q2(credit)), "credit_sales": credit_list,
        "expenses_total": str(q2(exp_total)), "expenses": expenses,
        "shift": shift, "fuels": fuels, "count": len(sales),
        "total_amount": str(q2(total_amount)), "total_liters": str(q3(total_liters)),
        "sales": list(reversed(sales)), "last_cash_check": checks[0] if checks else None,
        "pump_check": pump_check(shift["id"]),
    }


def save_cash_check(result, explanation, source, shift_id, cash_count=""):
    cid = execute(
        "INSERT INTO cash_checks (shift_id, created_at, sales_total, opening_float, noncash, expected, declared, "
        "diff, diff_pct, status, explanation, explanation_source, discounts, credit_sales, expenses, cash_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (shift_id, now(), result["sales_total"], result["opening_float"], result["noncash"], result["expected"],
         result["declared"], result["diff"], result["diff_pct"], result["status"], explanation, source,
         result.get("discounts", "0.00"), result.get("credit_sales", "0.00"), result.get("expenses", "0.00"),
         cash_count or ""))
    return rows("SELECT * FROM cash_checks WHERE id=?", (cid,))[0]


def queued_count():
    return sum(rows("SELECT COUNT(*) AS n FROM %s WHERE synced=0" % t)[0]["n"] for t in SYNC_TABLES)


# ---------------------------------------------------------------- pump totalizer (meter) check
# Each pump has two running lifetime counters: the PESO (amount, "Money") totalizer and the LITER (volume)
# totalizer. Both are read at shift start (opening) and shift end (closing); dispensed = closing - opening.
COUNTERS = ("amount", "volume")
COUNTER_UNIT = {"amount": "PHP", "volume": "L"}
PRICE_TOLERANCE = Decimal(os.environ.get("PRICE_TOLERANCE", "0.05"))  # pesos per liter, covers rounding


def get_setting(key, default=None):
    r = rows("SELECT value FROM settings WHERE key=?", (key,))
    return r[0]["value"] if r else default


def set_setting(key, value):
    execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def del_setting(key):
    execute("DELETE FROM settings WHERE key=?", (key,))


def pump_tolerance():
    d = dec(get_setting("pump_tolerance_pct", PUMP_TOLERANCE_PCT))
    return d if d is not None and d >= 0 else Decimal(PUMP_TOLERANCE_PCT)


def clean_reading(value):
    """'775,397' / ' 775 397 ' / '7753.97' -> digits as shown ('775397', '7753.97'); None if not a number."""
    if value is None or isinstance(value, bool):
        return None
    s = re.sub(r"[\s,₱]", "", str(value))
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,4})?", s):
        return None
    return s


def reading_value(reading, decimals=0):
    """Readings are whole numbers as displayed (decimals 0, the default). If a pump hides decimal places,
    set them per counter: '775397' with 2 -> 7753.97. A reading typed with its own decimal point is used as is."""
    s = clean_reading(reading)
    if s is None:
        return None
    if "." in s:
        return Decimal(s)
    return Decimal(s).scaleb(-int(decimals or 0))


def fmt_qty(d, unit):
    """Liters: up to 3 decimals without trailing zeros ('181', '6.784'). Pesos: '₱1,234.50'."""
    if d is None:
        return None
    if unit == "PHP":
        return peso(d)
    d = q3(d)
    s = "{:,.3f}".format(d).rstrip("0").rstrip(".")
    return (s if s not in ("-0", "") else "0") + " L"


def _q_unit(d, unit):
    return q2(d) if unit == "PHP" else q3(d)


def dispensed(opening, closing, decimals=0):
    """closing - opening of a running total. Closing lower than opening is an error (misread, wrong decimals,
    or the counter rolled over), never a negative sale."""
    o, c = reading_value(opening, decimals), reading_value(closing, decimals)
    if o is None or c is None:
        return {"ok": False, "value": None, "error": None, "incomplete": True}
    if c < o:
        return {"ok": False, "value": None, "incomplete": False,
                "error": "Closing reading (%s) is lower than opening (%s). Check the photo and the decimals setting. "
                         "If the meter rolled over past its maximum, record it by hand." % (
                             clean_reading(closing), clean_reading(opening))}
    return {"ok": True, "value": c - o, "error": None, "incomplete": False}


def gap_check(pump_dispensed, recorded, unit="L", tolerance_pct=None):
    """Compare what the pump counter says was dispensed with what was recorded as sales. Pure code.

    gap > 0: fuel/money left the pump without a recorded sale (UNACCOUNTED).
    gap < 0: more was recorded than the pump dispensed (OVER_RECORDED: duplicate sale or a misread meter).
    """
    tol = pump_tolerance() if tolerance_pct is None else Decimal(str(tolerance_pct))
    D = _q_unit(Decimal(str(pump_dispensed)), unit)
    R = _q_unit(Decimal(str(recorded)), unit)
    gap = _q_unit(D - R, unit)
    pct = q2(gap / D * 100) if D != 0 else None
    if abs(gap) <= D * tol / 100:
        status = "OK"
    elif gap > 0:
        status = "UNACCOUNTED"
    else:
        status = "OVER_RECORDED"
    return {"unit": unit, "dispensed": str(D), "recorded": str(R), "gap": str(gap),
            "gap_pct": str(pct) if pct is not None else None, "tolerance_pct": str(tol), "status": status,
            "flag": status != "OK"}


def gap_headline(g, lang="en"):
    """'Pump says 181 L dispensed; recorded sales 174.216 L; 6.784 L (3.75%) unaccounted.' (code, not AI)"""
    u = g["unit"]
    D, R, G = Decimal(g["dispensed"]), Decimal(g["recorded"]), Decimal(g["gap"])
    pct = (" (%s%%)" % Decimal(g["gap_pct"]).copy_abs()) if g["gap_pct"] is not None else ""
    if lang == "tl":
        head = "Ayon sa metro ng pump, %s ang nailabas; %s ang naitalang benta" % (fmt_qty(D, u), fmt_qty(R, u))
        if g["status"] == "UNACCOUNTED":
            return "%s; %s%s ang hindi naitala." % (head, fmt_qty(G, u), pct)
        if g["status"] == "OVER_RECORDED":
            return "%s; %s%s ang sobra sa naitala kumpara sa metro." % (head, fmt_qty(-G, u), pct)
        return "%s; pasok sa %s%% na palugit." % (head, g["tolerance_pct"])
    head = "Pump says %s dispensed; recorded sales %s" % (fmt_qty(D, u), fmt_qty(R, u))
    if g["status"] == "UNACCOUNTED":
        return "%s; %s%s unaccounted." % (head, fmt_qty(G, u), pct)
    if g["status"] == "OVER_RECORDED":
        return "%s; %s%s more recorded than the pump shows." % (head, fmt_qty(-G, u), pct)
    return "%s; within the %s%% tolerance." % (head, g["tolerance_pct"])


# ---- price per liter sanity check (a WARNING, never a theft flag)
def _price_key(shift_id, fuel):
    return "price_change:%s:%s" % (shift_id, fuel)


def set_price_change(fuel, old_price, new_price, shift_id=None):
    """'Price changed this shift' for a fuel: any implied price between old and new passes. Empty clears it.
    Returns errors."""
    fuel = normalize_fuel(fuel)
    shift_id = shift_id or current_shift()["id"]
    if not fuel:
        return ["Choose the fuel."]
    if old_price in (None, "") and new_price in (None, ""):
        del_setting(_price_key(shift_id, fuel))
        return []
    o, n = dec(old_price), dec(new_price)
    if o is None or n is None or o <= 0 or n <= 0:
        return ["Enter both the old and the new price per liter."]
    set_setting(_price_key(shift_id, fuel), "%s,%s" % (q2(o), q2(n)))
    return []


def get_price_change(fuel, shift_id):
    v = get_setting(_price_key(shift_id, fuel))
    if not v:
        return None
    o, n = v.split(",")
    return {"old": o, "new": n}


def price_check(pesos, liters, recorded_prices=(), price_change=None, tolerance=None):
    """Implied price per liter = pesos dispensed / liters dispensed, compared with the posted price.

    Reference: the old..new range if the price changed this shift, else the price(s) used in recorded sales.
    Passes within +/- PRICE_TOLERANCE (P0.05/L) of that range. A miss is a warning to re-check readings."""
    tol = PRICE_TOLERANCE if tolerance is None else Decimal(str(tolerance))
    P, L = Decimal(str(pesos)), Decimal(str(liters))
    if L <= 0:
        return None
    implied = P / L
    if price_change:
        lo, hi = sorted([Decimal(price_change["old"]), Decimal(price_change["new"])])
        basis = "price change"
    elif recorded_prices:
        lo, hi = min(recorded_prices), max(recorded_prices)
        basis = "recorded sales"
    else:
        return {"implied": str(q2(implied)), "ok": None, "basis": "none", "low": None, "high": None,
                "tolerance": str(q2(tol)),
                "text": "Implied price %s/L (no posted price to compare)." % peso(implied),
                "text_tl": "Presyo mula sa metro: %s/L (walang presyong maikukumpara)." % peso(implied)}
    ok = lo - tol <= implied <= hi + tol
    ref = peso(lo) if lo == hi else "%s–%s" % (peso(lo), peso(hi))
    if ok:
        text = "Price check OK: implied %s/L (pesos ÷ liters) vs posted %s/L." % (peso(implied), ref)
        text_tl = "Tama ang presyo: %s/L mula sa metro vs %s/L na presyo." % (peso(implied), ref)
    else:
        text = ("Price check warning: implied %s/L (pesos ÷ liters) vs posted %s/L (±%s). Re-check the peso and "
                "liter readings, or set the price change for this shift. This is not a missing-fuel flag." % (
                    peso(implied), ref, peso(tol)))
        text_tl = ("Babala sa presyo: %s/L mula sa metro vs %s/L (±%s). Basahin ulit ang peso at litro sa metro, o "
                   "ilagay ang pagbabago ng presyo." % (peso(implied), ref, peso(tol)))
    return {"implied": str(q2(implied)), "ok": ok, "basis": basis, "low": str(q2(lo)), "high": str(q2(hi)),
            "tolerance": str(q2(tol)), "text": text, "text_tl": text_tl}


# ---- pumps and readings
def list_pumps():
    return rows("SELECT * FROM pumps ORDER BY name")


def _int_dec(v):
    try:
        d = int(v if v not in (None, "") else 0)
    except (TypeError, ValueError):
        return -1
    return d


def save_pump(data):
    """Create or update a pump/nozzle. Returns (pump, errors)."""
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:40]
    if name.isupper() or name.islower():
        name = name.title()  # "DIESEL 2" / "diesel 2" -> "Diesel 2"
    fuel = normalize_fuel(data.get("fuel_type"))
    pid = data.get("id")
    old = rows("SELECT * FROM pumps WHERE id=?", (int(pid),)) if pid else []
    old = old[0] if old else {}
    ad = _int_dec(data.get("amount_decimals", old.get("amount_decimals", 0)))
    vd = _int_dec(data.get("volume_decimals", old.get("volume_decimals", 0)))
    errors = []
    if not name:
        errors.append("Pump name is required (e.g. Diesel 2).")
    if not fuel:
        errors.append("Fuel type is required.")
    if ad not in PUMP_DECIMALS or vd not in PUMP_DECIMALS:
        errors.append("Decimal places must be 0, 1, 2 or 3.")
    if errors:
        return None, errors
    clash = rows("SELECT id FROM pumps WHERE name=?", (name,))
    if clash and (not pid or int(pid) != clash[0]["id"]):
        if pid:
            return None, ["Another pump is already named %s." % name]
        pid = clash[0]["id"]  # same name: update it
    if pid:
        execute("UPDATE pumps SET name=?, fuel_type=?, amount_decimals=?, volume_decimals=?, synced=0 WHERE id=?",
                (name, fuel, ad, vd, int(pid)))
    else:
        pid = execute("INSERT INTO pumps (name, fuel_type, amount_decimals, volume_decimals, created_at) "
                      "VALUES (?,?,?,?,?)", (name, fuel, ad, vd, now()))
    r = rows("SELECT * FROM pumps WHERE id=?", (int(pid),))
    return (r[0], []) if r else (None, ["Pump not found."])


def save_reading(pump_id, kind, amount=None, volume=None, source="manual", shift_id=None, created_at=None):
    """Store this shift's OPENING ('open') or CLOSING ('close') totalizers for a pump: the peso (amount) counter,
    the liter (volume) counter, or both. A counter left empty keeps its saved value. Returns (record, errors, warnings)."""
    kind = {"opening": "open", "closing": "close"}.get(kind, kind)
    if kind not in ("open", "close"):
        return None, ["Choose opening or closing."], []
    try:
        pump = rows("SELECT * FROM pumps WHERE id=?", (int(pump_id),))
    except (TypeError, ValueError):
        pump = []
    if not pump:
        return None, ["Choose a pump."], []
    pump = pump[0]
    vals, errors = {}, []
    for name, raw in (("amount", amount), ("volume", volume)):
        if raw in (None, ""):
            continue
        s = clean_reading(raw)
        if s is None:
            errors.append("The %s totalizer must be digits only, e.g. 775397." % ("peso" if name == "amount" else "liter"))
        else:
            vals[name] = s
    if not vals and not errors:
        errors.append("Enter the peso totalizer, the liter totalizer, or both.")
    if errors:
        return None, errors, []
    shift_id = shift_id or current_shift()["id"]
    source = source if source in ("photo", "manual", "seed", "carried") else "manual"
    key = (shift_id, pump["id"], kind)
    with _db_lock, connect() as conn:
        cur = conn.execute("SELECT * FROM totalizer_readings WHERE shift_id=? AND pump_id=? AND kind=?", key).fetchone()
        if cur is None:
            conn.execute("INSERT INTO totalizer_readings (shift_id, pump_id, kind, created_at) VALUES (?,?,?,?)",
                         key + (created_at or now(),))
        for name, s in vals.items():
            conn.execute("UPDATE totalizer_readings SET %s=?, %s_source=?, created_at=?, synced=0 WHERE shift_id=? AND pump_id=? "
                         "AND kind=?" % (name, name), (s, source, created_at or now()) + key)
        conn.commit()
    rec = rows("SELECT * FROM totalizer_readings WHERE shift_id=? AND pump_id=? AND kind=?", key)[0]
    other = rows("SELECT * FROM totalizer_readings WHERE shift_id=? AND pump_id=? AND kind=?",
                 (shift_id, pump["id"], "close" if kind == "open" else "open"))
    warnings = []
    if other:
        for name in COUNTERS:
            if rec[name] and other[0][name]:
                o, c = (rec[name], other[0][name]) if kind == "open" else (other[0][name], rec[name])
                d = dispensed(o, c, pump["%s_decimals" % name])
                if d["error"]:
                    warnings.append(("Peso" if name == "amount" else "Liter") + " totalizer: " + d["error"])
    return rec, [], warnings


def _pump_row(p, o, c):
    row = dict(p)
    row["error"], row["errors"] = None, []
    statuses = []
    for name in COUNTERS:
        unit, decs = COUNTER_UNIT[name], p["%s_decimals" % name]
        ov, cv = (o or {}).get(name), (c or {}).get(name)
        row["opening_" + name], row["closing_" + name] = ov, cv
        row["opening_%s_source" % name] = (o or {}).get(name + "_source")
        row["closing_%s_source" % name] = (c or {}).get(name + "_source")
        row["opening_%s_value" % name] = str(reading_value(ov, decs)) if ov else None
        row["closing_%s_value" % name] = str(reading_value(cv, decs)) if cv else None
        row["dispensed_" + name], row["dispensed_%s_text" % name] = None, None
        if not ov and not cv:
            st = "NONE"
        elif not (ov and cv):
            st = "INCOMPLETE"
        else:
            d = dispensed(ov, cv, decs)
            if d["error"]:
                st = "ERROR"
                msg = ("Peso" if name == "amount" else "Liter") + " totalizer: " + d["error"]
                row["errors"].append(msg)
                row["error"] = row["error"] or msg
            else:
                st = "OK"
                v = _q_unit(d["value"], unit)
                row["dispensed_" + name], row["dispensed_%s_text" % name] = str(v), fmt_qty(v, unit)
        row["status_" + name] = st
        statuses.append(st)
    if "ERROR" in statuses:
        row["status"] = "ERROR"
    elif all(s == "NONE" for s in statuses):
        row["status"] = "NONE"
    elif "OK" in statuses and "INCOMPLETE" not in statuses:
        row["status"] = "OK"
    else:
        row["status"] = "INCOMPLETE"
    return row


STATUS_RANK = {"ERROR": 0, "UNACCOUNTED": 1, "OVER_RECORDED": 2, "OK": 3}


def pump_check(shift_id=None, tolerance_pct=None):
    """Per pump: both totalizers, dispensed pesos and liters. Per fuel: pump totals vs recorded sales (liters vs
    sales liters, pesos vs gross sales pesos), plus the implied price per liter as a sanity check (warning only)."""
    shift = rows("SELECT * FROM shifts WHERE id=?", (shift_id,))[0] if shift_id else current_shift()
    sid = shift["id"]
    tol = pump_tolerance() if tolerance_pct is None else Decimal(str(tolerance_pct))
    readings = {(r["pump_id"], r["kind"]): r for r in rows("SELECT * FROM totalizer_readings WHERE shift_id=?", (sid,))}
    sales = {}
    for s in rows("SELECT fuel_type, liters, amount_pesos, price_per_liter FROM sales WHERE shift_id=? AND voided=0",
                  (sid,)):
        t = sales.setdefault(s["fuel_type"], {"volume": Decimal(0), "amount": Decimal(0), "count": 0, "prices": set()})
        t["volume"] += Decimal(s["liters"])
        t["amount"] += Decimal(s["amount_pesos"])  # gross: the pump's peso counter doesn't know about discounts
        t["count"] += 1
        t["prices"].add(Decimal(s["price_per_liter"]))
    pumps, groups = [], {}
    for p in list_pumps():
        row = _pump_row(p, readings.get((p["id"], "open")), readings.get((p["id"], "close")))
        pumps.append(row)
        if row["status"] == "NONE":
            continue  # pump not read this shift: leave it out of the comparison
        g = groups.setdefault(p["fuel_type"], {"pumps": [], "rows": []})
        g["pumps"].append(p["name"])
        g["rows"].append(row)
    out = []
    order = {f: i for i, f in enumerate(FUELS)}
    for fuel, g in sorted(groups.items(), key=lambda kv: (order.get(kv[0], 9), kv[0])):
        sold = sales.get(fuel, {"volume": Decimal(0), "amount": Decimal(0), "count": 0, "prices": set()})
        item = {"fuel_type": fuel, "pumps": g["pumps"], "sales_count": sold["count"], "tolerance_pct": str(tol),
                "liters": None, "pesos": None, "price": None, "price_change": get_price_change(fuel, sid)}
        errs = [e for r in g["rows"] for e in ["%s: %s" % (r["name"], m) for m in r["errors"]]]
        waiting = []
        for name, key in (("volume", "liters"), ("amount", "pesos")):
            sts = [r["status_" + name] for r in g["rows"]]
            if any(s == "INCOMPLETE" for s in sts):
                waiting.append(name)
            if "ERROR" in sts or "INCOMPLETE" in sts or "OK" not in sts:
                continue
            total = sum((Decimal(r["dispensed_" + name]) for r in g["rows"] if r["status_" + name] == "OK"),
                        Decimal(0))
            c = gap_check(total, sold[name], COUNTER_UNIT[name], tol)
            c["headline"], c["headline_tl"] = gap_headline(c, "en"), gap_headline(c, "tl")
            item[key] = c
        if errs:
            item.update(status="ERROR", flag=True, headline=" ".join(errs),
                        headline_tl=" ".join(errs).replace("is lower than opening", "ay mas mababa sa opening"))
        elif not (item["liters"] or item["pesos"]):
            names = ", ".join(r["name"] for r in g["rows"] if r["status"] == "INCOMPLETE") or ", ".join(g["pumps"])
            item.update(status="INCOMPLETE", flag=False,
                        headline="Waiting for the closing (or opening) totalizer of %s." % names,
                        headline_tl="Hinihintay pa ang closing (o opening) na reading ng %s." % names)
        else:
            parts = [item[k] for k in ("liters", "pesos") if item[k]]
            item["status"] = min((p["status"] for p in parts), key=lambda s: STATUS_RANK[s])
            item["flag"] = item["status"] != "OK"
            item["headline"] = " ".join(p["headline"] for p in parts)
            item["headline_tl"] = " ".join(p["headline_tl"] for p in parts)
            if waiting:
                item["headline"] += " (%s totalizer not complete yet.)" % " and ".join(
                    "peso" if w == "amount" else "liter" for w in waiting)
                item["headline_tl"] += " (Kulang pa ang reading ng %s.)" % " at ".join(
                    "peso" if w == "amount" else "litro" for w in waiting)
            if item["liters"] and item["pesos"]:
                item["price"] = price_check(item["pesos"]["dispensed"], item["liters"]["dispensed"],
                                            sorted(sold["prices"]), item["price_change"])
        out.append(item)
    return {"shift_id": sid, "tolerance_pct": str(tol), "price_tolerance": str(q2(PRICE_TOLERANCE)), "pumps": pumps,
            "groups": out, "flagged": [g for g in out if g.get("flag")],
            "price_warnings": [g for g in out if g.get("price") and g["price"]["ok"] is False]}


# ---------------------------------------------------------------- expenses / petty cash out
def save_expense(data, shift_id=None, created_at=None):
    """Petty cash taken out of the drawer. Returns (record, errors)."""
    amount = dec(data.get("amount_pesos", data.get("amount")))
    desc = re.sub(r"\s+", " ", str(data.get("description") or "")).strip()[:120]
    errors = []
    if amount is None or amount <= 0:
        errors.append("Enter the expense amount in pesos.")
    elif amount > Decimal("100000"):
        errors.append("Expense amount looks too large. Please check.")
    if not desc:
        errors.append("Enter what the expense was for.")
    if errors:
        return None, errors
    source = data.get("source") if data.get("source") in ("photo", "manual", "seed") else "manual"
    eid = execute("INSERT INTO expenses (shift_id, created_at, amount_pesos, description, source) VALUES (?,?,?,?,?)",
                  (shift_id or current_shift()["id"], created_at or now(), str(q2(amount)), desc, source))
    return rows("SELECT * FROM expenses WHERE id=?", (eid,))[0], []


def void_expense(expense_id):
    execute("UPDATE expenses SET voided=1, synced=0 WHERE id=?", (int(expense_id),))


# ---------------------------------------------------------------- shift closing slip (paper) vs this shift
def _name_tokens(name):
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(t) >= 2}


def slip_compare(slip, shift_id=None):
    """Compare a read slip (slipparse.parse result, possibly edited by staff) with what this shift already has.

    Expenses: a slip item with the same amount as an unmatched recorded expense is 'recorded', else 'new'.
    Credit: same amount and a shared name word ('Mang Ben' ~ 'Mang Ben (trucking)') is 'recorded', else 'new'.
    Discounts can only be entered per sale, so the slip total is compared with the recorded total.
    The preview is the cash check as it would be after saving the new items. Pure code."""
    s = shift_summary(shift_id)
    free_exp = [dict(e) for e in s["expenses"]]
    expenses = []
    for item in slip.get("expenses") or []:
        amt = dec(item.get("amount_pesos"))
        e = dict(item, amount_pesos=str(q2(amt)) if amt is not None else None, status="new", matched=None)
        if amt is None or amt <= 0:
            e["status"] = "invalid"
        else:
            hit = next((r for r in free_exp if Decimal(r["amount_pesos"]) == q2(amt)), None)
            if hit:
                free_exp.remove(hit)
                e.update(status="recorded", matched=hit["description"])
        expenses.append(e)
    free_cr = [dict(c) for c in s["credit_sales"]]
    credits = []
    for item in slip.get("credits") or []:
        amt = dec(item.get("amount_pesos"))
        c = dict(item, amount_pesos=str(q2(amt)) if amt is not None else None, status="new", matched=None,
                 same_amount_as=None)
        if amt is None or amt <= 0:
            c["status"] = "invalid"
        else:
            same = [r for r in free_cr if Decimal(r["amount"]) == q2(amt)]
            hit = next((r for r in same if _name_tokens(r["customer"]) & _name_tokens(item.get("customer"))), None)
            if hit:
                free_cr.remove(hit)
                c.update(status="recorded", matched=hit["customer"])
            elif same:
                c["same_amount_as"] = same[0]["customer"]
        credits.append(c)
    new_exp = sum((Decimal(e["amount_pesos"]) for e in expenses if e["status"] == "new"), Decimal(0))
    new_cr = sum((Decimal(c["amount_pesos"]) for c in credits if c["status"] == "new"), Decimal(0))
    slip_disc = dec(slip.get("discounts"))
    rec_disc = Decimal(s["discounts_total"])
    discounts = {"slip": str(q2(slip_disc)) if slip_disc is not None else None, "recorded": str(q2(rec_disc)),
                 "match": None if slip_disc is None else q2(slip_disc) == rec_disc}
    noncash = dec(slip.get("noncash"))
    if noncash is None and (slip.get("gcash") or slip.get("card")):
        noncash = (dec(slip.get("gcash")) or Decimal(0)) + (dec(slip.get("card")) or Decimal(0))
    preview = None
    if dec(slip.get("cash_counted")) is not None:
        # A new credit sale adds to gross sales AND to credit, so it doesn't change expected cash.
        preview = compute_cash(Decimal(s["total_amount"]) + new_cr, slip.get("cash_counted"),
                               slip.get("opening_float"), noncash, discounts=rec_disc,
                               credit=Decimal(s["credit_total"]) + new_cr,
                               expenses=Decimal(s["expenses_total"]) + new_exp)
    return {"expenses": expenses, "credits": credits, "discounts": discounts,
            "noncash": str(q2(noncash)) if noncash is not None else None, "preview": preview,
            "new_expenses_total": str(q2(new_exp)), "new_credit_total": str(q2(new_cr))}


def slip_apply(slip, shift_id=None):
    """Save the slip's NEW expenses and credit sales (items already recorded are skipped, so pressing confirm
    twice adds nothing). Returns {"saved_expenses", "saved_credits", "skipped", "errors"}."""
    cmp_ = slip_compare(slip, shift_id)
    saved_e, saved_c, skipped, errors = [], [], [], []
    for e in cmp_["expenses"]:
        if e["status"] != "new":
            skipped.append("%s %s (%s)" % (e.get("description") or "Expense", peso(e["amount_pesos"] or 0), e["status"]))
            continue
        rec, errs = save_expense({"amount_pesos": e["amount_pesos"], "description": e.get("description") or "Expense",
                                  "source": "photo"}, shift_id=shift_id)
        (errors.extend(errs) if errs else saved_e.append(rec))
    for c in cmp_["credits"]:
        if c["status"] != "new":
            skipped.append("%s %s (%s)" % (c.get("customer") or "Credit", peso(c["amount_pesos"] or 0), c["status"]))
            continue
        rec, errs, _ = save_sale({"payment": "credit", "customer": c.get("customer"), "fuel_type": c.get("fuel_type"),
                                  "amount_pesos": c["amount_pesos"], "source": "photo"}, shift_id=shift_id)
        (errors.extend(["%s: %s" % (c.get("customer") or "Credit", m) for m in errs]) if errs else saved_c.append(rec))
    return {"saved_expenses": saved_e, "saved_credits": saved_c, "skipped": skipped, "errors": errors}
