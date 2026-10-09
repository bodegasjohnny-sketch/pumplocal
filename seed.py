"""Load a realistic demo shift so the demo works immediately.

Run:  python3 seed.py          (only seeds if the database is empty)
      python3 seed.py --reset  (wipes local data and seeds again)

Prices are demo values for this hackathon, not live pump prices.
"""
import os
import sys
from datetime import datetime, timedelta

import core

DEMO_PRICES = {"Premium": "64.99", "Unleaded": "61.25", "Diesel": "57.40"}

# (minutes after shift start, fuel, peso amount). Liters are computed in code from pesos / price.
DEMO_SALES = [
    (8, "Unleaded", "500"), (15, "Diesel", "1000"), (22, "Premium", "1000"), (31, "Diesel", "2500"),
    (40, "Unleaded", "200"), (47, "Premium", "300"), (55, "Diesel", "1500"), (63, "Unleaded", "1000"),
    (71, "Diesel", "3000"), (80, "Premium", "500"), (92, "Unleaded", "150"), (104, "Diesel", "800"),
    (118, "Premium", "2000"), (131, "Unleaded", "700"), (145, "Diesel", "1200"),
]


# Extras on the seeded sales (minute of the sale -> fields): one senior discount, one credit (utang) sale.
DEMO_SALE_EXTRAS = {
    118: {"discount_pesos": "50", "discount_reason": "senior"},
    71: {"payment": "credit", "customer": "Mang Ben (trucking)"},
}
# Petty cash out of the drawer: (minutes after shift start, pesos, what for).
DEMO_EXPENSES = [(60, "150", "Ice and drinking water"), (125, "350", "Nozzle O-ring (hardware)")]

# Pump totalizer demo. OPENING 775397 is the reading on the REAL photo samples/real_totalizer_diesel2.png.
# CLOSING 775578 is DEMO DATA (made up): with 0 decimals in liters the pump says 181 L of diesel left the pump,
# while the seeded diesel sales add up to 174.216 L (P10,000 at P57.40/L), so 6.784 L (3.75%) is unaccounted.
DEMO_PUMP = {"name": "Diesel 2", "fuel_type": "Diesel", "unit": "L", "decimals": 0}
DEMO_OPENING, DEMO_CLOSING = "775397", "775578"


def seed_pumps(shift_id, start):
    pump, errors = core.save_pump(DEMO_PUMP)
    assert not errors, errors
    for minutes, kind, value in ((0, "open", DEMO_OPENING), (175, "close", DEMO_CLOSING)):
        when = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        rec, errors, _ = core.save_reading(pump["id"], kind, value, "seed", shift_id=shift_id, created_at=when)
        assert not errors, errors
    return pump


def seed_pumps_if_demo():
    """Add the pump demo to a database seeded before this feature existed (only the untouched demo shift)."""
    core.init_db()
    if core.list_pumps():
        return False
    shift = core.rows("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1")
    if not shift or shift[0]["attendant"] != "Demo Attendant":
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
    for minutes, fuel, amount in DEMO_SALES:
        when = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        data = dict({"fuel_type": fuel, "price_per_liter": DEMO_PRICES[fuel], "amount_pesos": amount, "source": "seed"},
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


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
