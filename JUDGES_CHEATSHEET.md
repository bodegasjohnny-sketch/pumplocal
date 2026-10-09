# Judges' Q&A cheat sheet (for Johnny)

Plain-English notes on how PumpLocal really works, taken from the code in this repo. File and function names are given so you can point to them if a judge asks "where is that in the code?"

---

## 1. How a photo becomes a number (sale meter or receipt)

1. **Phone or laptop browser** (`static/index.html`). Staff take or pick a photo. The page shrinks it in the browser:
   - a **768 px** JPEG copy for Gemma (`image`);
   - a sharper **1,600 px** copy for Apple Vision (`image_full`).

   Both are sent as base64 in a JSON POST to **`/api/extract`**.
2. **`app.py` → `Handler.do_POST`**, branch `/api/extract`. It checks that an image arrived, then calls **`ai.read_photo(image, image_full)`**.
3. **`ai.read_photo`** (`ai.py`) decides who reads the photo, based on the `READER` setting (`auto` by default):
   - `auto`: Apple Vision OCR first, with Gemma only as fallback.
   - `vision`: OCR only.
   - `gemma`: Gemma only.
4. **`ai._decode_image`** turns the base64 back into bytes.
5. **`vision.run_image_bytes`** (`vision.py`) writes the bytes to a temporary file and runs the compiled Swift helper on it, with a 30 s limit (`OCR_TIMEOUT`). It deletes the temp file afterwards, so **photos are not stored**.
   - The helper is compiled once by **`vision.ensure` → `_setup`**, which runs `swiftc -O ocr/ocr.swift -o ocr/pumplocal-ocr`. It's rebuilt only if the `.swift` file changes. If this isn't a Mac, or the Xcode tools are missing, OCR is marked unavailable and the app quietly uses Gemma.
6. **`ocr/ocr.swift`** uses Apple's Vision framework (`VNRecognizeTextRequest`, `.accurate`, **language correction off** so it doesn't "fix" digits into words). It prints JSON: each text line with its confidence and a box (`x, y, w, h`, flipped so the top-left is 0,0). Everything runs on-device.
7. **`meterparse.parse(lines)`** (`meterparse.py`) is plain code, no AI:
   - **`fuel_type`** finds PREMIUM / UNLEADED / DIESEL.
   - **`numbers_in`** pulls the numbers out of each line. It first fixes common OCR mix-ups with **`_fix_digits`** (O→0, I→1, …), and ignores dates and times.
   - **`label_of`** spots labels (AMOUNT/TOTAL, LITERS/VOLUME, PRICE, and words like CASH/CHANGE that must be *ignored*). **`unit_hint`** reads clues such as "/L" or a leading "₱".
   - **`_link_cost`** links each number to the nearest label: same row to the right, or directly below. The best-linked number wins for each field.
   - **Cross-check:** liters × price must equal the amount within **1 %** (**`_consistent`**), with sane ranges (price ₱30–150, liters 0.1–500). If the labelled numbers don't agree, **`_search`** tries every combination of the numbers on the page for one that adds up.
   - If only two of the three values are found, **`core.reconcile`** computes the third (for example liters = amount ÷ price, in `Decimal`).
8. **Report guard: `meterparse.report_reason`.** It actually runs right after OCR, before `meterparse.parse`. If the OCR text looks like a whole report or sheet (words like REPORT, PETTY CASH, SALES SUMMARY or RECONCILIATION, more than 30 numbers, or more than 40 lines), `ai.read_photo` stops. It returns empty fields and "This looks like a full report or sheet, not a pump screen or receipt. Snap one pump display or one receipt." in English and Tagalog. It does **not** fall back to Gemma. This was added after a photo of the full closing sheet (synthetic sample) produced a fake "sale" (1.000 L, ₱80.35, ₱79.85, numbers taken from two different pump prices).
9. **`ai._vision_result`** builds the reply: the fields, notes, warnings, which reader was used, and how many seconds it took.
   - If the numbers are **consistent**, that's the answer.
   - If not, and `READER=auto`, it goes to the Gemma fallback (section 2).
