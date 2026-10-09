"""Generate SYNTHETIC closing totalizer screens for Diesel 2 (requires Pillow).

They imitate the layout of the REAL photo (samples/real_totalizer_diesel2.png: menu title, "Volume <number>",
Cancel / Ok, a "DIESEL 2" sticker) but are computer-generated, with made-up closing numbers consistent with the
real opening peso reading 775397 (see DEMO_SCRIPT.md):

    synthetic_totalizer_diesel2_close_money.png   "2.Money All"   778611  (peso counter: +P3,214 this shift)
    synthetic_totalizer_diesel2_close_volume.png  "1.Volume All"  13564   (liter counter: +56 L from 13508)
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def screen(name, title, value):
    W, H = 900, 700
    im = Image.new("RGB", (W, H), (58, 62, 70))  # pump housing
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([90, 60, W - 90, 470], 18, fill=(20, 22, 26))  # bezel
    d.rectangle([120, 90, W - 120, 440], fill=(186, 205, 170))  # LCD
    ink = (25, 32, 25)
    d.text((150, 115), title, font=ImageFont.truetype(SANS_B, 46), fill=ink)
    d.text((150, 215), "Volume", font=ImageFont.truetype(SANS_B, 46), fill=ink)
    d.text((W - 150, 300), value, font=ImageFont.truetype(MONO_B, 84), fill=ink, anchor="ra")
    d.text((150, 380), "Cancel", font=ImageFont.truetype(SANS_B, 36), fill=ink)
    d.text((W - 150, 380), "Ok", font=ImageFont.truetype(SANS_B, 36), fill=ink, anchor="ra")
    d.rectangle([260, 500, 640, 600], fill=(250, 250, 248))  # sticker
    d.text((450, 550), "DIESEL 2", font=ImageFont.truetype(SANS_B, 56), fill=(120, 0, 0), anchor="mm")
    d.text((W // 2, 650), "SYNTHETIC SAMPLE - not a real photo", font=ImageFont.truetype(SANS, 22),
           fill=(200, 200, 200), anchor="mm")
    im = im.rotate(-1.5, resample=Image.BICUBIC, fillcolor=(58, 62, 70)).filter(ImageFilter.GaussianBlur(0.6))
    im.save(os.path.join(HERE, name), optimize=True)


if __name__ == "__main__":
    screen("synthetic_totalizer_diesel2_close_money.png", "2.Money All", "778611")
    screen("synthetic_totalizer_diesel2_close_volume.png", "1.Volume All", "13564")
    print("ok")
