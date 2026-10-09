# PumpLocal · Demo Day script

Every number below is checked by `tests/test_demo_script.py` (same click order, OCR replaced by the recorded text of
each sample image). If the app ever disagrees with this page, the test fails.

**Samples used.** `real_totalizer_diesel2.png` is the **real** photo from our station (Diesel 2 peso counter). Everything
else is **synthetic** (computer-made): the three sale photos, the two closing totalizer screens
(`synthetic_totalizer_diesel2_close_*.png`, made-up closings consistent with the real opening) and the closing slip.
Names on the slip ("Mang Ben") are demo names. Prices are the real JCB pump prices as of Oct 9, 2026; on every
sample, liters × price = amount (rounded half-up).

## Before you start (at the table, laptop)

```bash
ollama serve &                 # Gemma only needed for Ask answers in your own words and the fallback reader
python3 seed.py --demo-empty   # fresh shift, nothing sold yet
python3 app.py                 # HOST=0.0.0.0 python3 app.py to open it from a phone
```

`--demo-empty` presets only what is known before the shift starts: opening float **₱1,000.00**, posted prices
(real JCB prices as of Oct 9, 2026: Premium ₱85.90, Unleaded ₱85.40, Diesel ₱94.50) and the Diesel 2 OPENING counters (peso **775397**, which is the real
photo's value; liter 13508). No sales, expenses, credit or cash checks.

---

## A. Full version (judges at the table, ~6 min)

### Photo tab · three sales (tap the sample thumbnail, check, Save)

| # | Tap sample | Expected after the read | Do |
|---|---|---|---|
| 1 | `meter_premium.png` | Premium · 12.000 L × ₱85.90 = ₱1,030.80 | Save (no need to type the ₱20 senior discount: it is on the closing slip, step 7) |
| 2 | `meter_unleaded.png` | Unleaded · 6.000 L × ₱85.40 = ₱512.40 | Save |
| 3 | `receipt_diesel.png` | Diesel · 21.164 L × ₱94.50 = ₱2,000.00 | Save |

### Pump tab · Diesel 2 totalizers (tap the sample; Opening/Closing is preselected)

| # | Tap sample | Expected after the read | Do |
|---|---|---|---|
| 4 | `real_totalizer_diesel2.png` (REAL) | Diesel 2 · **Opening** · peso counter **775397** (same as preset) | Save |
| 5 | `synthetic_totalizer_diesel2_close_money.png` | Diesel 2 · **Closing** · peso counter **778421** | Save |
| 6 | `synthetic_totalizer_diesel2_close_volume.png` | Diesel 2 · **Closing** · liter counter **13540** | Save |

After step 6 the Diesel row is red, UNACCOUNTED (tolerance 0.5%). It turns into the final numbers below once the
slip's credit sale is saved in step 8.

### Cash tab · closing slip

| # | Do | Expected |
|---|---|---|
| 7 | **Scan closing slip** → sample `closing_slip_photo.jpg` | Float ₱1,000.00 · Expenses: Ice and water ₱60.00, Nozzle o-ring ₱350.00 (both **＋ new**) · Discounts: slip ₱20.00 · saved on sales ₱0.00 → **＋ ₱20.00 from the slip will be counted** · Credit: Mang Ben, Diesel ₱945.00 (**＋ new**) · GCash ₱500.00 + Card ₱300.00 = non-cash ₱800.00 · Cash counted ₱3,263.20. Nothing highlighted as missing. |
| 8 | **Confirm** | Saves 2 expenses + 1 credit sale (Diesel at ₱94.50 → 10.000 L) and the slip's ₱20.00 discount, then runs the cash check |

**Cash result:** expected ₱3,313.20 = 1,000 float + 4,488.20 sales − 20 discount − 945 credit − 410 expenses − 800
non-cash; declared ₱3,263.20 → **SHORT -₱50.00 (-1.51%)**.

**Pump result (Diesel 2):** Pump says 32 L dispensed; recorded sales 31.164 L; 0.836 L (2.61%) unaccounted.
Pump says ₱3,024.00 dispensed; recorded sales ₱2,945.00; ₱79.00 (2.61%) unaccounted. Price check OK: implied
₱94.50/L vs posted ₱94.50/L (so the price is right; the missing part is volume).

**Shift totals:** 4 sales · ₱4,488.20 · 49.164 L.

**Discount, either way:** if someone does type 20 / Senior on the Premium sale in step 1, the slip shows "✓ match"
instead. The shift counts the larger of "typed on sales" and "on the slip", never both, so it's ₱20.00 and
SHORT -₱50.00 in both cases. (Johnny's Oct 9 Mac run skipped typing it and got -₱70.00 before this fix.)

### Ask tab · tap the five chips, then type one question

The five chip questions (and close matches in English or Tagalog) are answered **by code**, labeled
"🧮 Computed by PumpLocal": exact numbers, instant, word for word as below. Gemma answers only free-form questions.

| # | Tap chip | Answer (exact) |
|---|---|---|
| 9 | Magkano ang benta ng diesel ngayon? | Ang benta ng Diesel ngayong shift ay ₱2,945.00 (31.164 L, 2 benta). |
| 10 | What are total sales this shift? | Total sales this shift: ₱4,488.20 (49.164 L, 4 sales). |
| 11 | Ilang litro ng Premium ang nabenta? | 12.000 L ang nabentang Premium ngayong shift (₱1,030.80, 1 benta). |
| 12 | May kulang ba sa cash? | May kulang na ₱50.00 (-1.51%) ang cash. Bilangin ulit ang pera at tingnan ang GCash/card slips at mga benta na hindi pa naitala. (Inaasahan: ₱3,313.20, nabilang: ₱3,263.20.) |
| 13 | May kulang ba sa diesel? | Oo. Ayon sa metro ng pump, 32 L ang nailabas; 31.164 L ang naitalang benta; 0.836 L (2.61%) ang hindi naitala. Ayon sa metro ng pump, ₱3,024.00 ang nailabas; ₱2,945.00 ang naitalang benta; ₱79.00 (2.61%) ang hindi naitala. Lampas sa 0.5% na palugit. |

Typed English versions give the same numbers, e.g. "What were total sales today?" → Total sales this shift: ₱4,488.20 (49.164 L, 4 sales).
and "Is any diesel missing?" → Yes. Pump says 32 L dispensed; recorded sales 31.164 L; 0.836 L (2.61%) unaccounted. Pump says ₱3,024.00 dispensed; recorded sales ₱2,945.00; ₱79.00 (2.61%) unaccounted. Over the 0.5% tolerance.

| # | Type (free-form, answered live by Gemma) | What a good answer says |
|---|---|---|
| 14 | Bakit hindi tugma ang diesel? (or: Why does the diesel not match?) | Labeled "🤖 Local AI (gemma3:4b) · numbers from PumpLocal". It should explain that the pump gave out more diesel than was recorded: 0.836 L / ₱79.00 (2.61%), price check OK, so look for an unrecorded sale or credit fill-up. Wording varies; any number it uses must be in the shift data, otherwise a ⚠ Check note appears. |

Gemma sees only the computed totals and the fuel dispensed this shift (e.g. "dispensed this shift 32 L and ₱3,024.00"),
never the raw counter readings. Give it 5–20 s on the 8 GB Mac; if it is slow, the chips above still answer instantly.

---

## B. Stage version (3 minutes)

**Backstage, before going on:** run `python3 seed.py --demo-empty`, then do steps 1–3 (sales) and 5–6 (synthetic
closings). Leave the Pump tab open. On stage you only show real-photo reading, the slip and one question.

| Time | Do | Say / expected on screen |
|---|---|---|
| 0:00–0:30 | Problem (one sentence) | "At the end of every shift, the owner can't tell if fuel or cash is missing, and the station has weak internet." |
| 0:30–1:15 | Pump tab → tap the **REAL** photo `real_totalizer_diesel2.png` → Save | Reads **775397**, Diesel 2, Opening, in about 2 seconds, offline. Diesel row: **32 L / ₱3,024.00** dispensed vs 21.164 L / ₱2,000.00 recorded so far (the credit sale comes from the slip). |
| 1:15–2:15 | Cash tab → **Scan closing slip** → `closing_slip_photo.jpg` → **Confirm** | Every line read; credit, expenses and the ₱20 discount marked new. Result: expected ₱3,313.20, counted ₱3,263.20 → **SHORT -₱50.00 (-1.51%)**. Pump tab now: 0.836 L (2.61%) / ₱79.00 unaccounted. |
| 2:15–2:45 | Ask → tap **"May kulang ba sa cash?"** | Instant, "🧮 Computed by PumpLocal": "May kulang na ₱50.00 (-1.51%) ang cash … (Inaasahan: ₱3,313.20, nabilang: ₱3,263.20.)" |
| 2:45–3:00 | Close | "All math is code, the AI only reads and explains. ₱0 monthly cloud." |

**If something goes wrong on stage:** a read that looks off → type the number (the form is always editable); the Ask
answer is slow → the Cash tab already shows -₱50.00; no Ollama → photos still read with Apple Vision.
