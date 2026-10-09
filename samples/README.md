# Sample images

## Synthetic (computer-generated)

These images are **synthetic**, generated with Pillow by `make_samples.py`. They are not photos of a real pump or receipt.
Prices are the real JCB pump prices as of Oct 9, 2026 (Premium ₱85.90, Unleaded ₱85.40, Diesel ₱94.50), and on every
sample liters × price = amount, rounded half-up.

| File | Shows | Expected reading |
|---|---|---|
| `meter_diesel.png` | Pump meter | Diesel, 15.000 L, ₱94.50/L, ₱1,417.50 |
| `meter_unleaded.png` | Pump meter | Unleaded, 6.000 L, ₱85.40/L, ₱512.40 |
| `receipt_diesel.png` | Thermal receipt (slightly rotated) | Diesel, 21.164 L, ₱94.50/L, ₱2,000.00 (21.164 × 94.50 = 1,999.998, rounded half-up) |

In the app, tap a thumbnail under "Try a sample" on the Photo tab.

| File | Shows | Expected reading |
|---|---|---|
| `closing_slip_photo.jpg` / `closing_slip_clean.png` | Filled Shift Closing Slip (phone photo with tilt and shadow / flat scan), made by `make_slip.py` | See `closing_slip_answer.json`: float ₱1,000; expenses ₱60 + ₱350; discounts ₱20; credit Mang Ben Diesel ₱945 (10 L); GCash ₱500; card ₱300; cash counted ₱39,169.55 (Demo Day: expected ₱39,219.55 → SHORT ₱50.00) |
| `archive/synthetic_totalizer_diesel2_close_money.png` | **SYNTHETIC** Diesel 2 "2.Money All" closing screen, made by `make_totalizer_close.py` (not in the demo any more) | Peso totalizer 778421 (closing) |
| `archive/synthetic_totalizer_diesel2_close_volume.png` | **SYNTHETIC** Diesel 2 "1.Volume All" closing screen (not in the demo any more) | Liter totalizer 13540 (closing) |

`archive/meter_premium.png` (Premium 12.000 L, ₱1,030.80) is the old Premium meter sample, kept for tests. The archived closing totalizer screens imitate the layout of the real photo but are generated, with made-up closings chosen to
fit the real opening 775397 for the Demo Day script (32 L / ₱3,024 dispensed, implied ₱94.50/L). Each image says "SYNTHETIC SAMPLE".

## REAL photos

| File | Shows | Expected reading |
|---|---|---|
| `real_premium3/premium3_opening_shift_pesos.png` | **REAL** Premium 3 LCD "2.Money All / Volume 2559778" | **Peso** totalizer 2559778 (opening = the previous day's closing) |
| `real_premium3/premium3_opening_shift_liters.png` | **REAL** Premium 3 "1.Report Oil / Volume 32333.73 lite" ("liters" cut off on screen) | **Liter** totalizer 32333.73 (opening) |
| `real_premium3/premium3_closing_shift_pesos.png` | **REAL** Premium 3 "2.Money All / Volume 2595535" | **Peso** totalizer 2595535 (closing) |
| `real_premium3/premium3_closing_shift_liters.png` | **REAL** Premium 3 "1.Report Oil / Volume 32749.80 lite" | **Liter** totalizer 32749.80 (closing) |
| `archive/real_totalizer_diesel2.png` | **REAL photo** from the team's own station: a pump's LCD totalizer menu ("2.Money All", "Volume 775397", Cancel / Ok) with the sticker label "DIESEL 2" | **Peso** (money) totalizer 775397, whole number; pump Diesel 2, fuel Diesel |

The Premium 3 photos are Johnny's, from his station (sent Oct 9, 2026): the real shift dispensed 416.07 L and ₱35,757,
implied ₱85.94/L against the posted ₱85.90 (whole-peso counter rounding). In the demo they are compared with a seeded
Premium logbook batch of **sample sales**, so the gap shown is a demo, not a real station shortage. They are not thumbnails in the
app: upload them with the Premium 3 card's 📷 Opening photo / 📷 Closing photo buttons on the Pump tab.

The Diesel 2 photo is a real asset, taken at Johnny's station (not generated). The screen title "2.Money All" means this is the
pump's lifetime **peso** counter, even though the line itself says "Volume". The pump's **liter** counter is on a
separate "Volume All" screen, which is not in this photo. Readings are whole numbers. In the app, open the **Pump** tab
and tap the sample thumbnail to read it into the peso field of an opening or closing reading.

## Other worker's assets

`closing_sheet_clean.png`, `closing_sheet_photo.jpg` and `closing_sheet_answer.json` are a synthetic closing-sheet form
and its expected answer, at the same real JCB prices (expected cash ₱57,004.26, counted ₱56,966.00, short ₱38.26).
They are not shown as photo samples in the app.
