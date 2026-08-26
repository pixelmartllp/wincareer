"""Check rendered creatives before they go anywhere near the Page.

Right now that means one thing the renderer cannot check itself: that the QR
still decodes to the WhatsApp number after downscaling and JPEG compression.
A QR that stops scanning is invisible in review - it looks perfectly fine.

The decode is done on the footer crop, not the whole frame. OpenCV's detector
regularly fails to *locate* a small QR inside a large busy image even when
the code is perfect, which produces false alarms; a phone is pointed at the
code, so the crop is both the more reliable test and the more representative
one.

    python tools/verify_creative.py output/2026-08-27
    python tools/verify_creative.py output/_proof/w001.jpg
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from win_social import brand, renderer  # noqa: E402
from win_social.config import force_utf8  # noqa: E402

# How much of the frame the footer crop takes, as fractions of the canvas.
CROP_LEFT = 0.66


def decode_qr(image: Image.Image) -> str:
    try:
        import cv2
    except ImportError:
        raise SystemExit(
            "opencv-python-headless is needed for QR verification:\n"
            "    .venv/Scripts/python.exe -m pip install opencv-python-headless")

    width, height = image.size
    bar_height = int(height * renderer.FOOTER_HEIGHT)
    strip = image.crop((int(width * CROP_LEFT), height - bar_height,
                        width, height))
    # Upscale first: the detector is markedly more reliable on a larger
    # target, and this costs nothing on a strip this size.
    strip = strip.resize((strip.width * 3, strip.height * 3), Image.LANCZOS)

    array = np.array(strip.convert("RGB"))[:, :, ::-1].copy()
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(array)
    return data or ""


def check(path: Path) -> tuple[bool, str]:
    image = Image.open(path).convert("RGB")

    expected = brand.CANVAS["portrait"]
    if image.size not in brand.CANVAS.values():
        return False, f"unexpected canvas {image.size}"

    data = decode_qr(image)
    if not data:
        return False, "QR did not decode"
    if data != brand.QR_TARGET:
        return False, f"QR points at {data!r}, expected {brand.QR_TARGET!r}"
    return True, f"QR -> {data}"


def main() -> int:
    force_utf8()
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    targets: list[Path] = []
    for argument in sys.argv[1:]:
        path = Path(argument)
        if path.is_dir():
            targets.extend(sorted(path.glob("*.jpg")))
        elif path.is_file():
            targets.append(path)
        else:
            print(f"!! not found: {path}")

    if not targets:
        raise SystemExit("Nothing to check.")

    failures = 0
    for path in targets:
        ok, detail = check(path)
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {path.name:28} {detail}")

    print(f"\n{len(targets) - failures}/{len(targets)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
