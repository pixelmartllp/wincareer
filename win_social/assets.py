"""Logo and background handling for Win Career Academy creatives.

The logo needs care. About a sixth of its ink - THE, ACADEMY and the
COMMUNICATION | CONFIDENCE | CAREER strapline - is near-black, and the WIN
wordmark is a dark blue. Dropped straight onto the brand navy, those parts
disappear. The Academy's own designer solves this by seating the logo on a
white rounded plate (see the Page's own posts), so that is what this module
does by default; a recoloured variant is available for the cases where a
plate would look heavy.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import brand

# Ink darker than this reads as "black" and is what vanishes on navy.
DARK_INK_LEVEL = 90

# How close to grey a dark pixel has to be before it counts as lettering
# rather than brand colour. The CAREER ribbon's dark red sits well outside
# this; the black in THE and ACADEMY sits well inside it.
NEUTRAL_INK_RANGE = 40

# How much of the plate is padding around the mark, as a fraction of the
# logo's own width. Matches the proportion on the Page's existing creatives.
PLATE_PAD = 0.10
PLATE_RADIUS = 0.06


class AssetError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------

@lru_cache(maxsize=4)
def load_logo() -> Image.Image:
    """The brand logo, trimmed of its transparent margin.

    The supplied file is 800x800 with the mark occupying roughly the middle
    660x610. Trimming first means every later size calculation is about the
    mark itself, not about invisible padding.
    """
    if not brand.LOGO_SOURCE.is_file():
        raise AssetError(
            f"Logo not found at {brand.LOGO_SOURCE}. Put the transparent PNG "
            f"there - it goes on every creative.")

    logo = Image.open(brand.LOGO_SOURCE).convert("RGBA")
    box = logo.split()[3].getbbox()
    if box:
        logo = logo.crop(box)
    return logo


@lru_cache(maxsize=4)
def load_logo_light_ink() -> Image.Image:
    """The logo with its near-black ink lifted to cream.

    Only the dark ink moves. The blue wordmark, the orange figure and the red
    ribbon are left exactly as they are, because those are the brand and
    recolouring them would produce a different logo.
    """
    logo = load_logo().copy()
    pixels = logo.load()
    width, height = logo.size
    cream = brand.WHITE

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0 or max(r, g, b) >= DARK_INK_LEVEL:
                continue
            # Only *neutral* dark ink is lifted. Judging on brightness alone
            # also caught the ribbon's dark red and purple, turning their
            # edges cream and dissolving the CAREER banner into mush. Black
            # lettering is near-grey; brand colour is not.
            if max(r, g, b) - min(r, g, b) >= NEUTRAL_INK_RANGE:
                continue
            pixels[x, y] = (*cream, a)
    return logo


def logo_on_plate(logo: Image.Image | None = None) -> Image.Image:
    """The logo seated on a white rounded plate.

    This is the Academy's own device and the reason it exists is legibility:
    the mark carries black text and dark blue, so on a photograph or on the
    brand navy it needs its own ground. A plate never has to be measured
    against what is behind it, which makes it the safe default.
    """
    logo = logo or load_logo()
    pad = int(logo.width * PLATE_PAD)
    size = (logo.width + pad * 2, logo.height + pad * 2)

    plate = Image.new("RGBA", size, (0, 0, 0, 0))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (size[0] - 1, size[1] - 1)],
        radius=int(min(size) * PLATE_RADIUS), fill=255)

    white = Image.new("RGBA", size, (*brand.WHITE, 255))
    plate.paste(white, (0, 0), mask)
    plate.paste(logo, (pad, pad), logo)
    return plate


def fit_logo(box_width: int, box_height: int, *, on_plate: bool = True,
             light_ink: bool = False) -> Image.Image:
    """Scale the logo to sit inside a box, keeping its aspect ratio.

    Sized by whichever side binds. The mark is wider than it is tall, but a
    short footer band binds on height - on the sibling Shashi pipeline a
    width-only calculation rendered the logo at 98px on a 1080 canvas and it
    effectively vanished. Both are checked here.
    """
    mark = load_logo_light_ink() if light_ink else load_logo()
    if on_plate:
        mark = logo_on_plate(mark)

    scale = min(box_width / mark.width, box_height / mark.height)
    size = (max(1, round(mark.width * scale)), max(1, round(mark.height * scale)))
    return mark.resize(size, Image.LANCZOS)


def logo_report() -> dict:
    """What the logo actually is, for check_setup."""
    if not brand.LOGO_SOURCE.is_file():
        return {"ready": False, "expected_at": str(brand.LOGO_SOURCE)}

    raw = Image.open(brand.LOGO_SOURCE).convert("RGBA")
    trimmed = load_logo()
    opaque = dark = 0
    for r, g, b, a in trimmed.getdata():
        if a > 128:
            opaque += 1
            if max(r, g, b) < DARK_INK_LEVEL:
                dark += 1
    return {
        "ready": True,
        "source": str(brand.LOGO_SOURCE),
        "raw_size": list(raw.size),
        "trimmed_size": list(trimmed.size),
        "has_transparency": raw.split()[3].getextrema()[0] < 255,
        "dark_ink_fraction": round(dark / opaque, 3) if opaque else 0.0,
        "note": ("Dark ink is why the logo is plated by default - THE, "
                 "ACADEMY and the strapline are black."),
    }


# --------------------------------------------------------------------------
# QR code
# --------------------------------------------------------------------------

@lru_cache(maxsize=2)
def load_qr() -> Image.Image:
    """The WhatsApp QR, kept whole.

    Not trimmed, unlike the logo. The transparent border around the modules
    is the quiet zone the spec requires, and a scanner needs it - cropping to
    the bounding box would produce a tidier image that phones refuse to read.
    """
    if not brand.QR_SOURCE.is_file():
        raise AssetError(
            f"QR not found at {brand.QR_SOURCE}. It goes on every creative.")
    return Image.open(brand.QR_SOURCE).convert("RGBA")


def fit_qr(side: int) -> Image.Image:
    """The QR at `side` pixels, on the white plate it cannot scan without.

    The supplied artwork is black modules on transparency. Dropped straight
    onto the brand navy, the "white" modules become navy and the contrast
    collapses to black-on-navy, which no scanner will read. The plate is not
    decoration.
    """
    qr = load_qr()
    plate = Image.new("RGBA", qr.size, (255, 255, 255, 255))
    plate.alpha_composite(qr)
    return plate.resize((side, side), Image.LANCZOS)


# --------------------------------------------------------------------------
# Backgrounds
# --------------------------------------------------------------------------

def list_backgrounds() -> list[Path]:
    """Every usable photograph in the background pool."""
    if not brand.BACKGROUND_DIR.is_dir():
        return []
    return sorted(p for p in brand.BACKGROUND_DIR.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))


@lru_cache(maxsize=1)
def known_themes() -> frozenset[str]:
    """The themes the content bank actually uses."""
    import json

    bank = brand.ROOT / "content_bank.json"
    if not bank.is_file():
        return frozenset()
    data = json.loads(bank.read_text(encoding="utf-8"))
    declared = data.get("_themes") or []
    return frozenset(declared) | {e.get("theme") for e in data.get("entries", [])
                                  if e.get("theme")}


def photo_theme(path: Path) -> str | None:
    """The theme a photo is reserved for, read from its filename.

    "confidence-01.jpg" belongs to the confidence theme only. Anything whose
    prefix is not a real content theme - "neutral-01.jpg", "office-04.jpg" -
    is theme-neutral and any post can use it.

    The prefix is checked against the bank rather than merely being present.
    A bare "-" test would read "neutral-01" as a theme called neutral, lock
    those photos to a theme no entry uses, and quietly starve the pool - the
    same failure that left the sibling pipeline generating fake backgrounds
    for months.
    """
    stem = path.stem
    if "-" not in stem:
        return None
    prefix = stem.rsplit("-", 1)[0]
    return prefix if prefix in known_themes() else None


HERO_DIR = "hero"


def list_hero() -> list[Path]:
    """Photographs composed for the dark hero layout.

    Kept apart from the main pool because that layout fades its left 46% into
    the ground: a photograph whose subject sits centre or left comes back with
    a face sliced in half. Everything here is framed subject-right with dark
    empty space on the left, which the general pool is not.
    """
    folder = brand.ASSETS / HERO_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))


def pick_hero(theme: str | None = None, exclude: list[str] | None = None,
              seed: int | None = None) -> Path | None:
    """A hero photograph for a theme, or None if the pool is empty."""
    import random

    photos = list_hero()
    if not photos:
        return None

    held = set(exclude or [])
    rng = random.Random(seed)
    themed = [p for p in photos if theme and photo_theme(p) == theme]
    for candidates in (themed, photos):
        fresh = [p for p in candidates if p.name not in held]
        if fresh:
            return rng.choice(fresh)
        if candidates:
            return rng.choice(candidates)
    return None


def pick_background(theme: str | None = None,
                    exclude: list[str] | None = None,
                    seed: int | None = None) -> Path:
    """Choose a photograph for a post.

    Order: photos locked to this theme, then theme-neutral ones. Recently
    used files are held back, but only while something is left - if the
    exclusion would empty the pool it is dropped rather than obeyed.

    That last clause is the whole point. The sibling pipeline let the
    exclusion window grow until every candidate was excluded, then fell back
    to a generated scene and said nothing. Repeating a photograph is a small
    problem; silently abandoning the photographs is a large one.
    """
    import random

    photos = list_backgrounds()
    if not photos:
        raise AssetError(
            f"No photographs in {brand.BACKGROUND_DIR}. Run "
            f"tools/fetch_photos.py, review the contact sheets, then "
            f"tools/install_photos.py --write.")

    held_back = set(exclude or [])
    rng = random.Random(seed)

    themed = [p for p in photos if theme and photo_theme(p) == theme]
    neutral = [p for p in photos if photo_theme(p) is None]

    for candidates in (themed, neutral, photos):
        fresh = [p for p in candidates if p.name not in held_back]
        if fresh:
            return rng.choice(fresh)
        if candidates:
            # Everything here has been used recently. Better a repeat than
            # no photograph.
            return rng.choice(candidates)

    raise AssetError("Background pool is empty after filtering.")


def pool_report() -> dict:
    """Coverage of the background pool, per theme."""
    photos = list_backgrounds()
    neutral = [p for p in photos if photo_theme(p) is None]
    by_theme: dict[str, list[str]] = {}
    for path in photos:
        theme = photo_theme(path)
        if theme:
            by_theme.setdefault(theme, []).append(path.name)
    return {
        "folder": str(brand.BACKGROUND_DIR),
        "total": len(photos),
        "theme_neutral": len(neutral),
        "by_theme": {k: len(v) for k, v in sorted(by_theme.items())},
        "files": [p.name for p in photos[:40]],
    }
