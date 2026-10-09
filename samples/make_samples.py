"""Generate SYNTHETIC sample pump-meter / receipt images for the demo (requires Pillow).

These are computer-generated test images, not photos of a real pump or receipt.
The committed PNGs already exist; you only need this script to regenerate them.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def meter(name, fuel, color, amount, liters, price):
    W, H = 900, 1000
    im = Image.new("RGB", (W, H), (40, 44, 52))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([40, 30, W - 40, H - 30], 30, fill=(225, 228, 232), outline=(20, 20, 20), width=6)
    d.rounded_rectangle([40, 30, W - 40, 170], 30, fill=color)
    d.text((W // 2, 100), fuel.upper(), font=font(SANS_B, 78), fill="white", anchor="mm")
    rows = [("AMOUNT  (PESOS)", amount), ("LITERS", liters), ("PRICE PER LITER", price)]
    y = 210
    for label, value in rows:
        d.text((90, y), label, font=font(SANS_B, 34), fill=(30, 30, 30))
        d.rounded_rectangle([80, y + 48, W - 80, y + 218], 14, fill=(12, 20, 12), outline=(0, 0, 0), width=4)
        d.text((W - 110, y + 133), value, font=font(MONO_B, 120), fill=(120, 255, 120), anchor="rm")
        y += 250
    d.text((W // 2, H - 55), "SYNTHETIC SAMPLE - PumpLocal demo", font=font(SANS_B, 22), fill=(110, 110, 110), anchor="mm")
    im.save(os.path.join(OUT, name))


def receipt(name):
    W, H = 640, 1000
    im = Image.new("RGB", (W, H), (250, 250, 246))
    d = ImageDraw.Draw(im)
    f, fb, big = font(MONO, 28), font(MONO_B, 30), font(MONO_B, 44)
    lines = [
        ("c", "DEMO FUEL STATION", big), ("c", "Brgy. Sample, Philippines", f), ("c", "SALES INVOICE", fb), ("", "", f),
        ("l", "Date: 2026-10-09  10:42", f), ("l", "Pump: 2   Nozzle: 1", f), ("l", "-" * 34, f),
        ("l", "Product:        DIESEL", fb), ("l", "Volume:      34.843 L", fb), ("l", "Price/L:    P 57.40", fb),
        ("l", "-" * 34, f), ("l", "TOTAL:     P 2,000.00", big), ("l", "-" * 34, f), ("l", "Cash:      P 2,000.00", f),
        ("l", "Change:    P     0.00", f), ("", "", f), ("c", "Thank you! Salamat po!", fb), ("", "", f),
        ("c", "SYNTHETIC SAMPLE", f), ("c", "not a real receipt", f),
    ]
    y = 50
    for align, text, fnt in lines:
        if align == "c":
            d.text((W // 2, y), text, font=fnt, fill=(25, 25, 25), anchor="ma")
        elif text:
            d.text((40, y), text, font=fnt, fill=(25, 25, 25))
        y += 46
    im = im.rotate(2.5, expand=True, fillcolor=(90, 70, 50))
    im.save(os.path.join(OUT, name))


if __name__ == "__main__":
    meter("meter_premium.png", "Premium", (230, 57, 70), "1000.00", "15.387", "64.99")
    meter("meter_unleaded.png", "Unleaded", (42, 157, 143), "500.00", "8.163", "61.25")
    receipt("receipt_diesel.png")
    print("Wrote synthetic samples to", OUT)