10. **Back in the browser**, the values fill an **editable form**. Staff check them and press save. `/api/sales` then runs **`core.save_sale`**, which validates everything again and stores the sale.

**Pump totalizer photos** use the same path: `/api/pump/extract` → **`ai.read_totalizer`** → `vision.run_image_bytes` → **`totalizer.parse`** (`totalizer.py`).
- The screen title decides which counter it is: "2.Money All" means the peso counter, "Volume All" means liters. Menu numbers, dates and the "2" in "DIESEL 2" are ignored.
- **`totalizer.pump_label`** finds the pump name so the right pump is preselected.

**Expense receipts:** `/api/expense/extract` → **`ai.read_expense`** → **`meterparse.receipt_total`**. It finds the TOTAL line and ignores SUBTOTAL, CASH, CHANGE and VAT lines.

**Shift Closing Slip:** Cash tab → "Scan closing slip" → `/api/slip/extract` → **`ai.read_slip`** → `vision.run_image_bytes` → **`slipparse.parse`**.
- `rows_from_lines` joins the pieces of each row (OCR splits labels and amounts, and phone photos are tilted). Each row is matched to a printed label (OPENING FLOAT, EXPENSES, DISCOUNTS, CREDIT / UTANG, GCASH, CARD, CASH COUNTED); amounts are parsed in code. Anything missing or unreadable is listed and highlighted, never guessed. The slip is far below the report guard's limits, so the guard doesn't block it.
- **`core.slip_compare`** marks each expense/credit "✓ saved" (same amount already recorded; credit also needs a shared name word) or "＋ new", checks the discount total against the discounts typed on sales, and previews the expected cash. **`core.slip_apply`** (Confirm) saves only the new items, so pressing it twice saves nothing more; then the normal cash check runs.
- Gemma is used only if OCR fails, and only to **transcribe** the lines as JSON; the same code parses them.

**Ask: fixed questions in code, free-form to Gemma.** **`ai.code_answer`** answers the five chip questions and close matches in English or Tagalog (sales of a fuel, total sales, liters of a fuel, cash short/over, missing fuel at the pump, plus credit, expenses and discounts) from code templates with the exact computed numbers, in the question's language, labeled "🧮 Computed by PumpLocal". It's instant and never calls the model. This came from my Oct 9 live Mac test: gemma3:4b read the raw counter 13540 as liters, wrote "49,164 liters" for 49.164 L, and answered "Isa. 1 sales" for Premium liters. Only free-form questions (e.g. "Bakit hindi tugma ang diesel?") go to Gemma, and its data now has only the fuel dispensed this shift (e.g. "32 L and ₱3,024.00"), never the raw totalizer readings; liters are written as "49.164 L".

**Discounts from the closing slip:** on Confirm, the slip's discount total is saved for the shift, and the shift counts the **larger** of "typed on sales" and "on the slip", never the sum (`core.shift_summary`). So the ₱20 is counted once whether it was typed on the Premium sale, written on the slip, or both. My Oct 9 Mac run (not typed on the sale) showed −₱70.00 before this fix; now −₱50.00.

**Free-form Ask answers (Gemma):** the prompt asks for 1–2 sentences in the user's language, using only the DATA numbers, no disclaimers, and "can't answer" only when the number is truly missing. **`ai.strip_cant_answer`** removes a trailing "Hindi masasagot…"/"can't answer" sentence when the answer already uses a DATA number (seen in my test, after a correct answer).

**Demo Day:** `python3 seed.py --demo-empty`, then follow [DEMO_SCRIPT.md](DEMO_SCRIPT.md). `tests/test_demo_script.py` replays that click order and checks every number in the script.

**Real vs sample on Demo Day:** the pump is **Premium 3**, read from four REAL photos uploaded live with the card's 📷 Opening / Closing photo buttons (opening 2559778 / 32333.73 = the previous day's closing; closing 2595535 / 32749.80 → 416.07 L, ₱35,757, ₱85.94/L vs posted ₱85.90). The Premium sales it is compared with are a seeded logbook batch of **sample sales** (413.5 L, ₱35,519.65, labeled "Sample sales (demo)"), so the 2.57 L / ₱237.35 gap is staged. Say it plainly: "Pump readings: real photos. Sales: sample data. Gap is a demo, not a real station shortage." Parser: "2.Money All" = pesos even though the line says "Volume"; "1.Report Oil … lite" = liters, decimals kept; "3.Type Money / Bank" = pesos. Readings go to the side you pick (📷 Opening photo / 📷 Closing photo); a closing lower than the opening is never swapped silently: "Opening is higher than closing. Swap them?" with a ⇄ Swap button, and no negative sale is ever counted.

