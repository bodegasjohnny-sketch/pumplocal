# Sample images

## Synthetic (computer-generated)

These images are **synthetic**, generated with Pillow by `make_samples.py`. They are not photos of a real pump or receipt.

| File | Shows | Expected reading |
|---|---|---|
| `meter_premium.png` | Pump meter | Premium, 15.387 L, ₱64.99/L, ₱1,000.00 |
| `meter_unleaded.png` | Pump meter | Unleaded, 8.163 L, ₱61.25/L, ₱500.00 |
| `receipt_diesel.png` | Thermal receipt (slightly rotated) | Diesel, 34.843 L, ₱57.40/L, ₱2,000.00 |

In the app, tap a thumbnail under "Try a sample" on the Photo tab.

| File | Shows | Expected reading |
|---|---|---|
| `closing_slip_photo.jpg` / `closing_slip_clean.png` | Filled Shift Closing Slip (phone photo with tilt and shadow / flat scan), made by `make_slip.py` | See `closing_slip_answer.json`: float ₱1,000; expenses ₱60 + ₱350; discounts ₱20; credit Mang Ben Diesel ₱1,000; GCash ₱500; card ₱300; cash counted ₱3,220 |
| `synthetic_totalizer_diesel2_close_money.png` | **SYNTHETIC** Diesel 2 "2.Money All" closing screen, made by `make_totalizer_close.py` | Peso totalizer 778611 (closing) |
| `synthetic_totalizer_diesel2_close_volume.png` | **SYNTHETIC** Diesel 2 "1.Volume All" closing screen | Liter totalizer 13564 (closing) |

The closing totalizer screens imitate the layout of the real photo but are generated, with made-up closings chosen to
fit the real opening 775397 for the Demo Day script (56 L / ₱3,214 dispensed). Each image says "SYNTHETIC SAMPLE".

## REAL photo

| File | Shows | Expected reading |
|---|---|---|
| `real_totalizer_diesel2.png` | **REAL photo** from the team's own station: a pump's LCD totalizer menu ("2.Money All", "Volume 775397", Cancel / Ok) with the sticker label "DIESEL 2" | **Peso** (money) totalizer 775397, whole number; pump Diesel 2, fuel Diesel |

This is a real asset, taken at Johnny's station (not generated). The screen title "2.Money All" means this is the
pump's lifetime **peso** counter, even though the line itself says "Volume". The pump's **liter** counter is on a
separate "Volume All" screen, which is not in this photo. Readings are whole numbers. In the app, open the **Pump** tab
and tap the sample thumbnail to read it into the peso field of an opening or closing reading.

## Other worker's assets

`closing_sheet_clean.png`, `closing_sheet_photo.jpg` and `closing_sheet_answer.json` are a synthetic closing-sheet form
and its expected answer. They are not shown as photo samples in the app.
