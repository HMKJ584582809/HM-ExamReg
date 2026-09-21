# -*- coding: utf-8 -*-
"""生成应用图标（蓝底「考」字），输出 dist/app.ico。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "build" / "app.ico"
OUT.parent.mkdir(parents=True, exist_ok=True)

size = 256
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角蓝底
d.rounded_rectangle([8, 8, size - 8, size - 8], radius=48, fill=(31, 111, 235, 255))
d.rounded_rectangle([8, 8, size - 8, size // 2], radius=48, fill=(59, 130, 246, 255))

font = None
for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
          r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
    try:
        font = ImageFont.truetype(p, 150)
        break
    except Exception:
        continue
if font is None:
    font = ImageFont.load_default()

text = "考"
bbox = d.textbbox((0, 0), text, font=font)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), text, font=font, fill=(255, 255, 255, 255))

img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon ->", OUT)