## 2. How the Gemma fallback works

- **When it's used:** only if OCR is unavailable (not a Mac, no Xcode tools), OCR fails or times out, or OCR's numbers don't add up. For totalizers, also when no labelled number was found.
- **How it's called:** `ai.extract` / `ai.extract_totalizer` / `ai.extract_expense` → **`ai.chat`**. This sends the 768 px image to **Ollama on this machine** (`http://localhost:11434/api/chat`, model `gemma3:4b`) with a short prompt asking for **JSON only** and "null if not visible, do not guess".
  - Settings: temperature 0, `num_ctx` 2048, short outputs, `keep_alive` 15 min, timeout 600 s (`AI_TIMEOUT`).
- **`ai.parse_json_text`** digs the JSON out of the reply, even if it's wrapped in chatter or ```json fences. **`fields_from_obj`** maps the keys, and **`core.reconcile`** re-checks the math. **The model's numbers are never trusted blindly.**
- **If Gemma is also down:** the reply keeps the partial OCR values with a warning, or the app returns "Local AI unavailable. You can still type the values and save." Typing by hand always works.
- **Gemma also does the wording:** cash-check notes (**`ai.cash_explanation`**), pump notes (**`ai.pump_explanation`**) and Ask answers (**`ai.ask`**). Each one has a **template fallback** (`template_cash_text`, `template_pump_text`, `template_answer`).
  - Ask sends Gemma only the numbers that code already computed (**`ai.summary_context`**) and tells it not to calculate.
  - **`ai.unverified_numbers`** then flags any number in the answer that isn't in that data, and the UI shows a warning.
- **Status badge:** **`ai.status`** checks Ollama's `/api/tags`. A slow reply while a request is running shows **"Local AI busy"**, not "not running".
- **Warm-up:** `app.main` sends a 1-token "hi" at startup so the model is loaded before the first photo.

## 3. The math (all in `core.py`, `Decimal`, never the model)

**Cash: `core.compute_cash`**

```
expected cash = opening float + gross sales − discounts − credit (utang) − expenses − GCash/card
difference    = declared − expected      → OK if within ±₱5 (CASH_TOLERANCE), else SHORT or OVER
percent       = difference ÷ expected × 100
```

- Discounts are per sale (`save_sale`): a reason (suki/senior/PWD/other) is required, and the discount can't be more than the sale.
- Credit sales need a customer name. They count as sales but not as drawer cash. Credit is counted net of its own discount, so nothing is subtracted twice.
- **`core.count_cash`** adds up the bill count (₱1000 … ₱20 plus coins). The total can fill in "declared cash".

**Pump: `core.pump_check`**

- **`core.dispensed`**: dispensed = closing − opening, for **both** the peso and liter counters. Readings are whole numbers unless a hidden-decimals setting is chosen (**`reading_value`**). A closing lower than the opening is an **error** (misread, wrong decimals or rollover), never a negative sale.
- Sales are added up **by fuel type**: liters, and **gross** pesos, because the pump doesn't know about discounts.
- **`core.gap_check`**: gap = pump − recorded. Within the tolerance (default **0.5 %** of dispensed) it's OK. Above it, it's **UNACCOUNTED** (red). Below it, **OVER_RECORDED** (a duplicate sale or misread).
- **`core.price_check`**: implied price = pesos ÷ liters. It must match the posted price within **±₱0.05/L**, or fall inside the old–new range if "price changed this shift" was set. **A miss is a yellow warning only, never a theft flag.**
- Example from the demo data (real JCB prices as of Oct 9, 2026): the pump shows 105 L / ₱9,922, recorded sales are 101 L / ₱9,544.50, so **4 L (3.81 %) / ₱377.50 (3.80 %) unaccounted**, and the implied price is ₱94.50/L, which passes. Demo Day flow (`--demo-empty`): Premium 3 (real readings vs sample sales) 2.57 L (0.62 %) / ₱237.35 (0.66 %) unaccounted, price OK ₱85.94 vs ₱85.90; cash SHORT ₱50.00 (−0.13 %); totals 14 sales, ₱40,394.55, 465.664 L.

