"""Generate the SYNTHETIC Shift Closing Slip images (requires Pillow).

- docs/closing_slip_template.png   blank slip (same layout as static/closing_slip_template.html)
- samples/closing_slip_clean.png   filled, flat scan
- samples/closing_slip_photo.jpg   filled, phone-photo style (tilt, shadow, slight blur)
- samples/closing_slip_answer.json what a correct read must return

The filled numbers are made up and fit the Demo Day flow (DEMO_SCRIPT.md): python3 seed.py --demo-empty, then
the three sale photos. With the slip the Cash Check expects P3,313.20 and P3,263.20 was counted: SHORT P50.00.
"""
import json
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
INK = (15, 35, 120)  # dark blue pen

FILLED = {
    "date": "09 OCT 2026",
    "shift": "6AM - 2PM",
    "opening_float": "1,000.00",
    "expenses": [("ICE AND WATER", "60.00"), ("NOZZLE O-RING", "350.00")],
    "discounts": "20.00",
    "credits": [("MANG BEN - DIESEL", "945.00")],
    "gcash": "500.00",
    "card": "300.00",
    "cash_counted": "3,263.20",
}
ANSWER = {
    "_note": "Synthetic sample (made-up numbers) for the Demo Day flow in DEMO_SCRIPT.md (python3 seed.py --demo-empty, "
             "then the 3 sale photos with a P20 senior discount on the Premium sale). Prices: real JCB pump prices "
             "as of Oct 9, 2026. Expected cash: 1,000 float + 4,488.20 gross (1,030.80 + 512.40 + 2,000.00 + Mang Ben's "
             "945.00 credit = 10 L diesel at 94.50) - 20 discount - 945 credit - 410 expenses - 800 GCash/card = "
             "3,313.20; counted 3,263.20 -> SHORT 50.00 (-1.51%).",
    "date": "09 OCT 2026", "shift": "6AM - 2PM", "opening_float": "1000.00",
    "expenses": [{"description": "Ice and water", "amount_pesos": "60.00"},
                 {"description": "Nozzle o-ring", "amount_pesos": "350.00"}],
    "expenses_total": "410.00", "discounts": "20.00",
    "credits": [{"customer": "Mang Ben", "fuel_type": "Diesel", "amount_pesos": "945.00"}],
    "credit_total": "945.00", "gcash": "500.00", "card": "300.00", "noncash": "800.00", "cash_counted": "3263.20",
}

W, ROW = 1000, 84
X0, XV0, XV1 = 60, 600, 940  # label x, value line from..to


def f(path, size):
    return ImageFont.truetype(path, size)


