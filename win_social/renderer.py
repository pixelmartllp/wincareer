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


def draw_footer(image: Image.Image, subline: str = "") -> Image.Image:
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
    draw.text((margin + 2, block_top + bar_height * 0.36),
              subline or brand.CTA_LINE, font=cta_font,
              fill=brand.ORANGE_LIGHT)
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

DEFAULT_LAYOUT = "dark_hero"


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


# --------------------------------------------------------------------------
# Dark hero: near-black ground, stacked hook, the mentor on the right
# --------------------------------------------------------------------------
#
# Built from four references the owner supplied. The palette and the lit-desk
# composition come from the MET creative; the stacked hook and the free-demo
# prominence from the SKH one; the restraint - a headline, one supporting
# line, a badge, a footer, and nothing else - is the COMEX structure with the
# content stripped back, which is what he asked for.

INK_GROUND = (14, 13, 16)        # near black, very slightly warm
INK_GLOW = (38, 30, 24)          # the warm pool a desk lamp would throw

HERO_SPLIT = 0.46                # where the mentor's panel begins
HERO_FADE = 0.16                 # width of the fade into the ground
HOOK_SIZES = tuple(range(104, 55, -3))
HOOK_MAX_LINES = 3
BADGE_TEXT = 0.030            # the offer, set to actually be read
BADGE_SUB_TEXT = 0.0135
KICKER_TEXT = 0.0215          # "LEARN ENGLISH SPEAKING" - what this even is
OFFER_TILT = -4.5             # degrees; past ~8 the block reads as a sticker
MENTOR_DIR = "mentor"