## 4. Where data lives

- **One SQLite file: `pumplocal.db`** in the app folder (`DB_PATH`). It's created and seeded on first run (`core.init_db`, `seed.seed`).
- **Tables:**
  - `shifts`, `sales`, `cash_checks`, `expenses`;
  - `pumps`, `totalizer_readings`;
  - `settings` (pump tolerance, price changes);
  - `pump_readings` (an old table, only kept so v1 data can be migrated).
- **Schema changes:** new columns are added with `ALTER TABLE` on startup (`MIGRATIONS` in `core.py`), so old databases keep working.
- **Photos are not saved.** Only the numbers are.

## 5. What happens offline

- **Everything except cloud sync** works with no internet: OCR, Gemma, the math, the database, the UI, and the `/slides` deck (no CDN, no web fonts).
- **Sync queue (`sync.py`):**
  - Every new or changed row gets `synced=0`. That covers shifts, sales, cash checks, expenses, pumps and totalizer readings (`core.SYNC_TABLES`).
  - **`sync.sync_now`** POSTs the queued rows as JSON to `SYNC_URL`. It runs from the Sync button and every 30 s in the background.
  - After a 2xx reply, those rows are marked `synced=1`.
  - If there's no `SYNC_URL`, or the server can't be reached, rows just wait, and the header's sync pill shows **"Cloud sync: Offline, N records queued"**. The badge above the bottom tabs, **"💻 Running on this Mac · no internet needed"**, is always on: everything except sync works offline.
- **If Ollama is off:** OCR still reads photos on a Mac, cash and pump notes use templates, and Ask answers from the computed totals.
- **The pain point:** in the provinces, brownouts are frequent and long and the internet signal is weak. When the power or the signal goes, cloud apps stop, so fuel and cash gaps go unchecked. PumpLocal needs neither.
- **Station setup (proposed):** a **Mac mini** at the station runs PumpLocal and the AI, and **one small UPS powers both the Mac mini and the Wi-Fi router**, so staff phones keep working on the local Wi-Fi through a brownout, with no internet. A **laptop on battery plus a power bank for the router** also works. (The demo itself runs on a MacBook Air; the Mac mini + UPS setup is a proposal, not yet tested at the station.)

---

## 6. Ten likely judge questions (short, honest answers)

1. **"Is the AI doing the math?"**
   No. All the math is in `core.py` using Python's `Decimal`. AI only reads photos and words short explanations, and Ask answers are checked for numbers that aren't in the computed data.

2. **"Why not just use Gemma for the photos?"**
   We did at first. On my 8 GB MacBook Air the first photo took over 4 minutes and timed out. Apple Vision OCR is built into macOS, runs on-device, and read a photo in **1.8 s** when I timed it on my MacBook Air (one measurement, by hand). Code then checks the numbers. Gemma is kept as the fallback.

3. **"How accurate is the photo reading?"**
   Honest answer: **not benchmarked.** It's tested on synthetic meter/receipt images, the 4 real Premium 3 totalizer photos (including the exact Apple Vision text from my Mac), and a full closing sheet, which must be *refused* rather than read. Every value is shown in an editable form, and staff must confirm before saving. The 1 % liters × price check catches many misreads.

4. **"Is it really local / offline?"**
   Yes. Vision OCR is part of macOS, Gemma runs in Ollama on the laptop, and data is in a SQLite file. The only network call is the optional sync to `SYNC_URL`. I can demo it with Wi-Fi off.

5. **"How do you catch theft?"**
   The pump's own lifetime counters (pesos and liters): closing − opening is what left the pump. We compare that with recorded sales, and a gap over 0.5 % turns red. It doesn't prove theft. It shows a gap to check, which could be an unrecorded sale, an utang that wasn't logged, or a misread.

