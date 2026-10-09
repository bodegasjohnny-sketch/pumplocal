# PumpLocal build log

This is a plain account of how I built PumpLocal for the hackathon: what I used, what went wrong, and what I changed. It's a summary, not a chat transcript.

— Johnny Bodegas

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

After the pivot, photo reads took a few seconds on my Mac.

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

## Key design decisions

- **Math is done by code, never by AI.** Liters, totals, dispensed fuel, gaps, percentages and expected cash are all computed in Python with `Decimal`.
- **AI only reads photos and writes explanations.** The model's wording is labeled as AI, and there's a template fallback. Ask answers are checked against the computed numbers.
- **I deliberately did not OCR the handwritten closing sheet.** Handwriting is unreliable to read, and the point of the app is to replace that sheet with readings from the pump itself.
- **A price mismatch is a warning, not a theft flag.** If pesos ÷ liters doesn't match the posted price, it usually means a misread or a mid-shift price change. Only a fuel gap over the tolerance turns the card red.

## Assets

- **Made before kickoff:** the real photo of a pump totalizer screen at my station (`samples/real_totalizer_diesel2.png`). Only its peso reading (775397) is real; the other pump readings in the demo are made up. If I show a photo of a handwritten closing sheet on a slide, it was also taken before kickoff and is blurred. It is **not** in the repo; the slide falls back to a drawn placeholder.
- **Generated during the hackathon:** all other sample images, which are synthetic: the meter and receipt samples and `samples/closing_sheet_*`.
- **Code:** none existed before kickoff.

## Privacy

The repo contains no staff names, signatures, ticket QR codes, phone numbers or personal financial information. Demo sales, prices, customers and expenses are made up.