def list_mentor_photos() -> list[Path]:
    folder = brand.ASSETS / MENTOR_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def _warm_ground(canvas: tuple[int, int]) -> Image.Image:
    """Near-black with a soft warm pool, the way a lit desk reads."""
    width, height = canvas
    ground = Image.new("RGB", canvas, INK_GROUND)

    small = (max(1, width // 6), max(1, height // 6))
    mask = Image.new("L", small, 0)
    ImageDraw.Draw(mask).ellipse(
        [(small[0] * 0.30, small[1] * 0.18),
         (small[0] * 1.25, small[1] * 0.95)], fill=190)
    mask = mask.filter(ImageFilter.GaussianBlur(small[0] * 0.30))
    mask = mask.resize(canvas, Image.BILINEAR)

    return Image.composite(Image.new("RGB", canvas, INK_GLOW), ground, mask)


MENTOR_BADGE = 0.105             # badge diameter as a fraction of the canvas
MENTOR_FACE = (0.64, 0.23)       # where her face sits in the mentor photo
MENTOR_FACE_BOX = 0.30           # crop size, as a fraction of its height


def _badge_photo_name() -> str | None:
    photos = list_mentor_photos()
    return photos[0].name if photos else None


def _mentor_badge(image: Image.Image, footer_top: int) -> None:
    """A small circular portrait of the mentor, with her name beside it.

    She used to be the whole right-hand panel. That made every post a
    portrait of the same person, at the same desk, in the same cardigan -
    and it left no room for a picture of what the line is actually about.
    Small and circular keeps the Academy identifiable without spending the
    frame on it.

    Silently skipped when there is no photograph: this is identity, not
    structure, and a missing file should not stop the day going out.
    """
    photos = list_mentor_photos()
    if not photos:
        return

    width, height = image.size
    side = int(height * MENTOR_BADGE)
    margin = int(width * MARGIN_X)

    portrait = Image.open(photos[0]).convert("RGB")
    w, h = portrait.size
    # Crop tight on the face. The first version took the largest square from
    # the top of the frame, which on this photograph is mostly the logo on
    # the wall behind her - a badge of the wall, not of the mentor. These
    # fractions describe where she is in *this* photograph; a replacement
    # shot may need them moved.
    box = int(h * MENTOR_FACE_BOX)
    cx, cy = int(w * MENTOR_FACE[0]), int(h * MENTOR_FACE[1])
    left = max(0, min(w - box, cx - box // 2))
    top = max(0, min(h - box, cy - box // 2))
    portrait = portrait.crop((left, top, left + box, top + box))
    portrait = portrait.resize((side, side), Image.LANCZOS)

    mask = Image.new("L", (side * 4, side * 4), 0)
    ImageDraw.Draw(mask).ellipse([(0, 0), (side * 4 - 1, side * 4 - 1)],
                                 fill=255)
    mask = mask.resize((side, side), Image.LANCZOS)   # cheap antialiasing

    ring = Image.new("RGBA", (side + 8, side + 8), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([(0, 0), (side + 7, side + 7)],
                                 fill=(*brand.ORANGE, 255))
    x = margin
    y = footer_top - side - int(height * 0.055)
    image.paste(ring, (x - 4, y - 4), ring)
    image.paste(portrait, (x, y), mask)

    draw = ImageDraw.Draw(image)
    name_font = brand.load_font("body_medium", int(height * 0.0155))
    role_font = brand.load_font("body", int(height * 0.0125))
    text_x = x + side + int(width * 0.022)
    draw.text((text_x, y + side * 0.26), brand.MENTOR, font=name_font,
              fill=brand.WHITE)
    draw.text((text_x, y + side * 0.55), "Director & Mentor", font=role_font,
              fill=brand.GREY)


def mentor_variant(key: str, photos: list[Path]) -> tuple[Path, str, float]:
    """Pick the photograph, the side it sits on, and how tight the crop is.

    One photograph does not have to mean one composition. Varying the side of
    the frame and the crop gives four visibly different creatives from a
    single source, which is what keeps consecutive days from looking like a
    reprint while there is only one photograph to work with.

    The photograph itself is never mirrored: the Academy's logo is on the
    wall behind her, and flipping it reverses the lettering.
    """
    seed = sum(ord(c) for c in str(key))
    photo = photos[seed % len(photos)]
    zoom = (1.0, 1.14, 1.07, 1.21)[seed % 4]

    # Side is fixed to the right, and that is a limitation of the source
    # rather than a choice. Alternating it was tried: the only mentor
    # photograph has her standing in the right of the frame, so masking the
    # left half showed the wall behind her with her head cropped off. The
    # left path is kept because a photograph composed the other way would
    # use it correctly - it is simply not selectable automatically without
    # knowing where the subject stands.
    side = "right"
    return photo, side, zoom


def _mentor_panel(photo: Path, canvas: tuple[int, int],
                  bottom: int, side: str = "right",
                  zoom: float = 1.0) -> Image.Image:
    """The mentor on the right, faded into the ground rather than cut out.

    A hard cut-out needs a segmentation model and looks like a sticker when
    it is even slightly wrong. Her own photograph is already shot against a
    dark wall, so fading its left edge into the same near-black lets the two
    become one scene - which is exactly what the MET reference does.
    """
    width, height = canvas
    source = Image.open(photo).convert("RGB")
    if zoom > 1.0:
        w, h = source.size
        inset_x, inset_y = int(w * (1 - 1 / zoom) / 2), int(h * (1 - 1 / zoom) / 2)
        source = source.crop((inset_x, inset_y, w - inset_x, h - inset_y))

    image = vignette(grade(crop_to_canvas(source, (width, height))))
    image = ImageEnhance.Brightness(image).enhance(0.86)

    mask = Image.new("L", canvas, 0)
    draw = ImageDraw.Draw(mask)
    fade = int(width * HERO_FADE)

    if side == "right":
        start = int(width * HERO_SPLIT)
        draw.rectangle([(start + fade, 0), (width, bottom)], fill=255)
        for step in range(fade):
            draw.rectangle([(start + step, 0), (start + step + 1, bottom)],
                           fill=int(255 * (step / fade) ** 1.4))
    else:
        end = int(width * (1 - HERO_SPLIT))
        draw.rectangle([(0, 0), (end - fade, bottom)], fill=255)
        for step in range(fade):
            draw.rectangle([(end - step, 0), (end - step + 1, bottom)],
                           fill=int(255 * (step / fade) ** 1.4))
    mask = mask.filter(ImageFilter.GaussianBlur(6))

    return Image.composite(image, _warm_ground(canvas), mask)


def fit_hook(draw: ImageDraw.ImageDraw, text: str, max_width: int,
             max_height: int) -> tuple[Any, list[str], int]:
    words = text.split()
    for size in HOOK_SIZES:
        font = brand.load_font("display", size)
        if any(draw.textlength(w, font=font) > max_width for w in words):
            continue
        lines = _wrap(draw, text, font, max_width)
        if len(lines) > HOOK_MAX_LINES:
            continue
        line_height = int(size * LINE_SPACING)
        if line_height * len(lines) <= max_height:
            return font, lines, line_height
    font = brand.load_font("display", HOOK_SIZES[-1])
    return (font, _wrap(draw, text, font, max_width)[:HOOK_MAX_LINES],
            int(HOOK_SIZES[-1] * LINE_SPACING))


def _angled_offer(image: Image.Image, x: int, y: int) -> int:
    """The offer as a tilted block. Returns its bottom edge.

    Borrowed from the NS Study reference the owner sent, where the offer is a
    rotated slab and is the loudest thing on the canvas. The tilt is what does
    the work: everything else on the creative sits on a horizontal, so a few
    degrees off makes the block read as applied rather than laid out.

    Kept to a small angle. Past about eight degrees the type starts to look
    like a sticker rather than a designed element, and the bounding box eats
    into the headline above it.
    """
    width, height = image.size

    font = brand.load_font("display", int(height * BADGE_TEXT))
    sub_font = brand.load_font("body_medium", int(height * BADGE_SUB_TEXT))
    tracking = height * 0.0010
    sub = "Book your seat today"

    probe = ImageDraw.Draw(image)
    text_width = tracked_width(probe, brand.OFFER, font, tracking)
    inner = max(text_width, probe.textlength(sub, font=sub_font))

    pad_x, pad_y = int(width * 0.034), int(height * 0.016)
    badge_w = int(inner) + pad_x * 2
    badge_h = (int(height * BADGE_TEXT * 1.15)
               + int(height * BADGE_SUB_TEXT * 1.9) + pad_y * 2)

    # Drawn upright on its own transparent layer, then rotated - rotating the
    # finished text keeps the letterforms clean, where drawing on an angle
    # would have to fake it per glyph.
    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rounded_rectangle([(0, 0), (badge_w - 1, badge_h - 1)],
                         radius=int(height * 0.010), fill=(*brand.ORANGE, 255))
    draw_tracked(bd, (pad_x, pad_y), brand.OFFER, font, brand.WHITE, tracking)
    bd.text((pad_x + 2, pad_y + int(height * BADGE_TEXT * 1.25)), sub,
            font=sub_font, fill=(255, 226, 208))

    badge = badge.rotate(OFFER_TILT, resample=Image.BICUBIC, expand=True)
    image.paste(badge, (x - int(width * 0.012), y), badge)
    return y + badge.height


def _demo_badge(image: Image.Image, x: int, y: int) -> int:
    """The free-demo offer block. Returns its bottom edge.

    Sized to be seen, not merely present. The first version set this in a
    small pill and the owner's verdict on the result was that the creative
    said nothing about free demo classes at all - which was fair: at that
    size it read as a caption. On both reference ads the offer is the loudest
    object after the headline, so it is set in the display face and given a
    line of supporting text underneath.
    """
    draw = ImageDraw.Draw(image)
    width, height = image.size

    font = brand.load_font("display", int(height * BADGE_TEXT))
    tracking = height * 0.0010
    text = brand.OFFER
    text_width = tracked_width(draw, text, font, tracking)

    sub_font = brand.load_font("body_medium", int(height * BADGE_SUB_TEXT))
    sub = "Book your seat today"
    sub_width = draw.textlength(sub, font=sub_font)

    pad_x, pad_y = int(width * 0.034), int(height * 0.016)
    inner = max(text_width, sub_width)
    badge_w = int(inner) + pad_x * 2
    badge_h = (int(height * BADGE_TEXT * 1.15) + int(height * BADGE_SUB_TEXT * 1.9)
               + pad_y * 2)

    draw.rounded_rectangle([(x, y), (x + badge_w, y + badge_h)],
                           radius=int(height * 0.016), fill=brand.ORANGE)
    draw_tracked(draw, (x + pad_x, y + pad_y), text, font, brand.WHITE,
                 tracking)
    draw.text((x + pad_x + 2, y + pad_y + int(height * BADGE_TEXT * 1.25)),
              sub, font=sub_font, fill=(255, 226, 208))
    return y + badge_h


def _layout_dark_hero(entry: dict, background: Path, out_path: Path,
                      canvas: str = "portrait") -> dict[str, Any]:
    size = brand.CANVAS.get(canvas)
    if not size:
        raise RenderError(f"Unknown canvas {canvas!r}")

    headline = (entry.get("headline") or "").strip()
    if not headline:
        raise RenderError(f"Entry {entry.get('id')} has no headline")
    accent = (entry.get("accent") or "").strip()

    # The hero image is the day's photograph, chosen for the line it carries.
    # The mentor is no longer the whole right-hand panel - she is a small
    # badge, which is what keeps the Academy identifiable without making
    # every post a portrait of the same person on the same day at the same
    # desk.
    # This layout has its own pool. It fades the left 46% of the frame into
    # the ground, so a photograph whose subject sits centre or left comes
    # back with a face sliced in half - which is what the general pool, shot
    # for the other layouts, mostly gives. assets/hero/ holds frames composed
    # subject-right with dark empty space on the left.
    hero = assets.pick_hero(entry.get("theme"),
                            seed=sum(ord(c) for c in str(entry.get("id", ""))))
    if hero is None:
        raise RenderError(
            f"No photographs in {brand.ASSETS / assets.HERO_DIR}. This layout "
            f"needs frames composed subject-right; the general background "
            f"pool is not, and its subjects get cut by the fade.")
    photo, zoom = hero, 1.0
    side = "right"

    width, height = size
    margin = int(width * MARGIN_X)
    footer_top = height - int(height * FOOTER_HEIGHT)

    image = _mentor_panel(photo, size, footer_top, side=side, zoom=zoom)
    _mentor_badge(image, footer_top)
    draw = ImageDraw.Draw(image)

    # When the mentor sits on the left, the type moves to the right half.
    text_width = int(width * HERO_SPLIT + width * 0.02) - margin
    margin = margin if side == "right" else width - margin - text_width

    # Plated, even here. The light-ink variant rescues THE and ACADEMY but
    # can do nothing for the CAREER ribbon, whose dark red and purple have
    # almost no contrast against near-black - on the first pass the banner
    # dissolved into a smudge. The Academy's own flyers plate the logo over
    # dark photographs for the same reason, so this follows them rather than
    # inventing a treatment that half works.
    logo = assets.fit_logo(int(width * 0.30), int(height * 0.095),
                           on_plate=True)
    image.paste(logo, (margin, int(height * 0.050)), logo)

    # The kicker says what this is before the headline says anything clever.
    # As a small chip it was invisible, and a reader scrolling past could not
    # tell the Page taught English at all.
    y = int(height * 0.200)
    kicker_font = brand.load_font("display_alt", int(height * KICKER_TEXT))
    rule_w = int(width * 0.055)
    draw.rectangle([(margin, y + int(height * 0.012)),
                    (margin + rule_w, y + int(height * 0.012) + 5)],
                   fill=brand.ORANGE)
    draw_tracked(draw, (margin + rule_w + int(width * 0.020), y),
                 brand.CATEGORY, kicker_font, brand.ORANGE,
                 height * CHIP_TRACKING)
    y += int(height * 0.058)

    hook_font, hook_lines, line_height = fit_hook(
        draw, headline, text_width, int(height * 0.34))
    for i, line in enumerate(hook_lines):
        # Last line in orange: the references all land the emphasis at the
        # end of the stack.
        colour = brand.ORANGE if i == len(hook_lines) - 1 and \
            len(hook_lines) > 1 else brand.WHITE
        draw.text((margin, y), line, font=hook_font, fill=colour)
        y += line_height

    y += int(height * 0.016)
    if accent:
        accent_font, accent_lines, accent_line_h = fit_accent(
            draw, accent, text_width)
        for line in accent_lines:
            draw.text((margin, y), line, font=accent_font, fill=brand.GREY)
            y += accent_line_h
        y += int(height * 0.012)

    y = _angled_offer(image, margin, y + int(height * 0.014))

    image = draw_footer(image, subline=brand.BRAND_TAGLINE)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "JPEG", quality=92, optimize=True)

    return {
        "image_path": str(out_path),
        "background": photo.name,
        "offered_background": Path(background).name,
        "mentor_badge": _badge_photo_name(),
        "mentor_side": side,
        "mentor_zoom": zoom,
        "canvas": canvas,
        "layout": "dark_hero",
        "strapline": brand.STRAPLINE,
        "headline_lines": hook_lines,
        "headline_size": hook_font.size,
        "accent_lines": accent_lines if accent else [],
        "accent_size": accent_font.size if accent else None,
        "scrim": None,
        "text_seated": True,
    }


LAYOUTS["dark_hero"] = _layout_dark_hero


# --------------------------------------------------------------------------
# Light card: no photograph at all, type and shape doing the work
# --------------------------------------------------------------------------
#
# Modelled on the NS Study creative the owner sent: near-white ground, logo
# top left, a two-line tag top right, a stacked two-tone headline, the offer
# as a tilted slab, a feature row, and a contact bar.
#
# The reference fills its right side with a styled product photograph. There
# is no equivalent in this brand's assets, so that space is given to the
# offer and to two soft shapes instead. Faking a product shot with clip art
# would look exactly like faking a product shot.

CARD_KICKER = 0.0195
CARD_TAG_TEXT = 0.0150
CARD_HEAD_SIZES = tuple(range(112, 59, -3))
CARD_HEAD_MAX_LINES = 3


def _paper_ground(canvas: tuple[int, int], dark: bool = False) -> Image.Image:
    """Near-white, with two soft brand-coloured shapes.

    Very low contrast on purpose. They exist so the ground is not a blank
    rectangle; the moment they are strong enough to notice they start
    competing with the type, which is the only thing on this layout.
    """
    width, height = canvas
    ground = Image.new("RGB", canvas, INK_GROUND if dark else brand.PAPER_WARM)
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Barely-there on both grounds. The first dark pass used 46/34, on the
    # theory that a tint which works on paper would vanish into near-black.
    # It did not vanish - it turned into a large brown stain in the corner.
    # Dark needs *less*, not more, because there is no detail for it to hide
    # behind.
    warm, cool = (16, 12) if dark else (26, 20)
    draw.ellipse([(width * 0.52, -height * 0.10),
                  (width * 1.30, height * 0.42)],
                 fill=(*brand.ORANGE, warm))
    draw.ellipse([(-width * 0.28, height * 0.52),
                  (width * 0.36, height * 1.02)],
                 fill=(*brand.BLUE, cool))

    layer = layer.filter(ImageFilter.GaussianBlur(width * 0.02))
    return Image.alpha_composite(ground.convert("RGBA"), layer).convert("RGB")


def _corner_tag(image: Image.Image, dark: bool = False) -> None:
    """The strapline, right-aligned top corner, with a rule beside it.

    Two lines because it is a two-clause line, and splitting it on the comma
    is what lets the emphasis sit on its own row the way the reference does.
    """
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin = int(width * MARGIN_X)

    head, tail = brand.STRAPLINE.split(",", 1)
    lines = [(head.strip() + ",", brand.WHITE if dark else brand.NAVY),
             (tail.strip(), brand.ORANGE_LIGHT if dark else brand.ORANGE)]

    font = brand.load_font("body_medium", int(height * CARD_TAG_TEXT))
    line_h = int(height * CARD_TAG_TEXT * 1.55)
    top = int(height * 0.062)
    right = width - margin

    for i, (text, colour) in enumerate(lines):
        w = draw.textlength(text, font=font)
        draw.text((right - w, top + i * line_h), text, font=font, fill=colour)

    rule_x = right - max(draw.textlength(t, font=font) for t, _ in lines)
    draw.rectangle([(rule_x - int(width * 0.022), top),
                    (rule_x - int(width * 0.022) + 5,
                     top + line_h * len(lines) - int(height * 0.006))],
                   fill=brand.ORANGE)


def _feature_row(image: Image.Image, top: int, dark: bool = False) -> int:
    """The three things taught, spread across the full measure."""
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin = int(width * MARGIN_X)

    labels = [f.upper() for f in brand.FEATURES]
    size = int(height * 0.0150)
    font = brand.load_font("body_medium", size)
    tracking = height * 0.0012

    widths = [tracked_width(draw, l, font, tracking) for l in labels]
    span = width - margin * 2
    gap = (span - sum(widths)) / (len(labels) - 1)

    draw.rectangle([(margin, top), (width - margin, top + 2)],
                   fill=(58, 56, 62) if dark else (214, 216, 220))
    y = top + int(height * 0.022)
    x = margin
    for i, (label, w) in enumerate(zip(labels, widths)):
        draw_tracked(draw, (x, y), label, font,
                     brand.OFF_WHITE if dark else brand.NAVY, tracking)
        if i < len(labels) - 1:
            cx = x + w + gap / 2
            cy = y + size * 0.55
            draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)],
                         fill=brand.ORANGE)
        x += w + gap
    return y + int(size * 1.6)


def _layout_card(entry: dict, background: Path, out_path: Path,
                 canvas: str = "portrait", dark: bool = False) -> dict[str, Any]:
    """A creative with no photograph in it at all.

    `background` is accepted and ignored, so this layout is a drop-in for the
    others in the pipeline - a day pinned to a photograph still renders, it
    just does not use it.
    """
    size = brand.CANVAS.get(canvas)
    if not size:
        raise RenderError(f"Unknown canvas {canvas!r}")

    headline = (entry.get("headline") or "").strip()
    if not headline:
        raise RenderError(f"Entry {entry.get('id')} has no headline")
    accent = (entry.get("accent") or "").strip()

    width, height = size
    margin = int(width * MARGIN_X)
    footer_top = height - int(height * FOOTER_HEIGHT)

    image = _paper_ground(size, dark=dark)
    draw = ImageDraw.Draw(image)

    # Bare on paper, plated on near-black - the mark carries black lettering
    # and a dark ribbon, neither of which survives an unlit ground.
    logo = assets.fit_logo(int(width * 0.30), int(height * 0.100),
                           on_plate=dark)
    image.paste(logo, (margin, int(height * 0.052)), logo)
    _corner_tag(image, dark=dark)

    # Started at 0.215 and left a quarter of the canvas blank between the
    # offer and the feature row, which reads as a mistake rather than as
    # breathing room. Dropped so the space is shared above and below.
    y = int(height * 0.295)
    kicker_font = brand.load_font("display_alt", int(height * CARD_KICKER))
    rule_w = int(width * 0.055)
    draw.rectangle([(margin, y + int(height * 0.011)),
                    (margin + rule_w, y + int(height * 0.011) + 5)],
                   fill=brand.ORANGE)
    draw_tracked(draw, (margin + rule_w + int(width * 0.020), y),
                 brand.CATEGORY, kicker_font, brand.ORANGE,
                 height * CHIP_TRACKING)
    y += int(height * 0.055)

    text_width = width - margin * 2
    head_font, head_lines, line_height = None, [], 0
    words = headline.split()
    for cand in CARD_HEAD_SIZES:
        font = brand.load_font("display", cand)
        if any(draw.textlength(w, font=font) > text_width for w in words):
            continue
        lines = _wrap(draw, headline, font, text_width)
        if len(lines) <= CARD_HEAD_MAX_LINES:
            head_font, head_lines = font, lines
            line_height = int(cand * LINE_SPACING)
            break
    if not head_font:
        head_font = brand.load_font("display", CARD_HEAD_SIZES[-1])
        head_lines = _wrap(draw, headline, head_font,
                           text_width)[:CARD_HEAD_MAX_LINES]
        line_height = int(CARD_HEAD_SIZES[-1] * LINE_SPACING)

    for i, line in enumerate(head_lines):
        last = i == len(head_lines) - 1 and len(head_lines) > 1
        colour = brand.ORANGE if last else (brand.WHITE if dark else brand.NAVY)
        draw.text((margin, y), line, font=head_font, fill=colour)
        y += line_height

    y += int(height * 0.014)
    if accent:
        accent_font, accent_lines, accent_line_h = fit_accent(
            draw, accent, int(text_width * 0.72))
        for line in accent_lines:
            draw.text((margin, y), line, font=accent_font,
                      fill=brand.GREY if dark else brand.NAVY_SOFT)
            y += accent_line_h
        y += int(height * 0.014)
    else:
        accent_font, accent_lines = None, []

    y = _angled_offer(image, margin, y + int(height * 0.016))

    _feature_row(image, footer_top - int(height * 0.072), dark=dark)
    image = draw_footer(image, subline=f"Classes by {brand.MENTOR}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "JPEG", quality=92, optimize=True)

    return {
        "image_path": str(out_path),
        "background": Path(background).name,
        "canvas": canvas,
        "layout": "dark_card" if dark else "light_card",
        "strapline": brand.STRAPLINE,
        "headline_lines": head_lines,
        "headline_size": head_font.size,
        "accent_lines": accent_lines,
        "accent_size": accent_font.size if accent_font else None,
        # Nothing is measured here: every element sits on a flat brand colour,
        # so contrast is fixed by construction.
        "scrim": None,
        "text_seated": True,
    }


LAYOUTS["light_card"] = _layout_card
LAYOUTS["dark_card"] = lambda e, b, o, c="portrait": _layout_card(e, b, o, c,
                                                                 dark=True)