6. **"What if the price changed mid-shift?"**
   Staff enter the old and new price. Any implied price in that range (±₱0.05) passes. A mismatch is only a warning.

7. **"What are the weaknesses?"**
   - Sales don't record which nozzle, so pumps of the same fuel are checked as a group.
   - A counter rollover must be entered by hand.
   - OCR expects labelled meters and receipts; unusual layouts fall back to slow Gemma.
   - Vision OCR is macOS-only.
   - Tagalog detection is a simple keyword list.
   - Accuracy isn't benchmarked, and speed was timed only once (1.8 s for one Apple Vision read).

8. **"Is the data secure?"**
   It stays on the station's computer. Sync has **no authentication yet**, and there's **no receiving server** in this repo; `SYNC_URL` must point to an endpoint you control. There are no user logins, and the SQLite file isn't encrypted. All of these are on the to-do list.

9. **"Where's the AI?"**
   Two places, both local. (1) **Reading photos:** Apple Vision OCR reads meters, receipts, totalizers and the closing slip, with Gemma 3 4B as the fallback reader. (2) **Free-form chat:** typed questions like "Bakit hindi tugma ang diesel?" are answered by Gemma from the shift's computed numbers. The fixed questions (the chips) are answered by code on purpose, for accuracy: when I tested on my Mac, Gemma garbled some numbers. All the math is always code.

10. **"Who wrote the code?"**
   Grok Bot, an AI coding assistant, wrote essentially all of it from my direction. I supplied the station's real problems and photos, tested on my Mac, and made the decisions. Devin was tried but had no credits left, so it wrote nothing. Claude (Claude Code) wrote no PumpLocal code; it installed Ollama and cleaned up my Mac (disk space, background processes). All of this is in `BUILD_LOG.md`.

11. **"How do you know it works?"**
    There are 169 automated tests (`MOCK_AI=1 python3 -m unittest discover -s tests`). They start the real server, call every endpoint, and test the parsers, the math, sync (with a fake server), a fake Ollama, and Ollama being down. **Caveat:** the tests run on Linux, so Apple Vision itself is replaced by a stand-in that returns recorded OCR text. The real Vision path has only been tried by hand on my Mac.

12. **"Was Ollama or the model installed before the hackathon?"**
    No. Claude installed Ollama at 1:38 PM on Oct 9, and I pulled `gemma3:4b` at 2:14 PM, both after the 1:00 PM kickoff. The first PumpLocal commit was at 2:10 PM. The timeline is in `BUILD_LOG.md`.

13. **"What happens in a brownout?"**
    The laptop keeps running on its battery, and the Wi-Fi router needs a UPS or a power bank so staff phones stay connected to the local network (no internet needed). The proposed station setup is a Mac mini and the router on one small UPS, so staff phones keep working through a brownout. Everything is saved to SQLite as soon as it's entered, and sync just waits until the internet is back.

### If they push further

- **Sync de-duplication:** each row is sent with its table name and local `id`. Editing a row (for example re-saving a totalizer reading) updates the same row and queues it again, so a receiver should **upsert by (table, id)**. The app itself doesn't import data from the server.
  - Known gap: if a row is edited *while* a sync is in flight, it can be marked synced without its latest edit being sent.
- **Why stdlib Python?** Nothing to `pip install`; `python3 app.py` runs on a stock Mac. Fewer things break at a remote station.
- **Why not OCR the handwritten closing sheet?** Handwriting is unreliable, and the goal is to replace the sheet with the pump's own numbers, not digitize it.

## 7. Loopholes & live Q&A (from the Oct 9 stress test)

I tried to break the live app: bad numbers, double-clicks, wrong photos, a new shift mid-demo, a restart, Ollama stopped. Honest answers, weaknesses included.

1. **"What if staff just don't record the sale and also skip the photo?"**
   That's exactly what the pump counters catch: the totalizer keeps counting whether anyone records the sale or not, so closing − opening shows more fuel left the pump than was recorded (red "unaccounted"). **Weakness:** it only works if someone photographs (or types) the opening and closing totalizer each shift. Skip that, and there is nothing to compare.

