# PumpLocal · Demo Day script

Every number below is checked by `tests/test_demo_script.py` (same click order, OCR replaced by the recorded text of
each sample image). If the app ever disagrees with this page, the test fails.

**What is real and what is a sample.**
- **REAL:** the four Premium 3 totalizer photos from our station (`samples/real_premium3/`): opening 2559778 /
  32333.73 (the previous day's closing, used as the shift start), closing 2595535 / 32749.80. The real shift dispensed 416.07 L and ₱35,757 (₱85.94/L against the posted
  ₱85.90/L). Prices are the real JCB pump prices as of Oct 9, 2026.
- **SAMPLE (demo data):** the Premium logbook batch seeded into the shift (10 sales, 413.5 L, ₱35,519.65), the three
  sale photos (synthetic images), the closing slip and the names on it ("Mang Ben"). On every sample, liters × price =
  amount (rounded half-up).
- **Line to say on stage:** "Pump readings: real photos. Sales: sample data. Gap is a demo, not a real station shortage."

## Before you start (at the table, laptop)

```bash
ollama serve &                 # Gemma only needed for Ask answers in your own words and the fallback reader
python3 seed.py --demo-empty   # fresh shift: float, prices, pump Premium 3, Premium logbook batch (sample sales)
python3 app.py                 # HOST=0.0.0.0 python3 app.py to open it from a phone
```

`--demo-empty` presets: opening float **₱1,000.00**; posted prices (real JCB prices as of Oct 9, 2026: Premium ₱85.90,
Unleaded ₱85.40, Diesel ₱94.50); pump **Premium 3** (Premium) with **no readings**; and the shift's earlier Premium
sales entered from the paper logbook by liters at ₱85.90: **10 sales, 413.500 L, ₱35,519.65**, each marked
**Sample sales (demo)** in the Sales list. No expenses, credit or cash checks.

**What a judge sees cold (UI polish, Oct 9 night; no button was renamed):**
- A **Start here · Simulan dito** strip at the top of every tab: **1. Snap or upload a photo** (→ Photo tab) →
  **2. Check the numbers** (→ Cash tab) → **3. Ask a question** (→ Ask tab). The current step is outlined in red.
- Under it, one plain hint per tab in English with the Tagalog beneath (e.g. Pump: "Upload the opening and closing
  meter photos on the pump card below…").
- A badge pinned above the bottom tabs on every screen: **💻 Running on this Mac · no internet needed**. The header's
  sync pill now reads **Cloud sync: Offline, N records queued** (the only part that needs internet).
- One big primary button per screen: 📷 Take / Upload Photo (Photo), 📷 Opening / Closing photo on the pump card
  (Pump), 🧮 Check Cash · Suriin (Cash), Ask (Ask). On the Pump tab the **Pumps** card (with the Premium 3 card) now
  sits above the "Totalizer photo" card, and the check-and-save form scrolls into view after each photo is read.
- **Start new shift** is a small grey button and now asks "Start a new shift? …" (OK/Cancel) before the attendant-name
  prompt. **void** asks first, as before.
- The cash result adds a plain line under the big ❌ SHORT: "Cash drawer is short by ₱50.00. · Kulang ang pera sa kaha
  ng ₱50.00." Empty areas (Photo read, Cash result, Ask chat, Pump verdict) say what to tap instead of staying blank.

---

## A. Full version (judges at the table, ~6 min)

### Photo tab · three sales (tap the sample thumbnail, check, Save)

| # | Tap sample | Expected after the read | Do |
|---|---|---|---|
| 1 | `meter_diesel.png` | Diesel · 15.000 L × ₱94.50 = ₱1,417.50 | Save (no need to type the ₱20 senior discount: it is on the closing slip, step 8) |
| 2 | `meter_unleaded.png` | Unleaded · 6.000 L × ₱85.40 = ₱512.40 | Save |
| 3 | `receipt_diesel.png` | Diesel · 21.164 L × ₱94.50 = ₱2,000.00 | Save |

### Pump tab · Premium 3 (REAL photos)

There are **no sample thumbnails on the Pump tab**: you upload the four real photos yourself, so the judges see a real
upload. On the **Premium 3** pump card (Pump tab, "Pumps" card, right under the Premium 3 REAL PUMP card) tap **📷 Opening photo · Litrato ng simula** or
**📷 Closing photo · Litrato ng pagsara**. On the Mac the file picker opens: go to the `pumplocal/samples/real_premium3/`
folder (tip: drag that folder into the Finder sidebar beforehand) and pick the file below. On a phone the camera opens
instead (you can photograph the pump itself). The opening photos are the previous day's closing readings, used as this
shift's start. Each photo fills only that side; the screen title decides pesos or liters ("2.Money All" = pesos,
"1.Report Oil" = liters, decimals kept). Check the number in the form, then **Save**.

