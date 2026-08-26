"""Creative renderer for Win Career Academy.

One layout, done properly: a full-bleed photograph, a short headline in the
brand's condensed caps, one accent line, the logo on its plate, and a thin
footer. Deliberately sparse - the audience is a working professional
scrolling past, and the Page's dense flyers already cover the campaign end.

The type is seated, not assumed. Text is fitted first, then the strip it
actually occupies is measured, then the scrim is deepened until the ink
separates from what is behind it. Three things there are load-bearing and
each of them is a bug that shipped on the sibling pipeline:

  * Judge the band the text occupies, not the whole frame. A frame can
    average dark while the strip under the headline is a bright window.
  * Judge on a percentile, not the mean. A band of office lighting averages
    mid while every highlight in it is blown; a mean-based test passes and
    the accent line lands on something unreadable.
  * The accent fails first. It is smaller and lower contrast than the
    headline, so it gets the stricter target.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from . import assets, brand

# Layout, as fractions of the canvas.
MARGIN_X = 0.074                 # 80px on 1080
LOGO_BOX = (0.28, 0.115)         # width, height of the logo's box
LOGO_TOP = 0.045

FOOTER_HEIGHT = 0.125            # tall enough that the QR stays scannable
QR_OF_FOOTER = 0.72              # QR side as a fraction of the footer height
TEXT_BOTTOM = 0.845              # accent baseline sits above the footer

HEADLINE_MAX_LINES = 2           # the brief is less text, not more
HEADLINE_SIZES = tuple(range(96, 51, -3))
ACCENT_SIZES = tuple(range(40, 21, -2))
STRAPLINE_SIZES = tuple(range(34, 21, -2))
STRAPLINE_GAP = 0.60             # gap below the strapline, in its own heights
LINE_SPACING = 1.02              # Anton's caps collide below about 1.0
ACCENT_GAP = 1.30                # gap above the accent, in accent-line heights
RULE_HEIGHT = 6

# Premium grade. Small numbers on purpose - a heavy grade reads as a filter,
# and the point is that nobody notices it, only that the feed looks like one
# campaign instead of thirty stock photos.
GRADE_DESATURATE = 0.20          # blend towards greyscale
GRADE_CONTRAST = 1.09
GRADE_SPLIT = (                  # per channel: (shadow shift, highlight shift)
    (-4, 6),                     # R: cooler shadows, warmer highlights
    (-2, 3),                     # G
    (10, -2),                    # B: navy into the shadows
)
VIGNETTE_STRENGTH = 0.16

CHIP_TEXT = 0.0145               # chip text size as a fraction of canvas height
CHIP_TRACKING = 0.0022
CHIP_RULE = 5

STRAPLINE_TRACKING = 0.6

# Scrim targets: the brightness percentile inside a text band has to fall to
# at least this before the ink counts as seated. 0-255, lower is darker.
# These are strict on purpose - the first pass used 96/78, every creative
# "passed", and the type was still fighting the photograph.
HEADLINE_TARGET = 74
ACCENT_TARGET = 62
SCRIM_STEPS = 18
SCRIM_PERCENTILE = 0.88


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def region_levels(image: Image.Image, box: tuple[int, int, int, int],
                  percentile: float = SCRIM_PERCENTILE) -> int:
    """The brightness at a percentile inside a region, 0-255.

    A percentile, not a mean. The mean hides exactly the failure that
    matters: a band whose average is comfortable but whose bright tail - a
    window, a lamp, a white shirt - is what the small type has to survive.
    """
    left, top, right, bottom = (max(0, int(v)) for v in box)
    right = min(right, image.width)
    bottom = min(bottom, image.height)
    if right <= left or bottom <= top:
        return 0

    grey = image.convert("L").crop((left, top, right, bottom))
    histogram = grey.histogram()
    total = sum(histogram)
    if not total:
        return 0

    seen = 0
    cutoff = total * percentile
    for level, count in enumerate(histogram):
        seen += count
        if seen >= cutoff:
            return level
    return 255


def band_scrim(image: Image.Image, top: int, bottom: int,
               strength: float) -> Image.Image:
    """Lay a feathered navy scrim over a horizontal band.

    Feathered on purpose. A hard-edged rectangle reads as a graphic box
    pasted onto a photograph; a gradient that fades out above the band reads
    as light falling off, which is what the eye expects.
    """
    if strength <= 0:
        return image

    height = image.height
    feather = max(1, int((bottom - top) * 0.55))
    layer = Image.new("L", (1, height), 0)
    pixels = layer.load()

    peak = int(max(0, min(255, strength * 255)))
    for y in range(height):
        if y < top - feather:
            value = 0
        elif y < top:
            value = int(peak * (y - (top - feather)) / feather)
        elif y <= bottom:
            value = peak
        else:
            value = peak
        pixels[0, y] = value

    mask = layer.resize((image.width, height))
    mask = mask.filter(ImageFilter.GaussianBlur(2))

    navy = Image.new("RGB", image.size, brand.NAVY_DEEP)
    return Image.composite(navy, image, mask)


def seat_bands(image: Image.Image,
               bands: list[tuple[tuple[int, int, int, int], int]],
               scrim_top: int) -> tuple[Image.Image, float, list[int]]:
    """Deepen one scrim until every band clears its own target.

    One scrim, not one per band. Applying a second scrim over ground the
    first already darkened compounds unevenly and produces a visible step
    partway down the frame. A single gradient, judged against every band it
    covers, is both simpler and what the eye expects from falling light.

    Returns the image, the strength used, and the level reached in each band,
    so the caller can report what happened instead of assuming it worked.
    """
    levels = [region_levels(image, band) for band, _ in bands]
    if all(level <= target for level, (_, target) in zip(levels, bands)):
        return image, 0.0, levels

    candidate = image
    strength = 0.0
    for step in range(1, SCRIM_STEPS + 1):
        strength = step / SCRIM_STEPS * 0.94
        candidate = band_scrim(image, scrim_top, image.height, strength)
        levels = [region_levels(candidate, band) for band, _ in bands]
        if all(level <= target for level, (_, target) in zip(levels, bands)):
            return candidate, strength, levels

    # Ran out of scrim. Hand back the deepest tried and let the caller say so
    # - silently shipping unreadable type is the failure this exists to stop.
    return candidate, strength, levels


# --------------------------------------------------------------------------
# Text fitting
# --------------------------------------------------------------------------

def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_headline(draw: ImageDraw.ImageDraw, text: str, max_width: int,
                 max_height: int,
                 max_lines: int = HEADLINE_MAX_LINES) -> tuple[Any, list[str], int]:
    """Largest size at which the headline fits the box in few enough lines.

    max_lines is a parameter because the split layout gives the type half
    the width. Forcing two lines there shrinks the headline to something
    apologetic; three lines at a real size reads far better.
    """
    words = text.split()
    for size in HEADLINE_SIZES:
        font = brand.load_font("display", size)

        # A word wider than the column cannot be wrapped out of trouble -
        # _wrap puts it on a line of its own and lets it overflow. On the
        # split layout, where the column is half the canvas, that put
        # REMEMBER and UNPREPARED straight across the photograph. Shrink
        # until the longest word fits.
        if any(draw.textlength(word, font=font) > max_width for word in words):
            continue

        lines = _wrap(draw, text, font, max_width)
        if len(lines) > max_lines:
            continue
        line_height = int(size * LINE_SPACING)
        if line_height * len(lines) <= max_height:
            return font, lines, line_height
    font = brand.load_font("display", HEADLINE_SIZES[-1])
    lines = _wrap(draw, text, font, max_width)[:max_lines]
    return font, lines, int(HEADLINE_SIZES[-1] * LINE_SPACING)


def fit_strapline(draw: ImageDraw.ImageDraw, max_width: int) -> tuple[Any, int]:
    """Largest size at which the standing line still fits on one line.

    One line, always. Wrapped across two it stops reading as a strapline and
    starts competing with the headline underneath it.
    """
    for size in STRAPLINE_SIZES:
        font = brand.load_font("display_alt", size)
        if tracked_width(draw, brand.STRAPLINE, font,
                         STRAPLINE_TRACKING) <= max_width:
            return font, int(size * 1.25)
    size = STRAPLINE_SIZES[-1]
    return brand.load_font("display_alt", size), int(size * 1.25)


def draw_strapline(draw: ImageDraw.ImageDraw, x: int, y: int, font) -> None:
    """Draw the standing line with its tail in orange.

    Drawn as two segments rather than one string so the emphasis lands on
    the brand's own word. The head is measured, not guessed, because a
    proportional face makes the split point impossible to assume.
    """
    head = brand.STRAPLINE[:-len(brand.STRAPLINE_EMPHASIS)]
    used = draw_tracked(draw, (x, y), head, font, brand.WHITE,
                        STRAPLINE_TRACKING)
    draw_tracked(draw, (x + used + STRAPLINE_TRACKING, y),
                 brand.STRAPLINE_EMPHASIS, font, brand.ORANGE_LIGHT,
                 STRAPLINE_TRACKING)


def fit_accent(draw: ImageDraw.ImageDraw, text: str,
               max_width: int) -> tuple[Any, list[str], int]:
    for size in ACCENT_SIZES:
        font = brand.load_font("body_medium", size)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= 2:
            return font, lines, int(size * 1.28)
    font = brand.load_font("body_medium", ACCENT_SIZES[-1])
    return font, _wrap(draw, text, font, max_width)[:2], int(ACCENT_SIZES[-1] * 1.28)


# --------------------------------------------------------------------------
# Pieces
# --------------------------------------------------------------------------

def grade(image: Image.Image) -> Image.Image:
    """Pull a stock photograph into the brand's own light.

    This is the single biggest thing separating a feed that looks designed
    from one that looks bought. Every photo arrives with its own cast - one
    office is warm tungsten, the next is cold daylight - and posted raw they
    never look like a set. Desaturating a little, lifting contrast slightly
    and split-toning the shadows towards the brand navy makes thirty
    different shoots read as one campaign.
    """
    grey = ImageOps.grayscale(image).convert("RGB")
    image = Image.blend(image, grey, GRADE_DESATURATE)
    image = ImageEnhance.Contrast(image).enhance(GRADE_CONTRAST)

    # Split tone: navy into the shadows, a touch of warmth into the
    # highlights. Built as lookup tables so it is one pass over the image.
    lut: list[int] = []
    for channel, (shadow_shift, highlight_shift) in enumerate(GRADE_SPLIT):
        for value in range(256):
            weight = value / 255.0
            shifted = value + shadow_shift * (1 - weight) ** 2 \
                            + highlight_shift * weight ** 2
            lut.append(max(0, min(255, int(round(shifted)))))
    return image.point(lut)


def vignette(image: Image.Image) -> Image.Image:
    """Darken the corners very slightly, to sit the subject in the frame."""
    width, height = image.size
    small = (max(1, width // 8), max(1, height // 8))
    mask = Image.new("L", small, 0)
    ImageDraw.Draw(mask).ellipse(
        [(-small[0] * 0.28, -small[1] * 0.22),
         (small[0] * 1.28, small[1] * 1.22)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(small[0] * 0.18))
    mask = mask.resize(image.size, Image.BILINEAR)

    dark = ImageEnhance.Brightness(image).enhance(1 - VIGNETTE_STRENGTH)
    return Image.composite(image, dark, mask)


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                 font, fill, tracking: float) -> float:
    """Draw text with letter-spacing, returning the width used.

    PIL has no tracking, and tracked caps are most of what makes a small
    label read as considered rather than as a default. Worth the loop.
    """
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += draw.textlength(character, font=font) + tracking
    return x - xy[0] - tracking if text else 0.0


def tracked_width(draw: ImageDraw.ImageDraw, text: str, font,
                  tracking: float) -> float:
    if not text:
        return 0.0
    return sum(draw.textlength(c, font=font)
               for c in text) + tracking * (len(text) - 1)


def draw_chip(image: Image.Image) -> Image.Image:
    """The category chip, top right - what this Page is actually selling.

    Without it a viewer sees a smart photograph and a motivational line and
    has no idea the subject is English. The strapline carries the promise;
    this carries the category, which is what a corporate reader scans for.
    """
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    margin = int(width * MARGIN_X)

    font = brand.load_font("body_medium", int(height * CHIP_TEXT))
    tracking = height * CHIP_TRACKING
    text_width = tracked_width(draw, brand.CATEGORY, font, tracking)

    pad_x = int(height * 0.014)
    pad_y = int(height * 0.009)
    chip_width = int(text_width) + pad_x * 2 + CHIP_RULE
    chip_height = int(height * CHIP_TEXT * 1.45) + pad_y * 2

    right = width - margin
    top = int(height * LOGO_TOP) + int(height * LOGO_BOX[1] * 0.22)
    left = right - chip_width

    draw.rounded_rectangle([(left, top), (right, top + chip_height)],
                           radius=int(chip_height * 0.16),
                           fill=(*brand.NAVY_DEEP, 205))
    draw.rectangle([(left, top), (left + CHIP_RULE, top + chip_height)],
                   fill=brand.ORANGE)

    draw_tracked(draw, (left + CHIP_RULE + pad_x, top + pad_y),
                 brand.CATEGORY, font, brand.WHITE, tracking)
    return image


def crop_to_canvas(image: Image.Image, canvas: tuple[int, int]) -> Image.Image:
    target = canvas[0] / canvas[1]
    width, height = image.size
    if width / height > target:
        new_width = round(height * target)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = round(width / target)
        top = (height - new_height) // 3      # bias up: faces sit high
        image = image.crop((0, top, width, top + new_height))
    return image.resize(canvas, Image.LANCZOS)


def draw_footer(image: Image.Image) -> Image.Image:
    """The navy footer: call line on the left, WhatsApp QR on the right.

    The bar is tall enough for the QR rather than the other way round. A QR
    scaled down to fit a thin strip stops being scannable, which turns the
    whole point of putting it there into decoration.
    """
    width, height = image.size
    bar_height = int(height * FOOTER_HEIGHT)
    top = height - bar_height

    image.paste(Image.new("RGB", (width, bar_height), brand.NAVY), (0, top))
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, top), (width, top + 5)], fill=brand.ORANGE)

    margin = int(width * MARGIN_X)

    # QR first - it is fixed size, and the text has to fit in what is left.
    qr_side = int(bar_height * QR_OF_FOOTER)
    qr = assets.fit_qr(qr_side)
    qr_x = width - margin - qr_side
    qr_y = top + (bar_height - qr_side) // 2
    image.paste(qr, (qr_x, qr_y), qr)

    # The prompt sits to the QR's left, not under it. Underneath, it either
    # ran off the bottom of the canvas or clung to the edge, and the bar is
    # wide open between the phone number and the code.
    scan_font = brand.load_font("body_medium", int(bar_height * 0.115))
    scan_lines = brand.SCAN_LINE.split(" to ")
    scan_lines = [scan_lines[0] + " to", scan_lines[1]] if len(scan_lines) == 2 \
        else [brand.SCAN_LINE]
    line_height = int(bar_height * 0.115 * 1.30)
    scan_y = qr_y + (qr_side - line_height * len(scan_lines)) // 2
    for line in scan_lines:
        line_width = draw.textlength(line, font=scan_font)
        draw.text((qr_x - int(width * 0.022) - line_width, scan_y), line,
                  font=scan_font, fill=brand.OFF_WHITE)
        scan_y += line_height

    call_font = brand.load_font("display", int(bar_height * 0.30))
    cta_font = brand.load_font("body_medium", int(bar_height * 0.135))

    block_top = top + bar_height * 0.24
    draw.text((margin, block_top), brand.CALL_LINE, font=call_font,
              fill=brand.WHITE)
    draw.text((margin + 2, block_top + bar_height * 0.36), brand.CTA_LINE,
              font=cta_font, fill=brand.ORANGE_LIGHT)
    return image


def place_logo(image: Image.Image) -> Image.Image:
    width, height = image.size
    box = (int(width * LOGO_BOX[0]), int(height * LOGO_BOX[1]))
    mark = assets.fit_logo(*box, on_plate=True)
    image.paste(mark, (int(width * MARGIN_X), int(height * LOGO_TOP)), mark)
    return image


# --------------------------------------------------------------------------
# The layout
# --------------------------------------------------------------------------

def _layout_photo_dark(entry: dict, background: Path, out_path: Path,
                       canvas: str = "portrait") -> dict[str, Any]:
    """Full-bleed photograph, type seated into the lower third."""
    size = brand.CANVAS.get(canvas)
    if not size:
        raise RenderError(f"Unknown canvas {canvas!r}")

    background = Path(background)
    if not background.is_file():
        raise RenderError(f"Background not found: {background}")

    image = vignette(grade(crop_to_canvas(
        Image.open(background).convert("RGB"), size)))
    width, height = image.size
    margin = int(width * MARGIN_X)
    max_text_width = width - margin * 2

    measure = ImageDraw.Draw(image)

    headline = (entry.get("headline") or "").strip()
    accent = (entry.get("accent") or "").strip()
    if not headline:
        raise RenderError(f"Entry {entry.get('id')} has no headline")

    footer_top = height - int(height * FOOTER_HEIGHT)
    text_bottom = int(height * TEXT_BOTTOM)

    accent_font, accent_lines, accent_line_h = fit_accent(
        measure, accent, max_text_width) if accent else (None, [], 0)
    accent_block = accent_line_h * len(accent_lines)
    accent_gap = int(accent_line_h * ACCENT_GAP) if accent_lines else 0

    headline_bottom = text_bottom - accent_block - accent_gap
    headline_font, headline_lines, line_height = fit_headline(
        measure, headline, max_text_width,
        max_height=int(height * 0.30))
    headline_block = line_height * len(headline_lines)
    headline_top = headline_bottom - headline_block

    strap_font, strap_height = fit_strapline(measure, max_text_width)
    strap_gap = int(strap_height * STRAPLINE_GAP)
    strap_top = headline_top - strap_gap - strap_height

    # One scrim, judged against every band. The accent gets the stricter
    # target because it is smaller and lower contrast - it is always what
    # fails first, so it is what the scrim has to satisfy. The strapline
    # sits inside the same scrim, which is why it goes above the headline
    # rather than up beside the logo where it would need its own.
    band_pad = int(height * 0.018)
    bands = [((0, strap_top - band_pad, width, headline_bottom),
              HEADLINE_TARGET)]
    if accent_lines:
        bands.append(((0, headline_bottom, width, text_bottom + band_pad),
                      ACCENT_TARGET))

    image, strength, levels = seat_bands(
        image, bands, scrim_top=strap_top - band_pad)

    head_level = levels[0]
    accent_level = levels[1] if len(levels) > 1 else None

    draw = ImageDraw.Draw(image)

    draw_strapline(draw, margin, strap_top, strap_font)

    y = headline_top
    for line in headline_lines:
        draw.text((margin, y), line, font=headline_font, fill=brand.WHITE)
        y += line_height

    if accent_lines:
        # The rule sits in the gap, centred between the headline's last
        # baseline and the accent's first. Anchoring it to a fraction of the
        # gap put it through the headline on any two-line layout.
        rule_y = headline_bottom + (accent_gap - RULE_HEIGHT) // 2
        draw.rectangle(
            [(margin, rule_y),
             (margin + int(width * 0.085), rule_y + RULE_HEIGHT)],
            fill=brand.ORANGE)

        y = headline_bottom + accent_gap
        for line in accent_lines:
            draw.text((margin, y), line, font=accent_font,
                      fill=brand.ORANGE_PALE)
            y += accent_line_h

    image = draw_footer(image)
    image = place_logo(image)
    image = draw_chip(image)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "JPEG", quality=92, optimize=True)

    seated = (head_level <= HEADLINE_TARGET and
              (accent_level is None or accent_level <= ACCENT_TARGET))
    return {
        "image_path": str(out_path),
        "background": background.name,
        "canvas": canvas,
        "strapline": brand.STRAPLINE,
        "strapline_size": strap_font.size,
        "headline_lines": headline_lines,
        "headline_size": headline_font.size,
        "accent_lines": accent_lines,
        "accent_size": accent_font.size if accent_font else None,
        "scrim": {
            "strength": round(strength, 3),
            "headline_level": head_level,
            "headline_target": HEADLINE_TARGET,
            "accent_level": accent_level,
            "accent_target": ACCENT_TARGET,
        },
        "text_seated": seated,
    }


# --------------------------------------------------------------------------
# Split layout: light panel left, photograph curving in from the right
# --------------------------------------------------------------------------

SPLIT_X = 0.50                   # where the photo panel starts
SPLIT_BULGE = 0.055              # how far the divider curves into the panel
SPLIT_TEXT_TOP = 0.255
CARD_RADIUS = 0.022
DOT_RADIUS = 5


def _curved_photo(image: Image.Image, canvas: tuple[int, int],
                  bottom: int) -> Image.Image:
    """The photograph, masked to a softly curved right-hand panel.

    The curve is the device the Academy's own flyers already use to divide
    panel from photograph. A straight edge reads as two pictures pasted
    side by side; the curve reads as one composition.
    """
    width, height = canvas
    photo = vignette(grade(crop_to_canvas(image, (width, height))))

    mask = Image.new("L", canvas, 0)
    draw = ImageDraw.Draw(mask)

    base = width * SPLIT_X
    amplitude = width * SPLIT_BULGE
    steps = 96
    points = [(width, 0)]
    for step in range(steps + 1):
        y = bottom * step / steps
        # One smooth lobe, furthest left at mid height.
        x = base - amplitude * math.sin(math.pi * step / steps)
        points.append((x, y))
    points.append((width, bottom))
    draw.polygon(points, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))

    ground = Image.new("RGB", canvas, brand.PAPER)
    return Image.composite(photo, ground, mask)


def _feature_card(image: Image.Image, top: int, left: int, right: int) -> int:
    """A white card listing what is taught. Returns its bottom edge.

    Three plain labels separated by dots, not icons. Icons drawn with
    primitives at this size look drawn with primitives, and the whole point
    of this layout is that the creative should not.
    """
    draw = ImageDraw.Draw(image)
    width, height = image.size

    labels = [label.upper() for label in brand.FEATURES]
    pad_x = int(width * 0.030)
    available = (right - left) - pad_x * 2

    # Shrink until the row genuinely fits. The first version sized the card
    # from the layout and trusted the content to be smaller; it was not, and
    # the row ran off both edges of the canvas.
    text_size = gap = tracking = 0
    label_widths: list[float] = []
    for candidate in range(int(height * 0.0155), int(height * 0.0090), -1):
        font = brand.load_font("body_medium", candidate)
        tracking = height * 0.0012
        gap = height * 0.016
        label_widths = [tracked_width(draw, label, font, tracking)
                        for label in labels]
        if sum(label_widths) + gap * 2 * (len(labels) - 1) <= available:
            text_size = candidate
            break
    if not text_size:
        text_size = int(height * 0.0090)
        font = brand.load_font("body_medium", text_size)
        label_widths = [tracked_width(draw, label, font, tracking)
                        for label in labels]

    content = sum(label_widths) + gap * 2 * (len(labels) - 1)
    pad_y = int(height * 0.018)
    card_height = int(text_size * 1.5) + pad_y * 2

    draw.rounded_rectangle([(left, top), (right, top + card_height)],
                           radius=int(height * CARD_RADIUS), fill=brand.WHITE)

    x = left + ((right - left) - content) / 2
    y = top + pad_y
    for index, (label, label_width) in enumerate(zip(labels, label_widths)):
        draw_tracked(draw, (x, y), label, font, brand.NAVY, tracking)
        x += label_width
        if index < len(labels) - 1:
            centre_x = x + gap
            centre_y = y + text_size * 0.55
            draw.ellipse([(centre_x - DOT_RADIUS, centre_y - DOT_RADIUS),
                          (centre_x + DOT_RADIUS, centre_y + DOT_RADIUS)],
                         fill=brand.ORANGE)
            x += gap * 2
    return top + card_height


def _layout_split_light(entry: dict, background: Path, out_path: Path,
                        canvas: str = "portrait") -> dict[str, Any]:
    """Light panel carrying the type, photograph curving in from the right.

    The inverse of the dark layout, and it needs none of the scrim
    machinery: the type sits on a flat brand colour, so its contrast is
    fixed by construction rather than measured against a photograph. That
    also lets the logo go on bare, with no plate - the more considered look,
    and only safe because the ground behind it is known.
    """
    size = brand.CANVAS.get(canvas)
    if not size:
        raise RenderError(f"Unknown canvas {canvas!r}")
    background = Path(background)
    if not background.is_file():
        raise RenderError(f"Background not found: {background}")

    headline = (entry.get("headline") or "").strip()
    if not headline:
        raise RenderError(f"Entry {entry.get('id')} has no headline")
    accent = (entry.get("accent") or "").strip()

    width, height = size
    margin = int(width * MARGIN_X)
    footer_top = height - int(height * FOOTER_HEIGHT)

    image = _curved_photo(Image.open(background).convert("RGB"), size,
                          footer_top)
    draw = ImageDraw.Draw(image)

    # Type stays clear of the curve at its widest point.
    text_right = int(width * SPLIT_X - width * SPLIT_BULGE - width * 0.03)
    text_width = text_right - margin

    logo_top = int(height * 0.052)
    logo = assets.fit_logo(int(width * 0.30), int(height * 0.10),
                           on_plate=False)
    image.paste(logo, (margin, logo_top), logo)

    chip_font = brand.load_font("body_medium", int(height * CHIP_TEXT))
    chip_tracking = height * CHIP_TRACKING
    draw_tracked(draw, (margin + 2, logo_top + logo.height + int(height * 0.026)),
                 brand.CATEGORY, chip_font, brand.ORANGE, chip_tracking)

    strap_font, strap_height = fit_strapline(draw, text_width)
    head_font, head_lines, line_height = fit_headline(
        draw, headline, text_width, max_height=int(height * 0.32),
        max_lines=3)

    y = int(height * SPLIT_TEXT_TOP)
    head = brand.STRAPLINE[:-len(brand.STRAPLINE_EMPHASIS)]
    used = draw_tracked(draw, (margin, y), head, strap_font, brand.NAVY_SOFT,
                        STRAPLINE_TRACKING)
    draw_tracked(draw, (margin + used + STRAPLINE_TRACKING, y),
                 brand.STRAPLINE_EMPHASIS, strap_font, brand.ORANGE,
                 STRAPLINE_TRACKING)
    y += int(strap_height * 1.5)

    for line in head_lines:
        draw.text((margin, y), line, font=head_font, fill=brand.NAVY)
        y += line_height

    y += int(height * 0.014)
    draw.rectangle([(margin, y), (margin + int(width * 0.085), y + RULE_HEIGHT)],
                   fill=brand.ORANGE)
    y += RULE_HEIGHT + int(height * 0.024)

    accent_font = None
    accent_lines: list[str] = []
    if accent:
        accent_font, accent_lines, accent_line_h = fit_accent(
            draw, accent, text_width)
        for line in accent_lines:
            draw.text((margin, y), line, font=accent_font, fill=brand.NAVY_SOFT)
            y += accent_line_h

    card_height = int(height * 0.0155 * 1.5) + int(height * 0.018) * 2
    card_top = min(y + int(height * 0.045),
                   footer_top - int(height * 0.045) - card_height)
    _feature_card(image, card_top, margin, width - margin)

    image = draw_footer(image)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "JPEG", quality=92, optimize=True)

    return {
        "image_path": str(out_path),
        "background": background.name,
        "canvas": canvas,
        "layout": "split_light",
        "strapline": brand.STRAPLINE,
        "headline_lines": head_lines,
        "headline_size": head_font.size,
        "accent_lines": accent_lines,
        "accent_size": accent_font.size if accent_font else None,
        # No scrim by design: flat panel, so contrast is fixed by
        # construction instead of measured per photograph.
        "scrim": None,
        "text_seated": True,
    }


LAYOUTS = {
    "split_light": _layout_split_light,
    "photo_dark": _layout_photo_dark,
}

DEFAULT_LAYOUT = "split_light"


def render(entry: dict, background: Path, out_path: Path,
           canvas: str = "portrait",
           layout: str = DEFAULT_LAYOUT) -> dict[str, Any]:
    """Render one creative. Returns what was measured, for the batch record."""
    builder = LAYOUTS.get(layout)
    if not builder:
        raise RenderError(
            f"Unknown layout {layout!r}. Available: {', '.join(LAYOUTS)}")
    result = builder(entry, background, out_path, canvas)
    result.setdefault("layout", layout)
    return result
