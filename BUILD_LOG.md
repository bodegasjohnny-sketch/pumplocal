# PumpLocal build log

This is a plain account of how I built PumpLocal for the hackathon: what I used, what went wrong, and what I changed. It's a summary, not a chat transcript.

— Johnny Bodegas

## Origin

- At the 1:00 PM PHT kickoff I screenshotted the challenge reveal and matched it to a real problem at my JCB gas station: when the Wi-Fi drops or a brownout hits, my existing station app (FuelTrack) can't record or check sales, a gap I hadn't accounted for.
- PumpLocal is the offline piece FuelTrack was missing. It was built fresh during the hackathon, uses no FuelTrack code, and could plug into FuelTrack later. I can show the FuelTrack repo to judges on request to confirm none of its code is reused.
- The first commits share the same minute (2:10 PM PHT) because the code written in the AI-assisted session after kickoff was pushed in one batch; see the timeline below.

## At a glance

| | |
|---|---|
| **Event** | AppBuildersPH Devin Hackathon 2026. Theme: local AI that works offline |
| **Kickoff** | Oct 9, 2026, 1:00 PM PHT |
| **Deadline** | Oct 10, 2026, 10:00 AM PHT |
| **Team** | Johnny Bodegas, solo. I live in Tagaytay; my JCB gas station is in Sto. Niño, South Cotabato |
| **Machine** | MacBook Air, Apple Silicon, 8 GB RAM, running Ollama with `gemma3:4b` |
| **Code written by** | Grok Bot (an AI assistant), from my direction. See [Who did what](#who-did-what) |

## Who did what

- **Me (Johnny):** the idea and the real-world requirements from running the station; the real pump photo; testing every build on my Mac; reporting what broke and how fast it ran; every product decision.
- **Grok Bot (AI coding assistant):** wrote essentially all of the code, tests and docs, following my direction.
- **Claude:** used only for Mac cleanup during the hackathon, for two things: stopping the background processes of another local app of mine (Creative Video Studio), and clearing the ~1.6 GB Playwright cache. It wrote **no** PumpLocal code.
- **Devin:** I tried it. I installed the Devin GitHub app for the pumplocal repo only and prepared a task to add pump readings to the sync queue. The free credits had already run out, so Devin wrote **no** code. Grok Bot built the pump sync instead.

## Problems I hit, in order

These happened on my 8 GB MacBook Air after the first version of the app ran (between the 2:14 PM and 3:03 PM commits below).

1. **The first photo read took over 4 minutes and timed out.** `gemma3:4b` through Ollama was reading the pump photo, and it was far too slow on this machine.
2. **The Mac was low on disk,** with about 971 MB free. I cleaned up: I removed old installer (`.dmg`) files from Downloads, and Claude cleared the ~1.6 GB Playwright cache. Free space went from about 8.4 GB to about 10 GB. It stayed around 8–10 GB after that.
3. **Port 8080 was already taken.** Another local app of mine (Creative Video Studio) was using `localhost:8080`. Fix: PumpLocal now defaults to `127.0.0.1:8080`.
4. **Background workers were eating RAM.** That same studio app had background workers running. Claude stopped them.
5. **Even a text-only question took about 220 seconds.** This showed the real problem was memory pressure on 8 GB, not the photo itself.

### Fixes, in order

1. Raised the AI timeout to 600 s.
2. Downscaled images to 768 px before sending them to the model.
3. Warmed up the model when the app starts.
4. Added a status badge that says **"Local AI busy"** instead of "not running" while the model is working.
5. **The key pivot:** read photos with **Apple Vision OCR** (built into macOS) and turn the text into numbers with plain code. Gemma is now only the fallback for photos and does the chat and the short explanations.

After the pivot, photo reads took a few seconds on my Mac. Later I timed one Apple Vision read at **1.8 s** on the MacBook Air (a single measurement, by hand).

### Later: the Mac got laggy (about 3:53 PM PHT)

- **Problem:** during development, the MacBook Air got laggy, and even typing lagged. The `gemma3:4b` model was still loaded in memory by Ollama while other apps were open.
- **Fix:** stop the app in its terminal (Ctrl+C), then run `ollama stop gemma3:4b` to unload the model when I'm not demoing. It loads again automatically on the next AI request, or at the warm-up when the app starts.
- **Note for 8 GB machines:** unload the model when you're not using it.

## Timeline

Times are git commit times converted to PHT (UTC+8), from `git log`. Several commits landed within minutes of each other, so the times show when work was committed, not how long each piece took. Test counts come from running `MOCK_AI=1 python3 -m unittest discover -s tests` at that commit; all passed.

| Time (PHT, Oct 9) | Milestone | Commits | Tests |
|---|---|---|---|
| 1:00 PM | Kickoff | — | — |
| 2:10–2:14 PM | **Core app:** SQLite storage, Decimal money math, photo → sale form, shift totals, cash check, Ask (Tagalog/English), offline sync queue, demo seed, synthetic samples, README | `491ae6f` → `edc8475` | 27 |
| 2:43 PM | **Slow-model fixes:** model warm-up, 600 s timeout, 768 px images, `127.0.0.1` default | `07b1c0d` | — |
| 2:59–3:03 PM | **Pivot to Apple Vision OCR:** Swift OCR helper, parsing in code with a liters × price check, Vision first with Gemma fallback, "Local AI busy" badge, UI shows which model read the photo and how long it took | `5cb5a80` → `2af2efb` | 53 |
| 3:18–3:24 PM | **Pump totalizer v1:** real Diesel 2 photo added, totalizer parser, opening/closing readings, closing − opening, pump-vs-sales gap with tolerance, Pump tab, gap in Cash Check and Ask | `920f89a` → `b3efabf` | 76 |
| 3:26–3:38 PM | **Expenses and discounts, credit sales and bill count:** receipt TOTAL read in code, discounts (suki/senior/PWD/other), credit (utang) sales, bill count, new expected-cash formula | `0c358c4`, `5b05ce3` | 89 |
| 3:38 PM | Synthetic closing-sheet samples added | `d1e8a6c` | 89 |
| 3:40–3:42 PM | **Pump totalizer v2:** both lifetime counters (pesos and liters), closing − opening for each, price-per-liter check with an optional mid-shift price change, README | `be4b879` → `58686ea` | 94 |
| 3:44 PM | **Offline pitch deck** at `/slides` (3 slides + "Go to live demo"), header link | `ed3ccad` | 95 |
| 4:02 PM | **Pump readings sync:** pumps and totalizer readings join the offline sync queue the same way expenses do (built by Grok Bot after Devin had no credits) | `b46cef4` | 102 |
| 4:30 PM | **Slides:** added "How it runs at the station" (staff phones, Wi-Fi router, used Mac mini, UPS) | `17cfb66` | 102 |
| 4:41 PM | **Bug fix from my test:** a photo of the full closing sheet (synthetic sample) produced a fake sale (1.000 L, ₱80.35, ₱79.85). The Photo tab now recognizes a whole report or sheet and asks for one pump display or one receipt, without guessing and without falling back to Gemma | `c9ca7ca` | 110 |
| 4:49 PM | **Shift Closing Slip:** printable one-page slip (`/closing-slip`), synthetic filled samples, `slipparse.py` (code reads every line), Cash tab "Scan closing slip" → review (new vs already saved, discount check, missing values highlighted) → confirm saves only new items and runs the cash check. Gemma only if OCR fails, and only to transcribe | `f5e5fa7` | 124 |
| 4:49 PM | **Ask fix from my test:** a correct answer was followed by "Hindi masasagot ng datos ang tanong nang eksakto." The prompt now asks for 1–2 sentences, no disclaimers, and "can't answer" only when the number is truly missing; code drops a trailing can't-answer sentence after a real answer | `1cc152e` | 129 |
| 5:00 PM | **Demo Day flow:** `seed.py --demo-empty` (empty shift, float, prices and the real 775397 opening preset), two synthetic Diesel 2 closing totalizer screens, slip numbers aligned to the demo, [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (full + 3-minute stage version) and an end-to-end test of that exact click order | see git log | 131 |
| 5:15 PM | **Switched demo to real JCB pump prices as of Oct 9, 2026** (Premium ₱85.90, Unleaded ₱85.40, Diesel ₱94.50): seed (default and `--demo-empty`), every synthetic sample image (meters, receipt, closing totalizers, closing slip, closing sheet) regenerated with liters × price = amount (rounded half-up), OCR fixtures, tests, DEMO_SCRIPT and README screenshots. Backup tag `demo-v1-before-real-prices` | see git log | 131 |
| 5:31 PM | **Oct 9 live Mac test found wrong AI wording, so the chip questions now use code templates.** gemma3:4b read the raw counter 13540 as liters, wrote "49,164 liters" and answered "Isa. 1 sales" for Premium liters; fixed questions are now answered by code ("Computed by PumpLocal"), free-form ones still by Gemma, which no longer sees raw counter readings. Same test: Cash showed −₱70.00 because the ₱20 discount was only on the slip; the slip's discount now counts on Confirm (larger of slip vs sales, never both) | see git log | 135 |
| 6:10 PM | **Oct 9: real Premium 3 totalizer photos added, real shift 416.07 L / ₱35,757 reconciles at ₱85.94/L** (posted ₱85.90, whole-peso counter rounding). Parser reads this pump's screens ("2.Money All" = pesos, "1.Report Oil … lite" = liters with decimals, "3.Type Money / Bank" = pesos); fixtures hand-made from the photos (Tesseract can't read the blue LCD). Demo Day now uses Premium 3, uploaded by hand (no Pump tab thumbnails): each pump card has 📷 Opening photo / 📷 Closing photo buttons; a closing lower than the opening shows "Opening is higher than closing. Swap them?" with a ⇄ Swap button, never a negative sale. Photo samples are now Diesel meter (15 L, ₱1,417.50), Unleaded meter and Diesel receipt; Diesel 2 screens moved to `samples/archive/`. `--demo-empty` seeds a Premium logbook batch of **sample sales** (413.5 L, ₱35,519.65) so the pump check shows a demo gap of 2.57 L / ₱237.35; slip cash counted ₱39,169.55 → SHORT ₱50.00. "Pump readings: real photos. Sales: sample data. Gap is a demo, not a real station shortage." | `950ae65` | 152 |
| 6:28 PM | **Oct 9 6:28 PM Mac test: closing pesos routed to liters; fixed with plausibility routing.** Real Vision text (`2.Money All / Volume / 2595535 / Cancel / 8k / PREMIUM 3 / Amoun / Quantity`) now a fixture. Fixes: the menu title outranks 'Volume'; panel labels 'Amoun'/'Quantity' and the '8k' sticker are ignored; every reading is checked against the pump's other side (dispensed must be ≥ 0, < 5,000 L, < ₱500,000): if only one box fits it goes there, if unclear the app asks "Is this pesos or liters?"; an implausible saved reading shows ERROR "looks wrong for liters, did you mean pesos?" with a one-tap ⇄ Move. Stale 775397 placeholder removed. | `HASH` | TESTS |

## Key design decisions

- **Math is done by code, never by AI.** Liters, totals, dispensed fuel, gaps, percentages and expected cash are all computed in Python with `Decimal`.
- **AI only reads photos and writes explanations.** The model's wording is labeled as AI, and there's a template fallback. Ask answers are checked against the computed numbers.
- **I deliberately did not OCR the handwritten closing sheet.** Handwriting is unreliable to read, and the point of the app is to replace that sheet with readings from the pump itself. The Shift Closing Slip is a different, printed form with one amount per line; its samples use typed digits, and real handwriting hasn't been tested.
- **A price mismatch is a warning, not a theft flag.** If pesos ÷ liters doesn't match the posted price, it usually means a misread or a mid-shift price change. Only a fuel gap over the tolerance turns the card red.

## Assets

- **Made before kickoff:** the real photo of a pump totalizer screen at my station (now `samples/archive/real_totalizer_diesel2.png`, no longer in the demo). Only its peso reading (775397) is real; the other pump readings in the demo are made up. If I show a photo of a handwritten closing sheet on a slide, it was also taken before kickoff and is blurred. It is **not** in the repo; the slide falls back to a drawn placeholder.
- **Generated during the hackathon:** all other sample images, which are synthetic: the meter and receipt samples, `samples/closing_sheet_*`, the closing slip (`samples/closing_slip_*`) and the two Diesel 2 closing totalizer screens (`samples/archive/synthetic_totalizer_diesel2_close_*`, made-up closings that fit the real opening).
- **Why real photos:** to make PumpLocal usable in real practice and not just a lab demo, I built it from my own station's real material. The pump totalizer reader was built and tested against the real station photo (peso counter 775397). The Shift Closing Slip and the Cash tab fields were designed from the layout of my real handwritten closing sheet, but that sheet itself was not OCR-tested: the closing-sheet upload in my test was the synthetic typed sample. (The totalizer photo was taken before kickoff; the handwritten sheet photo is not in the repo.)
- **Real Premium 3 photos (Oct 9, 5:53 PM PHT):** four totalizer screens from my station's Premium 3 pump (`samples/real_premium3/`); the opening readings are the previous day's closing, used as the shift start. All four readings are real; the Premium sales they are compared with in the demo are sample data.
- **Code:** none existed before kickoff.

## Privacy

The repo contains no staff names, signatures, ticket QR codes, phone numbers or personal financial information. Demo sales, prices, customers and expenses are made up.