| # | Button → file in `samples/real_premium3/` | Expected after the read | Do |
|---|---|---|---|
| 4 | Opening photo · `premium3_opening_shift_pesos.png` | Premium 3 · **Opening** · peso **2559778** | Save |
| 5 | Opening photo · `premium3_opening_shift_liters.png` | Premium 3 · **Opening** · liter **32333.73** | Save |
| 6 | Closing photo · `premium3_closing_shift_pesos.png` | Premium 3 · **Closing** · peso **2595535** | Save |
| 7 | Closing photo · `premium3_closing_shift_liters.png` | Premium 3 · **Closing** · liter **32749.80** | Save |

After step 7 the Premium row is **red, UNACCOUNTED** (tolerance 0.5%):

- Pump says 416.07 L dispensed; recorded sales 413.5 L; 2.57 L (0.62%) unaccounted.
- Pump says ₱35,757.00 dispensed; recorded sales ₱35,519.65; ₱237.35 (0.66%) unaccounted.
- Price check OK: implied ₱85.94/L (pesos ÷ liters) vs posted ₱85.90/L (whole-peso counter rounding), so the price is
  right and the missing part is volume.
- The card says: **Pump readings: real photos. Sales: sample data. Gap is a demo, not a real station shortage.**

If a closing is read into the wrong box (lower than the opening), the pump shows
**"Opening is higher than closing. Swap them? (Could also mean the counter rolled over or was reset.)"** with a
**⇄ Swap** button. Nothing negative is ever counted.

### Cash tab · closing slip

| # | Do | Expected |
|---|---|---|
| 8 | **Scan closing slip** → sample `closing_slip_photo.jpg` | Float ₱1,000.00 · Expenses: Ice and water ₱60.00, Nozzle o-ring ₱350.00 (both **＋ new**) · Discounts: slip ₱20.00 · saved on sales ₱0.00 → **＋ ₱20.00 from the slip will be counted** · Credit: Mang Ben, Diesel ₱945.00 (**＋ new**) · GCash ₱500.00 + Card ₱300.00 = non-cash ₱800.00 · Cash counted ₱39,169.55. Nothing highlighted as missing. |
| 9 | **Confirm** | Saves 2 expenses + 1 credit sale (Diesel at ₱94.50 → 10.000 L) and the slip's ₱20.00 discount, then runs the cash check |

**Cash result:** expected ₱39,219.55 = 1,000 float + 40,394.55 sales − 20 discount − 945 credit − 410 expenses − 800
non-cash; declared ₱39,169.55 → **SHORT -₱50.00 (-0.13%)**.

