"""Load a realistic demo shift so the demo works immediately.

Run:  python3 seed.py          (only seeds if the database is empty)
      python3 seed.py --reset  (wipes local data and seeds again)

Prices are the real JCB pump prices as of Oct 9, 2026 (Premium 85.90, Unleaded 85.40, Diesel 94.50).
Sales, customers, expenses and pump readings are demo data (made up), except the real opening peso totalizer 775397.
"""
import os
import sys
from datetime import datetime, timedelta

import core

DEMO_PRICES = {"Premium": "85.90", "Unleaded": "85.40", "Diesel": "94.50"}  # real JCB prices, Oct 9, 2026

# (minutes after shift start, fuel, liters). Pesos are computed in code: liters x price, rounded half-up,
# so every seeded sale is exactly consistent.
DEMO_SALES = [
    (8, "Unleaded", "6"), (15, "Diesel", "10"), (22, "Premium", "12"), (31, "Diesel", "25"),
    (40, "Unleaded", "2.5"), (47, "Premium", "3.5"), (55, "Diesel", "15"), (63, "Unleaded", "12"),
    (71, "Diesel", "30"), (80, "Premium", "6"), (92, "Unleaded", "2"), (104, "Diesel", "8.5"),
    (118, "Premium", "23"), (131, "Unleaded", "8"), (145, "Diesel", "12.5"),
]


# Extras on the seeded sales (minute of the sale -> fields): one senior discount, one credit (utang) sale.
DEMO_SALE_EXTRAS = {
    118: {"discount_pesos": "50", "discount_reason": "senior"},
    71: {"payment": "credit", "customer": "Mang Ben (trucking)"},
}
# Petty cash out of the drawer: (minutes after shift start, pesos, what for).
DEMO_EXPENSES = [(60, "150", "Ice and drinking water"), (125, "350", "Nozzle O-ring (hardware)")]

# Pump totalizer demo: two running totals per pump, read as whole numbers as displayed.
# PESO (Money) opening 775397 is the number on the REAL photo samples/real_totalizer_diesel2.png.
# Everything else here is DEMO DATA (made up): the LITER (Volume) opening 13508, and both closings.
# Pump says 105 L and P9,922 left the pump (implied P94.50/L, the posted price); the seeded diesel sales add up
# to 101 L and P9,544.50, so 4 L (3.81%) and P377.50 (3.80%) are unaccounted.
DEMO_PUMP = {"name": "Diesel 2", "fuel_type": "Diesel", "amount_decimals": 0, "volume_decimals": 0}
DEMO_OPENING = {"amount": "775397", "volume": "13508"}
DEMO_CLOSING = {"amount": "785319", "volume": "13613"}


def seed_pumps(shift_id, start):
    pump, errors = core.save_pump(DEMO_PUMP)
    assert not errors, errors
    for minutes, kind, vals in ((0, "open", DEMO_OPENING), (175, "close", DEMO_CLOSING)):
        when = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        rec, errors, _ = core.save_reading(pump["id"], kind, vals["amount"], vals["volume"], "seed",
                                           shift_id=shift_id, created_at=when)
        assert not errors, errors
    return pump


def seed_pumps_if_demo():
    """Add the pump demo to a database seeded before this feature existed (only the untouched demo shift)."""
    core.init_db()
    shift = core.rows("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1")
    if not shift or shift[0]["attendant"] != "Demo Attendant":
        return False
    if core.rows("SELECT id FROM totalizer_readings WHERE shift_id=?", (shift[0]["id"],)):
        return False
    start = datetime.strptime(shift[0]["opened_at"], "%Y-%m-%d %H:%M:%S")
    seed_pumps(shift[0]["id"], start)
    return True


def seed(reset=False):
    if reset and os.path.exists(core.DB_PATH):
        os.remove(core.DB_PATH)
    core.init_db()
    if not core.is_empty():
        print("Database already has data; not seeding. Use --reset to start over.")
        return False
    start = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    sid = core.execute("INSERT INTO shifts (opened_at, attendant) VALUES (?, ?)",
                       (start.strftime("%Y-%m-%d %H:%M:%S"), "Demo Attendant"))
    for minutes, fuel, liters in DEMO_SALES:
        when = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        data = dict({"fuel_type": fuel, "price_per_liter": DEMO_PRICES[fuel], "liters": liters, "source": "seed"},
                    **DEMO_SALE_EXTRAS.get(minutes, {}))
        rec, errors, _ = core.save_sale(data, shift_id=sid, created_at=when)
        assert not errors, errors
    for minutes, amount, desc in DEMO_EXPENSES:
        when = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        rec, errors = core.save_expense({"amount_pesos": amount, "description": desc, "source": "seed"},
                                        shift_id=sid, created_at=when)
        assert not errors, errors
    seed_pumps(sid, start)
    s = core.shift_summary(sid)
    print("Seeded demo shift #%d: %d sales, %s, %s L" % (sid, s["count"], core.peso(s["total_amount"]),
                                                        s["total_liters"]))
    print("  Discounts %s, credit (utang) %s, expenses %s" % (
        core.peso(s["discounts_total"]), core.peso(s["credit_total"]), core.peso(s["expenses_total"])))
    for g in s["pump_check"]["groups"]:
        print("  Pump check (demo): %s" % g["headline"])
    return True


# ---- Demo Day: an EMPTY shift, so everything on screen comes from the photos read live (see DEMO_SCRIPT.md).
DEMO_DAY_FLOAT = "1000.00"
DEMO_DAY_OPENING = {"amount": "775397", "volume": "13508"}  # 775397 = the REAL photo; 13508 is demo data


def seed_demo_empty():
    """Fresh database: one open shift with the opening float, posted prices and the Diesel 2 OPENING totalizers
    preset. No sales, expenses, credit or cash checks."""
    if os.path.exists(core.DB_PATH):
        os.remove(core.DB_PATH)
    core.init_db()
    sid = core.execute("INSERT INTO shifts (opened_at, attendant) VALUES (?, ?)",
                       (datetime.now().replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"), "Demo Day"))
    core.set_setting("opening_float:%s" % sid, DEMO_DAY_FLOAT)
    for fuel, price in DEMO_PRICES.items():
        core.set_setting("price:%s" % fuel, price)
    pump, errors = core.save_pump(DEMO_PUMP)
    assert not errors, errors
    rec, errors, _ = core.save_reading(pump["id"], "open", DEMO_DAY_OPENING["amount"], DEMO_DAY_OPENING["volume"],
                                       "manual", shift_id=sid)
    assert not errors, errors
    print("Demo Day shift #%d ready: no sales yet. Opening float %s; prices %s; Diesel 2 opening: peso %s, liter %s."
          % (sid, core.peso(DEMO_DAY_FLOAT), ", ".join("%s %s" % (f, core.peso(p)) for f, p in DEMO_PRICES.items()),
             DEMO_DAY_OPENING["amount"], DEMO_DAY_OPENING["volume"]))
    return sid


if __name__ == "__main__":
    if "--demo-empty" in sys.argv:
        seed_demo_empty()
    else:
        seed(reset="--reset" in sys.argv)