2. **"Can staff edit the readings?"**
   Yes, on purpose: OCR can misread, so every field is editable. Each reading shows where it came from (photo, typed, or carried over from last shift). **Weakness:** a re-saved reading overwrites the old one; there's no edit history yet, and the photo itself isn't stored.

3. **"What stops someone from deleting data?"**
   There's no delete button: "void" only marks a sale or expense as voided; it stays in the database and is queued for sync. **Weakness:** no logins and no roles, so anyone at the laptop can void, and someone with file access could delete the SQLite file. Sync to an owner's server is the backstop, and it's not authenticated yet.

4. **"How accurate is the OCR?"**
   Not benchmarked. It reads the 4 real Premium 3 photos and the synthetic samples correctly, and every value is shown for staff to check before saving. On the Pump tab, a reading is also checked against the pump's other reading: if it can't be liters (e.g. 2,563,201 L in one shift), the app says so and offers ⇄ Move, or asks "Is this pesos or liters?".

5. **"Why not just use the cloud?"**
   The station's internet is unreliable, and the owner's sales data stays on the station's laptop. Everything (OCR, math, the AI) runs offline; sync is optional and queues until it's online.

6. **"What happens on a counter rollover?"**
   A closing lower than the opening is never counted as a negative sale. The app shows "Opening is higher than closing. Swap them? (Could also mean the counter rolled over or was reset.)" with a one-tap swap. **Weakness:** a true rollover has to be entered by hand.

7. **"Is the AI making up numbers?"**
   The quick-question chips are answered by code. For free-form questions Gemma gets only the computed numbers, and any number in its reply that isn't in that data is flagged ("⚠️ Check: … not found in this shift's data"). All math is code (`Decimal`).

8. **"Will the AI accuse my staff?"**
   No. Why-questions ask Gemma for neutral causes (an unrecorded sale, an unlogged test pour, calibration, a counting error) plus a check, and a code filter replaces any reply that mentions stealing or theft (nagnakaw, ninakaw, steal, theft…).

9. **"What if Gemma is slow or Ollama is off?"**
   Photos use Apple Vision first (no Gemma). If Ollama is off, Ask and the cash note fall back to template answers from the computed numbers, and the app says "Local AI unavailable". **Weakness:** if Ollama is running but hung, the app waits up to `AI_TIMEOUT` (600 s by default, because the first model load on an 8 GB Mac is slow) before falling back.

10. **"What if someone uploads the wrong photo?"**
    A random image or a totalizer on the Photo tab gives "Could not read values… (a pump totalizer photo goes on the Pump tab)". A sale meter or receipt on the Pump tab is flagged "This looks like a sale meter or receipt, not a pump totalizer". Anything else on the slip scan is refused ("This doesn't look like a shift closing slip").

11. **"What about typos: negative, zero, letters, 1e9?"**
    Refused with a clear message: amounts must be above zero, `1e9` isn't read as 1, and a single sale over 10,000 L or ₱1,000,000 is refused. In the cash check, negative or non-numeric cash, float or GCash is refused. A double-tap on a button is ignored.

12. **"What if they press 'Start new shift' by mistake?"**
    It's a small grey button and it asks first ("Start a new shift? This closes the current shift…", then the
    attendant-name prompt); Cancel on either leaves the shift untouched. If confirmed, the old shift is closed and kept; its closing totalizer becomes the new shift's opening. The posted fuel prices carry over (this was a bug I fixed on Oct 9). **Weakness:** there's no "reopen shift" button.

13. **"What if the app or laptop restarts mid-shift?"**
    Everything is saved to SQLite as soon as it's entered; after a restart the same shift, sales and readings are there (tested by killing and restarting the server).

14. **"What if the closing slip is scanned twice?"**
    Items already recorded are matched and skipped, so confirming twice adds nothing.

15. **"Same sale entered twice?"**
    **Weakness:** two genuinely separate saves of the same values are two sales (a station can sell ₱500 of diesel twice in a minute). They show up in the Sales list to void, and the pump-vs-sales check would show more recorded than dispensed.
