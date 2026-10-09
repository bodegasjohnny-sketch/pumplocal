# PumpLocal · Demo Day script

Every number below is checked by `tests/test_demo_script.py` (same click order, OCR replaced by the recorded text of
each sample image). If the app ever disagrees with this page, the test fails.

**Samples used.** `real_totalizer_diesel2.png` is the **real** photo from our station (Diesel 2 peso counter). Everything
else is **synthetic** (computer-made): the three sale photos, the two closing totalizer screens
(`synthetic_totalizer_diesel2_close_*.png`, made-up closings consistent with the real opening) and the closing slip.
Names on the slip ("Mang Ben") are demo names.

## Before you start (at the table, laptop)

```bash
ollama serve &                 # Gemma only needed for Ask answers in your own words and the fallback reader
python3 seed.py --demo-empty   # fresh shift, nothing sold yet
python3 app.py                 # HOST=0.0.0.0 python3 app.py to open it from a phone
```

`--demo-empty` presets only what is known before the shift starts: opening float **₱1,000.00**, posted prices
(Premium ₱64.99, Unleaded ₱61.25, Diesel ₱57.40) and the Diesel 2 OPENING counters (peso **775397**, which is the real
photo's value; liter 13508). No sales, expenses, credit or cash checks.

---

## A. Full version (judges at the table, ~6 min)

### Photo tab · three sales (tap the sample thumbnail, check, Save)

| # | Tap sample | Expected after the read | Do |
|---|---|---|---|
| 1 | `meter_premium.png` | Premium · 15.387 L × ₱64.99 = ₱1,000.00 | Type **20** in Discount, Reason **Senior**, Save |
| 2 | `meter_unleaded.png` | Unleaded · 8.163 L × ₱61.25 = ₱500.00 | Save |
| 3 | `receipt_diesel.png` | Diesel · 34.843 L × ₱57.40 = ₱2,000.00 | Save |

### Pump tab · Diesel 2 totalizers (tap the sample; Opening/Closing is preselected)

| # | Tap sample | Expected after the read | Do |
|---|---|---|---|
| 4 | `real_totalizer_diesel2.png` (REAL) | Diesel 2 · **Opening** · peso counter **775397** (same as preset) | Save |
| 5 | `synthetic_totalizer_diesel2_close_money.png` | Diesel 2 · **Closing** · peso counter **778611** | Save |
| 6 | `synthetic_totalizer_diesel2_close_volume.png` | Diesel 2 · **Closing** · liter counter **13564** | Save |

After step 6 the Diesel row is red, UNACCOUNTED (tolerance 0.5%). It turns into the final numbers below once the
slip's credit sale is saved in step 8.

### Cash tab · closing slip

| # | Do | Expected |
|---|---|---|
| 7 | **Scan closing slip** → sample `closing_slip_photo.jpg` | Float ₱1,000.00 · Expenses: Ice and water ₱60.00, Nozzle o-ring ₱350.00 (both **＋ new**) · Discounts ₱20.00 (**matches** the 20 typed in step 1) · Credit: Mang Ben, Diesel ₱1,000.00 (**＋ new**) · GCash ₱500.00 + Card ₱300.00 = non-cash ₱800.00 · Cash counted ₱3,220.00. Nothing highlighted as missing. |
| 8 | **Confirm** | Saves 2 expenses + 1 credit sale (Diesel at ₱57.40 → 17.422 L), then runs the cash check |

**Cash result:** expected ₱3,270.00 = 1,000 float + 4,500 sales − 20 discount − 1,000 credit − 410 expenses − 800
non-cash; declared ₱3,220.00 → **SHORT -₱50.00 (-1.53%)**.

**Pump result (Diesel 2):** Pump says 56 L dispensed; recorded sales 52.265 L; 3.735 L (6.67%) unaccounted.
Pump says ₱3,214.00 dispensed; recorded sales ₱3,000.00; ₱214.00 (6.66%) unaccounted. Price check OK: implied
₱57.39/L vs posted ₱57.40/L (so the price is right; the missing part is volume).

**Shift totals:** 4 sales · ₱4,500.00 · 75.815 L.

### Ask tab · four questions

Gemma words the answer; the numbers must be these (with MOCK_AI=1 the built-in templates answer word for word):

| # | Question | Correct numbers |
|---|---|---|
| 9 | Magkano ang benta ng diesel ngayon? | ₱3,000.00 · 52.265 litro (2 benta: receipt + Mang Ben credit) |
| 10 | What were total sales today? | ₱4,500.00 · 75.815 liters · 4 sales |
| 11 | Is any diesel missing? | Yes: 3.735 L (6.67%) and ₱214.00 (6.66%) unaccounted on Diesel 2 |
| 12 | May kulang ba sa cash? | Oo, kulang ₱50.00 (-1.53%); inaasahan ₱3,270.00, nabilang ₱3,220.00 |

If an answer shows "⚠ numbers not in the data", say so: that is the guard working, and the numbers on the Cash and
Pump tabs are the source of truth.

---

## B. Stage version (3 minutes)

**Backstage, before going on:** run `python3 seed.py --demo-empty`, then do steps 1–3 (sales) and 5–6 (synthetic
closings). Leave the Pump tab open. On stage you only show real-photo reading, the slip and one question.

| Time | Do | Say / expected on screen |
|---|---|---|
| 0:00–0:30 | Problem (one sentence) | "At the end of every shift, the owner can't tell if fuel or cash is missing, and the station has weak internet." |
| 0:30–1:15 | Pump tab → tap the **REAL** photo `real_totalizer_diesel2.png` → Save | Reads **775397**, Diesel 2, Opening, in about 2 seconds, offline. Diesel row: **56 L / ₱3,214.00** dispensed vs ₱2,000.00 recorded so far (the credit sale comes from the slip). |
| 1:15–2:15 | Cash tab → **Scan closing slip** → `closing_slip_photo.jpg` → **Confirm** | Every line read, credit + expenses marked new, discount matches. Result: expected ₱3,270.00, counted ₱3,220.00 → **SHORT -₱50.00 (-1.53%)**. Pump tab now: 3.735 L (6.67%) / ₱214.00 unaccounted. |
| 2:15–2:45 | Ask → **"May kulang ba sa cash?"** | "May kulang na ₱50.00 (-1.53%) ang cash … (Inaasahan: ₱3,270.00, nabilang: ₱3,220.00)" |
| 2:45–3:00 | Close | "All math is code, the AI only reads and explains. ₱0 monthly cloud." |

**If something goes wrong on stage:** a read that looks off → type the number (the form is always editable); the Ask
answer is slow → the Cash tab already shows -₱50.00; no Ollama → photos still read with Apple Vision.