def draw_slip(values=None):
    blank = values is None
    v = values or {}
    rows = [("title",), ("sub",), ("gap",),
            ("field", "DATE", "date"), ("field", "SHIFT", "shift"), ("gap",),
            ("field", "OPENING FLOAT", "opening_float"), ("gap",),
            ("head", "EXPENSES", "item + amount"), ("items", "expenses", 3), ("gap",),
            ("field", "DISCOUNTS", "discounts"), ("gap",),
            ("head", "CREDIT / UTANG", "name - fuel + amount"), ("items", "credits", 2), ("gap",),
            ("field", "GCASH", "gcash"), ("field", "CARD", "card"), ("gap",),
            ("field", "CASH COUNTED", "cash_counted")]
    H = 60 + sum({"title": 80, "sub": 50, "gap": 26, "note": 44}.get(r[0], ROW) if r[0] != "items" else ROW * r[2]
                 for r in rows) + 90
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, W - 20, H - 20], outline=(0, 0, 0), width=4)
    y = 50
    line = (190, 190, 190)  # light guide lines: easy to write on, easy for OCR to ignore
    for r in rows:
        kind = r[0]
        if kind == "title":
            d.text((W // 2, y + 36), "JCB SHIFT CLOSING SLIP", font=f(SANS_B, 52), fill=(0, 0, 0), anchor="mm")
            y += 80
        elif kind == "sub":
            d.text((W // 2, y + 20), "One amount per line  ·  Discounts = senior / PWD / suki total", font=f(SANS, 24),
                   fill=(90, 90, 90), anchor="mm")
            y += 50
        elif kind == "gap":
            y += 26
        elif kind == "note":
            d.text((X0 + 20, y + 10), r[1], font=f(SANS, 24), fill=(110, 110, 110))
            y += 44
        elif kind == "field":
            d.text((X0, y + ROW - 22), r[1], font=f(SANS_B, 40), fill=(0, 0, 0), anchor="ls")
            d.line([XV0, y + ROW - 8, XV1, y + ROW - 8], fill=line, width=2)
            if not blank and v.get(r[2]):
                d.text((XV1 - 8, y + ROW - 24), v[r[2]], font=f(MONO_B, 42), fill=INK, anchor="rs")
            y += ROW
        elif kind == "head":
            d.text((X0, y + ROW - 22), r[1], font=f(SANS_B, 40), fill=(0, 0, 0), anchor="ls")
            d.text((XV1, y + ROW - 24), r[2], font=f(SANS, 24), fill=(110, 110, 110), anchor="rs")
            y += ROW
        elif kind == "items":
            items = [] if blank else v.get(r[1], [])
            for i in range(r[2]):
                d.line([X0 + 30, y + ROW - 8, XV0 - 30, y + ROW - 8], fill=line, width=2)
                d.line([XV0, y + ROW - 8, XV1, y + ROW - 8], fill=line, width=2)
                if i < len(items):
                    desc, amt = items[i]
                    d.text((X0 + 34, y + ROW - 24), desc, font=f(MONO_B, 36), fill=INK, anchor="ls")
                    d.text((XV1 - 8, y + ROW - 24), amt, font=f(MONO_B, 42), fill=INK, anchor="rs")
                y += ROW
    d.text((W // 2, H - 45), "PumpLocal  ·  Scan in the Cash tab", font=f(SANS, 22), fill=(130, 130, 130),
           anchor="mm")
    return im


def phone_photo(im):
    random.seed(7)
    pad = 120
    bg = Image.new("RGB", (im.width + 2 * pad, im.height + 2 * pad), (92, 70, 52))  # wooden counter
    noise = Image.effect_noise(bg.size, 18).convert("RGB")
    bg = Image.blend(bg, noise, 0.12)
    paper = im.convert("RGBA")
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([pad + 18, pad + 24, pad + im.width + 18, pad + im.height + 24],
                                     fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow)
    bg.alpha_composite(paper, (pad, pad))
    # soft shadow of the phone/hand across the lower-left part of the slip
    shade = Image.new("L", bg.size, 0)
    ImageDraw.Draw(shade).ellipse([-bg.width // 2, bg.height // 2, bg.width // 2, bg.height * 3 // 2], fill=55)
    shade = shade.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGBA", bg.size, (0, 0, 0, 255))
    dark.putalpha(shade)
    bg = Image.alpha_composite(bg, dark).convert("RGB")
    bg = bg.rotate(-2.2, resample=Image.BICUBIC, expand=False, fillcolor=(92, 70, 52))
    bg = bg.filter(ImageFilter.GaussianBlur(0.9))
    k = 1200 / bg.width
    return bg.resize((1200, int(bg.height * k)), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    draw_slip().save(os.path.join(DOCS, "closing_slip_template.png"), optimize=True)
    clean = draw_slip(FILLED)
    clean.save(os.path.join(HERE, "closing_slip_clean.png"), optimize=True)
    phone_photo(clean).save(os.path.join(HERE, "closing_slip_photo.jpg"), quality=82)
    with open(os.path.join(HERE, "closing_slip_answer.json"), "w") as fh:
        json.dump(ANSWER, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("ok")
