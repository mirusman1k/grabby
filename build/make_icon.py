"""Generate an original app icon (.icns) -- a download arrow over a play mark."""
from PIL import Image, ImageDraw
from pathlib import Path

S = 1024
OUT = Path(__file__).resolve().parent


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return m


def gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize((size, size))


# Background: indigo -> blue, macOS-style squircle corners (~22% radius).
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(gradient(S, (88, 118, 255), (38, 66, 200)), (0, 0), rounded_mask(S, int(S * 0.225)))

d = ImageDraw.Draw(icon)
cx = S / 2
W = "white"

# Downward arrow: shaft + head.
shaft_w, top_y, shaft_bot = S * 0.105, S * 0.215, S * 0.545
d.rounded_rectangle([cx - shaft_w / 2, top_y, cx + shaft_w / 2, shaft_bot],
                    radius=shaft_w / 2, fill=W)
head_w, head_tip = S * 0.30, S * 0.695
d.polygon([(cx - head_w / 2, S * 0.485), (cx + head_w / 2, S * 0.485), (cx, head_tip)], fill=W)

# Tray line underneath -- the universal "save to disk" cue.
tray_y, tray_w, tray_t = S * 0.775, S * 0.42, S * 0.075
d.rounded_rectangle([cx - tray_w / 2, tray_y, cx + tray_w / 2, tray_y + tray_t],
                    radius=tray_t / 2, fill=W)

iconset = OUT / "icon.iconset"
iconset.mkdir(exist_ok=True)
for base in (16, 32, 128, 256, 512):
    icon.resize((base, base), Image.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
    icon.resize((base * 2, base * 2), Image.LANCZOS).save(iconset / f"icon_{base}x{base}@2x.png")
print("iconset written to", iconset)
