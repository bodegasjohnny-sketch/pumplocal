# Sample images

## Synthetic (computer-generated)

These images are **synthetic**, generated with Pillow by `make_samples.py`. They are not photos of a real pump or receipt.

| File | Shows | Expected reading |
|---|---|---|
| `meter_premium.png` | Pump meter | Premium, 15.387 L, ₱64.99/L, ₱1,000.00 |
| `meter_unleaded.png` | Pump meter | Unleaded, 8.163 L, ₱61.25/L, ₱500.00 |
| `receipt_diesel.png` | Thermal receipt (slightly rotated) | Diesel, 34.843 L, ₱57.40/L, ₱2,000.00 |

In the app, tap a thumbnail under "Try a sample" on the Photo tab.

## REAL photo

| File | Shows | Expected reading |
|---|---|---|
| `real_totalizer_diesel2.png` | **REAL photo** from the team's own station: a pump's LCD totalizer menu ("2.Money All", "Volume 775397", Cancel / Ok) with the sticker label "DIESEL 2" | Totalizer 775397, pump Diesel 2, fuel Diesel |

This is a real asset, taken at Johnny's station (not generated). The display shows the raw counter only, so it is not
clear whether 775397 is liters with hidden decimals (7753.97 / 775.397) or pesos. In the app the unit (Liters or
Pesos) and the number of hidden decimal places are set per pump. In the app, open the **Pump** tab and tap the
sample thumbnail to read it as an opening or closing reading.
