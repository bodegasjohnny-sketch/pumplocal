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
PUMP_UNITS = ("L", "PHP")
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
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

SYNC_TABLES = ("shifts", "sales", "cash_checks")


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
            conn.execute("INSERT INTO pump_readings (shift_id, pump_id, kind, reading, source, created_at) "
                         "SELECT ?, pump_id, 'open', reading, 'carried', ? FROM pump_readings "
                         "WHERE shift_id=? AND kind='close'", (sid, now(), old["id"]))
        conn.commit()
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


# ---------------------------------------------------------------- pump totalizer (meter) check
def get_setting(key, default=None):
    r = rows("SELECT value FROM settings WHERE key=?", (key,))
    return r[0]["value"] if r else default


def set_setting(key, value):
    execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def pump_tolerance():
    d = dec(get_setting("pump_tolerance_pct", PUMP_TOLERANCE_PCT))
    return d if d is not None and d >= 0 else Decimal(PUMP_TOLERANCE_PCT)


def clean_reading(value):
    """'775,397' / ' 775 397 ' / '7753.97' -> digits as shown ('775397', '7753.97'); None if not a number."""
    if value is None or isinstance(value, bool):
        return None
    s = re.sub(r"[\s,]", "", str(value))
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,4})?", s):
        return None
    return s


