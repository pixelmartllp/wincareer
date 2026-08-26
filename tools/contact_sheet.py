"""Render the staged candidates the way the creative will actually crop them.

Metadata will not tell you that a photo has a watermark, someone mid-blink,
a competitor's logo on a laptop lid, or a face right where the headline goes.
Only looking will. On the sibling pipeline this exact check caught a
watermark, a person in a red jacket and a vintage painting that every filter
had passed.

The thumbnails are cropped to the 1080x1350 canvas and overlaid with the two
zones that are spoken for - the logo plate top-left and the headline band at
the bottom - so a photo whose subject sits under the type is obvious here
rather than after it ships.

    python tools/contact_sheet.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from win_social import brand  # noqa: E402
from win_social.config import ROOT, force_utf8  # noqa: E402

STAGING = ROOT / "assets" / "_staging"
MANIFEST = STAGING / "candidates.json"

CANVAS = brand.CANVAS["portrait"]          # 1080 x 1350
THUMB = (252, 315)                          # same 4:5, small enough to tile
COLUMNS = 5
ROWS = 4
LABEL_H = 22
MARGIN = 10

# Where the creative spends its space, as fractions of the canvas.
LOGO_ZONE = (0.05, 0.04, 0.34, 0.17)        # left, top, right, bottom
BAND_ZONE = (0.0, 0.62, 1.0, 0.93)


def crop_to_canvas(image: Image.Image) -> Image.Image:
    """Centre-crop to the canvas aspect, exactly as the renderer will."""
    target = CANVAS[0] / CANVAS[1]
    width, height = image.size
    if width / height > target:
        new_width = round(height * target)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = round(width / target)
        top = (height - new_height) // 3      # bias up: faces sit high
        image = image.crop((0, top, width, top + new_height))
    return image.resize(THUMB, Image.LANCZOS)


def overlay_zones(thumb: Image.Image) -> Image.Image:
    thumb = thumb.convert("RGBA")
    layer = Image.new("RGBA", thumb.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width, height = thumb.size

    l, t, r, b = BAND_ZONE
    draw.rectangle([(l * width, t * height), (r * width - 1, b * height)],
                   fill=(*brand.NAVY, 90))

    l, t, r, b = LOGO_ZONE
    draw.rectangle([(l * width, t * height), (r * width, b * height)],
                   outline=(*brand.ORANGE, 200), width=2)

    return Image.alpha_composite(thumb, layer).convert("RGB")


def main() -> int:
    force_utf8()
    if not MANIFEST.is_file():
        raise SystemExit(f"No candidates staged. Run tools/fetch_photos.py first.")

    candidates = json.loads(MANIFEST.read_text(encoding="utf-8"))
    candidates.sort(key=lambda c: (c.get("theme") or "~", c["id"]))
    print(f"{len(candidates)} candidates")

    try:
        font = ImageFont.truetype(brand.font_path("body"), 13)
    except Exception:
        font = ImageFont.load_default()

    per_sheet = COLUMNS * ROWS
    sheets = 0

    for start in range(0, len(candidates), per_sheet):
        chunk = candidates[start:start + per_sheet]
        rows = (len(chunk) + COLUMNS - 1) // COLUMNS
        sheet = Image.new(
            "RGB",
            (COLUMNS * (THUMB[0] + MARGIN) + MARGIN,
             rows * (THUMB[1] + LABEL_H + MARGIN) + MARGIN),
            (238, 238, 238))
        draw = ImageDraw.Draw(sheet)

        for i, candidate in enumerate(chunk):
            path = STAGING / candidate["file"]
            if not path.is_file():
                continue
            thumb = overlay_zones(crop_to_canvas(Image.open(path).convert("RGB")))

            col, row = i % COLUMNS, i // COLUMNS
            x = MARGIN + col * (THUMB[0] + MARGIN)
            y = MARGIN + row * (THUMB[1] + LABEL_H + MARGIN)
            sheet.paste(thumb, (x, y))

            label = f"{candidate['id']}  {candidate.get('theme') or 'neutral'}"
            draw.text((x + 2, y + THUMB[1] + 4), label, fill=(30, 30, 30),
                      font=font)

        out = STAGING / f"contact-sheet-{start // per_sheet + 1:02d}.png"
        sheet.save(out)
        sheets += 1
        print(f"  {out.name}  {sheet.size[0]}x{sheet.size[1]}  "
              f"({len(chunk)} photos)")

    print(f"\n{sheets} sheet(s) in {STAGING}")
    print("Look at every one before installing. Reject: watermarks, other "
          "brands' logos, faces under the headline band, anything that looks "
          "like cheap stock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
