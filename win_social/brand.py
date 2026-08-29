"""Brand system for Win Career Academy - Communication | Confidence | Career.

The palette is not invented. It was sampled from the Page's own creatives
(12 posts, June-July 2026) by bucketing every pixel and reading off the
dominant and the most saturated families. The navy, the orange and the ribbon
red below are the colours the brand is already using in public.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ASSETS = ROOT / "assets"
FONT_DIR = ASSETS / "fonts"
BACKGROUND_DIR = ASSETS / "backgrounds"
OUTPUT_DIR = ROOT / "output"
STATE_DIR = ROOT / "state"

LOGO_SOURCE = ASSETS / "logo.png"
LOGO_TRANSPARENT = ASSETS / "logo_transparent.png"
QR_SOURCE = ASSETS / "qr.png"

WINDOWS_FONTS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"


# --------------------------------------------------------------------------
# Palette - sampled from the Page's own creatives
# --------------------------------------------------------------------------

NAVY = (12, 36, 60)          # #0c243c - the panel colour on most posts
NAVY_DEEP = (10, 20, 40)     # #0a1428 - darker end, for scrims
NAVY_SOFT = (36, 60, 84)     # #243c54
BLUE = (12, 60, 156)         # #0c3c9c - the logo's blue
BLUE_LIGHT = (132, 180, 228) # #84b4e4

ORANGE = (252, 84, 12)       # #fc540c - the signature accent
ORANGE_LIGHT = (255, 146, 74)
ORANGE_PALE = (255, 214, 190)

RED = (204, 12, 36)          # #cc0c24 - the logo ribbon

WHITE = (252, 252, 252)
PAPER = (242, 243, 245)      # the light ground of the split layout
PAPER_WARM = (248, 248, 250)
OFF_WHITE = (228, 228, 228)
GREY = (180, 180, 180)
INK = (12, 12, 18)


# --------------------------------------------------------------------------
# Canvas presets
# --------------------------------------------------------------------------

CANVAS = {
    "portrait": (1080, 1350),   # what the Page already posts
    "square": (1080, 1080),
    "story": (1080, 1920),
}

DEFAULT_CANVAS = "portrait"


# --------------------------------------------------------------------------
# Font resolution
#
# The Page's headlines are heavy condensed caps (Anton / Oswald family).
# Drop those into assets/fonts/ and they are picked up automatically;
# otherwise we fall back to fonts that ship with Windows, which is why both
# workflows pin windows-latest.
# --------------------------------------------------------------------------

_FONT_STACKS: dict[str, list[str]] = {
    "display": [
        "Anton-Regular.ttf",
        "BebasNeue-Regular.ttf",
        "Oswald-Regular.ttf",
        "impact.ttf",
        "arialbd.ttf",
    ],
    "display_alt": [
        "Oswald-Regular.ttf",
        "Montserrat-Regular.ttf",
        "segoeuib.ttf",
        "arialbd.ttf",
    ],
    "body": [
        "Montserrat-Regular.ttf",
        "Lato-Regular.ttf",
        "segoeui.ttf",
        "calibri.ttf",
        "arial.ttf",
    ],
    "body_medium": [
        "Montserrat-Regular.ttf",
        "Lato-Bold.ttf",
        "segoeuib.ttf",
        "arialbd.ttf",
    ],
}

# Oswald and Montserrat ship from Google as *variable* fonts - one file that
# carries every weight. Asking for a role's heavier cut means selecting a
# named instance inside the file; a static bold does not exist to fall back
# on. Roles that want weight say so here.
_ROLE_WEIGHT = {
    "display_alt": "Bold",
    "body_medium": "SemiBold",
}


def font_path(role: str) -> str:
    """An absolute path to the best available font for a role."""
    candidates = _FONT_STACKS.get(role)
    if not candidates:
        raise KeyError(f"Unknown font role: {role!r}")

    for name in candidates:
        local = FONT_DIR / name
        if local.is_file():
            return str(local)

    for name in candidates:
        system = WINDOWS_FONTS / name
        if system.is_file():
            return str(system)

    # Last resort - anything at all, so rendering never hard-fails.
    for fallback in (WINDOWS_FONTS / "arialbd.ttf", WINDOWS_FONTS / "arial.ttf",
                     WINDOWS_FONTS / "segoeui.ttf"):
        if fallback.is_file():
            return str(fallback)

    raise FileNotFoundError(
        f"No font found for role {role!r}. Drop a .ttf into {FONT_DIR}")


def load_font(role: str, size: int):
    """A PIL font for a role, at the right weight.

    Variable fonts open at their default instance, which for Oswald and
    Montserrat is Regular. A headline asking for Bold would silently render
    Regular, so the named instance is selected explicitly where the role
    wants one.
    """
    from PIL import ImageFont

    font = ImageFont.truetype(font_path(role), size)
    weight = _ROLE_WEIGHT.get(role)
    if weight:
        try:
            font.set_variation_by_name(weight)
        except (OSError, AttributeError):
            # A static font, or one without that instance - the stack's own
            # fallback is already a bold face, so this is not worth failing.
            pass
    return font


def font_report() -> dict[str, str]:
    """Which concrete font file each role currently resolves to."""
    report = {}
    for role in _FONT_STACKS:
        path = font_path(role)
        weight = _ROLE_WEIGHT.get(role)
        report[role] = f"{path} [{weight}]" if weight else path
    return report


# --------------------------------------------------------------------------
# Brand copy that appears on every creative and in every caption
# --------------------------------------------------------------------------

BRAND_NAME = "Win Career Academy"
BRAND_TAGLINE = "Communication | Confidence | Career"
BRAND_HANDLE = "@thewincareer"
PAGE_URL = "https://www.facebook.com/TheWinCareer"

MENTOR = "Mandeepa Garg"
PHONE = "8837888293"
PHONE_DISPLAY = "8837 888 293"
CALL_LINE = f"Call : {PHONE_DISPLAY}"
CTA_LINE = "Book Your FREE Demo Class"

# The standing line, on every creative above the headline. EMPHASIS is the
# tail rendered in orange - it must be a literal suffix of STRAPLINE, which
# is asserted at import rather than left to go quietly wrong if either is
# edited alone.
STRAPLINE = "Upgrade your English, Upgrade your CAREER!"
STRAPLINE_EMPHASIS = "CAREER!"

# The category chip. A corporate reader scans for what this is before they
# read what it promises.
CATEGORY = "LEARN ENGLISH SPEAKING"

# The offer, in the words the owner uses for it. This is the one thing the
# creative asks anyone to do, so it gets its own object on the canvas rather
# than a line of small print.
OFFER = "FREE DEMO CLASSES"

# The three things the Academy actually teaches, for the feature card. Kept
# to three: the Page's own flyers run five and the row turns into a list
# nobody reads.
FEATURES = ("Spoken English", "Interview Skills", "Public Speaking")
SCAN_LINE = "Scan to WhatsApp"
COURSE_NOTE = "Only 2 students per batch"

# The QR resolves here. Verified by decoding assets/qr.png rather than taken
# on trust - a wrong QR would ship on every creative before anyone noticed.
QR_TARGET = "https://wa.me/91" + PHONE


assert STRAPLINE.endswith(STRAPLINE_EMPHASIS), (
    "STRAPLINE_EMPHASIS must be the tail of STRAPLINE, otherwise the "
    "renderer colours the wrong words.")


def ensure_dirs() -> None:
    """Create the directory layout the pipeline expects."""
    for directory in (ASSETS, FONT_DIR, BACKGROUND_DIR, OUTPUT_DIR, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