def reading_value(reading, decimals=0):
    """Apply the pump's hidden decimal places: '775397' with 2 -> 7753.97, with 3 -> 775.397.
    A reading typed with its own decimal point is taken as is."""
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
    """closing - opening in the pump's unit. Closing lower than opening is an error (misread, wrong decimals,
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
    """Compare what the pump meter says was dispensed with what was recorded as sales. Pure code.

    gap > 0: fuel left the pump without a recorded sale (UNACCOUNTED).
    gap < 0: more was recorded than the pump dispensed (OVER_RECORDED: duplicate sale or a misread meter).
    """
    tol = pump_tolerance() if tolerance_pct is None else Decimal(str(tolerance_pct))
    D = _q_unit(Decimal(str(pump_dispensed)), unit)
    R = _q_unit(Decimal(str(recorded)), unit)
    gap = _q_unit(D - R, unit)
    pct = q2(gap / D * 100) if D != 0 else None
    allowed = D * tol / 100
    if abs(gap) <= allowed:
        status = "OK"
    elif gap > 0:
        status = "UNACCOUNTED"
    else:
        status = "OVER_RECORDED"
    return {"unit": unit, "dispensed": str(D), "recorded": str(R), "gap": str(gap),
            "gap_pct": str(pct) if pct is not None else None, "tolerance_pct": str(tol), "status": status,
            "flag": status != "OK"}


def gap_headline(g, lang="en"):
    """'Pump says 181 L dispensed; recorded sales 174.216 L; 6.784 L (3.75%) unaccounted' (code, not AI)."""
    u = g["unit"]
    D, R, G = Decimal(g["dispensed"]), Decimal(g["recorded"]), Decimal(g["gap"])
    pct = (" (%s%%)" % Decimal(g["gap_pct"]).copy_abs()) if g["gap_pct"] is not None else ""
    what = "sales" if lang == "en" else "benta"
    if lang == "tl":
        head = "Ayon sa metro ng pump, %s ang nailabas; %s ang naitalang %s" % (fmt_qty(D, u), fmt_qty(R, u), what)
        if g["status"] == "UNACCOUNTED":
            return "%s; %s%s ang hindi naitala." % (head, fmt_qty(G, u), pct)
        if g["status"] == "OVER_RECORDED":
            return "%s; %s%s ang sobra sa naitala kumpara sa metro." % (head, fmt_qty(-G, u), pct)
        return "%s; pasok sa %s%% na palugit." % (head, g["tolerance_pct"])
    head = "Pump says %s dispensed; recorded %s %s" % (fmt_qty(D, u), what, fmt_qty(R, u))
    if g["status"] == "UNACCOUNTED":
        return "%s; %s%s unaccounted." % (head, fmt_qty(G, u), pct)
    if g["status"] == "OVER_RECORDED":
        return "%s; %s%s more recorded than the pump shows." % (head, fmt_qty(-G, u), pct)
    return "%s; within the %s%% tolerance." % (head, g["tolerance_pct"])


def list_pumps():
    return rows("SELECT * FROM pumps ORDER BY name")


def save_pump(data):
    """Create or update a pump/nozzle. Returns (pump, errors)."""
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:40]
    if name.isupper() or name.islower():
        name = name.title()  # "DIESEL 2" / "diesel 2" -> "Diesel 2"
    fuel = normalize_fuel(data.get("fuel_type"))
    unit = str(data.get("unit") or "L").upper()
    unit = {"LITERS": "L", "LITRES": "L", "PESOS": "PHP", "₱": "PHP", "P": "PHP"}.get(unit, unit)
    try:
        decimals = int(data.get("decimals") if data.get("decimals") not in (None, "") else 0)
    except (TypeError, ValueError):
        decimals = -1
    errors = []
    if not name:
        errors.append("Pump name is required (e.g. Diesel 2).")
    if not fuel:
        errors.append("Fuel type is required.")
    if unit not in PUMP_UNITS:
        errors.append("Unit must be Liters (L) or Pesos (PHP).")
    if decimals not in PUMP_DECIMALS:
        errors.append("Decimal places must be 0, 1, 2 or 3.")
    if errors:
        return None, errors
    pid = data.get("id")
    clash = rows("SELECT id FROM pumps WHERE name=?", (name,))
    if clash and (not pid or int(pid) != clash[0]["id"]):
        if pid:
            return None, ["Another pump is already named %s." % name]
        pid = clash[0]["id"]  # same name: update it
    if pid:
        execute("UPDATE pumps SET name=?, fuel_type=?, unit=?, decimals=? WHERE id=?",
                (name, fuel, unit, decimals, int(pid)))
    else:
        pid = execute("INSERT INTO pumps (name, fuel_type, unit, decimals, created_at) VALUES (?,?,?,?,?)",
                      (name, fuel, unit, decimals, now()))
    r = rows("SELECT * FROM pumps WHERE id=?", (int(pid),))
    return (r[0], []) if r else (None, ["Pump not found."])


def save_reading(pump_id, kind, reading, source="manual", shift_id=None, created_at=None):
    """Store this shift's OPENING ('open') or CLOSING ('close') totalizer for a pump (re-saving replaces it).
    Returns (record, errors, warnings)."""
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
    s = clean_reading(reading)
    if s is None:
        return None, ["Enter the totalizer reading (digits only, e.g. 775397)."], []
    shift_id = shift_id or current_shift()["id"]
    source = source if source in ("photo", "manual", "seed", "carried") else "manual"
    execute("INSERT OR REPLACE INTO pump_readings (shift_id, pump_id, kind, reading, source, created_at) "
            "VALUES (?,?,?,?,?,?)", (shift_id, pump["id"], kind, s, source, created_at or now()))
    rec = rows("SELECT * FROM pump_readings WHERE shift_id=? AND pump_id=? AND kind=?", (shift_id, pump["id"], kind))[0]
    warnings = []
    other = rows("SELECT reading FROM pump_readings WHERE shift_id=? AND pump_id=? AND kind=?",
                 (shift_id, pump["id"], "close" if kind == "open" else "open"))
    if other:
        o, c = (s, other[0]["reading"]) if kind == "open" else (other[0]["reading"], s)
        d = dispensed(o, c, pump["decimals"])
        if d["error"]:
            warnings.append(d["error"])
    return rec, [], warnings


def pump_check(shift_id=None, tolerance_pct=None):
    """Per pump: opening, closing, dispensed. Per fuel (and unit): pump total vs recorded sales -> gap."""
    shift = rows("SELECT * FROM shifts WHERE id=?", (shift_id,))[0] if shift_id else current_shift()
    sid = shift["id"]
    tol = pump_tolerance() if tolerance_pct is None else Decimal(str(tolerance_pct))
    readings = {}
    for r in rows("SELECT * FROM pump_readings WHERE shift_id=?", (sid,)):
        readings[(r["pump_id"], r["kind"])] = r
    sales = {}
    for s in rows("SELECT fuel_type, liters, amount_pesos FROM sales WHERE shift_id=? AND voided=0", (sid,)):
        t = sales.setdefault(s["fuel_type"], {"L": Decimal(0), "PHP": Decimal(0), "count": 0})
        t["L"] += Decimal(s["liters"])
        t["PHP"] += Decimal(s["amount_pesos"])
        t["count"] += 1
    pumps, groups = [], {}
    for p in list_pumps():
        o, c = readings.get((p["id"], "open")), readings.get((p["id"], "close"))
        row = dict(p, opening=o["reading"] if o else None, closing=c["reading"] if c else None,
                   opening_source=o["source"] if o else None, closing_source=c["source"] if c else None,
                   opening_value=None, closing_value=None, dispensed=None, dispensed_text=None, error=None)
        for k, r in (("opening_value", o), ("closing_value", c)):
            if r:
                row[k] = str(reading_value(r["reading"], p["decimals"]))
        if not o and not c:
            row["status"] = "NONE"
        elif not (o and c):
            row["status"] = "INCOMPLETE"
        else:
            d = dispensed(o["reading"], c["reading"], p["decimals"])
            if d["error"]:
                row["status"], row["error"] = "ERROR", d["error"]
            else:
                v = _q_unit(d["value"], p["unit"])
                row["status"], row["dispensed"], row["dispensed_text"] = "OK", str(v), fmt_qty(v, p["unit"])
        pumps.append(row)
        if row["status"] == "NONE":
            continue  # pump not read this shift: leave it out of the comparison
        g = groups.setdefault((p["fuel_type"], p["unit"]), {"fuel_type": p["fuel_type"], "unit": p["unit"],
                                                              "pumps": [], "total": Decimal(0), "problems": []})
        g["pumps"].append(p["name"])
        if row["status"] == "OK":
            g["total"] += Decimal(row["dispensed"])
        else:
            g["problems"].append(row)
    out = []
    order = {f: i for i, f in enumerate(FUELS)}
    for (fuel, unit), g in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][0], kv[0][1])):
        sold = sales.get(fuel, {"L": Decimal(0), "PHP": Decimal(0), "count": 0})
        item = {"fuel_type": fuel, "unit": unit, "pumps": g["pumps"], "sales_count": sold["count"]}
        errs = [r for r in g["problems"] if r["status"] == "ERROR"]
        if errs:
            item.update(status="ERROR", flag=True, tolerance_pct=str(tol),
                        headline="%s: %s" % (errs[0]["name"], errs[0]["error"]),
                        headline_tl="%s: Mas mababa ang closing kaysa opening. Suriin ang litrato at decimals." %
                        errs[0]["name"])
        elif g["problems"]:
            names = ", ".join(r["name"] for r in g["problems"])
            item.update(status="INCOMPLETE", flag=False, tolerance_pct=str(tol),
                        headline="Waiting for the %s reading of %s." % (
                            "closing" if g["problems"][0]["opening"] else "opening", names),
                        headline_tl="Hinihintay pa ang %s na reading ng %s." % (
                            "closing" if g["problems"][0]["opening"] else "opening", names))
        else:
            item.update(gap_check(g["total"], sold[unit], unit, tol))
            item["headline"] = gap_headline(item, "en")
            item["headline_tl"] = gap_headline(item, "tl")
        out.append(item)
    return {"shift_id": sid, "tolerance_pct": str(tol), "pumps": pumps, "groups": out,
            "flagged": [g for g in out if g.get("flag")]}
