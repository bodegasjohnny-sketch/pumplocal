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
8. **Report guard: `meterparse.report_reason`.** It actually runs right after OCR, before `meterparse.parse`. If the OCR text looks like a whole report or sheet (words like REPORT, PETTY CASH, SALES SUMMARY or RECONCILIATION, more than 30 numbers, or more than 40 lines), `ai.read_photo` stops. It returns empty fields and "This looks like a full report or sheet, not a pump screen or receipt. Snap one pump display or one receipt." in English and Tagalog. It does **not** fall back to Gemma. This was added after a photo of the full closing sheet produced a fake "sale" (1.000 L, ₱80.35, ₱79.85, numbers taken from two different pump prices).
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

**Ask answers:** the prompt asks for 1–2 sentences in the user's language, using only the DATA numbers, no disclaimers, and "can't answer" only when the number is truly missing. **`ai.strip_cant_answer`** removes a trailing "Hindi masasagot…"/"can't answer" sentence when the answer already uses a DATA number (seen in my test, after a correct answer).

**Demo Day:** `python3 seed.py --demo-empty`, then follow [DEMO_SCRIPT.md](DEMO_SCRIPT.md). `tests/test_demo_script.py` replays that click order and checks every number in the script.

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
- Example from the demo data: the pump shows 181 L / ₱10,390, recorded sales are 174.216 L / ₱10,000, so **6.784 L / ₱390 (3.75 %) unaccounted**, and the implied price is ₱57.40/L, which passes.

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
  - If there's no `SYNC_URL`, or the server can't be reached, rows just wait, and the header shows **"Offline, N records queued"**.
- **If Ollama is off:** OCR still reads photos on a Mac, cash and pump notes use templates, and Ask answers from the computed totals.

---

## 6. Ten likely judge questions (short, honest answers)

1. **"Is the AI doing the math?"**
   No. All the math is in `core.py` using Python's `Decimal`. AI only reads photos and words short explanations, and Ask answers are checked for numbers that aren't in the computed data.

2. **"Why not just use Gemma for the photos?"**
   We did at first. On my 8 GB MacBook Air the first photo took over 4 minutes and timed out. Apple Vision OCR is built into macOS, runs on-device, and read a photo in **1.8 s** when I timed it on my MacBook Air (one measurement, by hand). Code then checks the numbers. Gemma is kept as the fallback.

3. **"How accurate is the photo reading?"**
   Honest answer: **not benchmarked.** It's tested on three synthetic images, the text of one real totalizer photo, and a full closing sheet, which must be *refused* rather than read. Every value is shown in an editable form, and staff must confirm before saving. The 1 % liters × price check catches many misreads.

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

9. **"Who wrote the code?"**
   Grok Bot, an AI coding assistant, wrote essentially all of it from my direction. I supplied the station's real problems and photos, tested on my Mac, and made the decisions. Devin was tried but had no credits left, so it wrote nothing. Claude only helped clean up my Mac. All of this is in `BUILD_LOG.md`.

10. **"How do you know it works?"**
    There are 131 automated tests (`MOCK_AI=1 python3 -m unittest discover -s tests`). They start the real server, call every endpoint, and test the parsers, the math, sync (with a fake server), a fake Ollama, and Ollama being down. **Caveat:** the tests run on Linux, so Apple Vision itself is replaced by a stand-in that returns recorded OCR text. The real Vision path has only been tried by hand on my Mac.

### If they push further

- **Sync de-duplication:** each row is sent with its table name and local `id`. Editing a row (for example re-saving a totalizer reading) updates the same row and queues it again, so a receiver should **upsert by (table, id)**. The app itself doesn't import data from the server.
  - Known gap: if a row is edited *while* a sync is in flight, it can be marked synced without its latest edit being sent.
- **Why stdlib Python?** Nothing to `pip install`; `python3 app.py` runs on a stock Mac. Fewer things break at a remote station.
- **Why not OCR the handwritten closing sheet?** Handwriting is unreliable, and the goal is to replace the sheet with the pump's own numbers, not digitize it.