**Shift totals:** 14 sales · ₱40,394.55 · 465.664 L (Premium sample batch 413.500 L / ₱35,519.65; Diesel 46.164 L /
₱4,362.50 incl. Mang Ben's credit; Unleaded 6.000 L / ₱512.40).

**Pump result after the slip:** unchanged (the credit sale is diesel; Premium 3 still 2.57 L / ₱237.35 unaccounted).

**Discount, either way:** if someone types 20 / Senior on a sale in step 1, the slip shows "✓ match" instead. The
shift counts the larger of "typed on sales" and "on the slip", never both, so it's ₱20.00 and SHORT -₱50.00 in both
cases.

### Ask tab · tap the five chips, then type one question

The five chip questions (and close matches in English or Tagalog) are answered **by code**, labeled
"🧮 Computed by PumpLocal": exact numbers, instant, word for word as below. Gemma answers only free-form questions.

| # | Tap chip | Answer (exact) |
|---|---|---|
| 10 | Magkano ang benta ng diesel ngayon? | Ang benta ng Diesel ngayong shift ay ₱4,362.50 (46.164 L, 3 benta). |
| 11 | What are total sales this shift? | Total sales this shift: ₱40,394.55 (465.664 L, 14 sales). |
| 12 | Ilang litro ng Premium ang nabenta? | 413.500 L ang nabentang Premium ngayong shift (₱35,519.65, 10 benta). |
| 13 | May kulang ba sa cash? | May kulang na ₱50.00 (-0.13%) ang cash. Bilangin ulit ang pera at tingnan ang GCash/card slips at mga benta na hindi pa naitala. (Inaasahan: ₱39,219.55, nabilang: ₱39,169.55.) |
| 14 | May kulang ba sa premium? | Oo. Ayon sa metro ng pump, 416.07 L ang nailabas; 413.5 L ang naitalang benta; 2.57 L (0.62%) ang hindi naitala. Ayon sa metro ng pump, ₱35,757.00 ang nailabas; ₱35,519.65 ang naitalang benta; ₱237.35 (0.66%) ang hindi naitala. Lampas sa 0.5% na palugit. |

Typed English versions give the same numbers, e.g. "What were total sales today?" → Total sales this shift: ₱40,394.55 (465.664 L, 14 sales).
and "Is any premium missing?" → Yes. Pump says 416.07 L dispensed; recorded sales 413.5 L; 2.57 L (0.62%) unaccounted. Pump says ₱35,757.00 dispensed; recorded sales ₱35,519.65; ₱237.35 (0.66%) unaccounted. Over the 0.5% tolerance.

| # | Type (free-form, answered live by Gemma) | What a good answer says |
|---|---|---|
| 15 | Bakit hindi tugma ang premium? (or: Why does the premium not match?) | Labeled "🤖 Local AI (gemma3:4b) · numbers from PumpLocal". It should explain that the pump gave out more premium than was recorded: 2.57 L / ₱237.35 (about 0.6%), price check OK, so look for an unrecorded sale. Wording varies; any number it uses must be in the shift data, otherwise a ⚠ Check note appears. |

Gemma sees only the computed totals and the fuel dispensed this shift (e.g. "dispensed this shift 416.07 L and
₱35,757.00"), never the raw counter readings. Give it 5–20 s on the 8 GB Mac; if it is slow, the chips answer instantly.

---

## B. Stage version (3 minutes)

**Backstage, before going on:** run `python3 seed.py --demo-empty`, then do steps 1–3 (the three sale photos). Leave
the Pump tab open. On stage you show the real Premium 3 photos, the slip and one question.

| Time | Do | Say / expected on screen |
|---|---|---|
| 0:00–0:30 | Problem (one sentence) | "In the provinces, brownouts are frequent and long, and the internet signal is weak. When the power or the signal goes, cloud apps stop, and fuel and cash gaps go unchecked." |
| 0:30–1:30 | Pump tab → Premium 3 card: **📷 Opening photo** → pick `premium3_opening_shift_pesos.png` → Save; again with `premium3_opening_shift_liters.png`; then **📷 Closing photo** with `premium3_closing_shift_pesos.png` and `premium3_closing_shift_liters.png`, Save each (steps 4–7) | Each REAL photo reads in about 2 seconds, offline: 2559778 / 32333.73 → 2595535 / 32749.80. Red: **2.57 L (0.62%) / ₱237.35 (0.66%) unaccounted**; price check OK ₱85.94 vs ₱85.90. Say: "Pump readings: real photos. Sales: sample data. Gap is a demo, not a real station shortage." |
| 1:30–2:15 | Cash tab → **Scan closing slip** → `closing_slip_photo.jpg` → **Confirm** | Every line read; credit, expenses and the ₱20 discount marked new. Result: expected ₱39,219.55, counted ₱39,169.55 → **SHORT -₱50.00 (-0.13%)**. |
| 2:15–2:45 | Ask → tap **"May kulang ba sa premium?"** | Instant, "🧮 Computed by PumpLocal": "Oo. Ayon sa metro ng pump, 416.07 L ang nailabas; 413.5 L ang naitalang benta; 2.57 L (0.62%) ang hindi naitala. …" |
| 2:45–3:00 | Close | "All math is code, the AI only reads and explains. ₱0 monthly cloud." |

**If something goes wrong on stage:** a read that looks off → type the number (the form is always editable); a reading
landed in the wrong box → tap ⇄ Swap (opening/closing) or ⇄ Move (pesos/liters); the app asks "Is this pesos or
liters?" → tap ₱ Pesos or L Liters; to see what the camera read → open "OCR text" under the result; picked the wrong file → upload the right one with the same button (it
replaces that side); the Ask answer is slow → the Pump tab already shows the gap; no Ollama → photos
still read with Apple Vision.

---

## Demo video script (final)

Final narration for the demo video, 18 beats, read in order. Numbers are spelled out for the voiceover; on screen
they match the steps above (2.57 L unaccounted on Premium 3, cash SHORT ₱50.00).

| Beat | Narration |
|---|---|
| 01 | Johnny joined the App Builders PH Hackathon twenty twenty-six. This is his demo. |
| 02 | Before we begin, thank you to our sponsors: PC Express, AMD, ASUS, Cognition, Tutorials Dojo, PocketDevs, whitecloak, SM Supermalls, and PDAX. Thank you for making this event possible. |
| 03 | In the provinces, brownouts are frequent and long, and internet signal is weak. When the power or the signal goes, cloud apps stop, and fuel and cash gaps go unchecked. |
| 04 | Johnny runs a gas station in South Cotabato, so he built the fix. Let's see it. |
| 05 | This is PumpLocal. An offline AI assistant for gas stations. |
| 06 | First, Johnny switches off the Wi-Fi. The app runs on his MacBook Air with no internet connection. |
| 07 | He opens a sample pump-meter photo. Apple Vision reads the numbers on the Mac itself, in about two seconds. He saves the reading. |
| 08 | Next, he loads the opening and closing totalizer photos from his actual Premium Three pump. |
| 09 | PumpLocal compares the fuel dispensed against the recorded sales. The result? |
| 10 | Two point five seven liters unaccounted for. |
| 11 | These are real pump photos. The sales figures are sample data for this demo. |
| 12 | Now Johnny scans the closing cash slip and confirms it. PumpLocal flags another gap. |
| 13 | Fifty pesos short. |
| 14 | Finally, he asks in Tagalog why the Premium fuel is short. |
| 15 | Gemma, running locally on the Mac, answers in Tagalog using this shift's numbers. The Wi-Fi is still off. |
| 16 | At a station, a Mac mini and the Wi-Fi router run on one small UPS, so staff phones keep working through a brownout, with no internet needed. A laptop on battery plus a power bank for the router works too. |
| 17 | From meter photos to fuel checks, cash checks, and answers. |
| 18 | PumpLocal. When the power or the internet stops, the work doesn't. |
