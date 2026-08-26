"""Install reviewed candidates into the live background pool.

Reads assets/_staging/selected.txt - an explicit accept list, not a reject
list. Default-deny is the point: a photo nobody looked at never reaches the
pool, so a bad frame cannot arrive by being overlooked.

Crops to the canvas the same way tools/contact_sheet.py previewed it, so what
was approved is what gets installed, and writes SOURCES.md with the
photographer credit for every file.

    python tools/install_photos.py            # dry run
    python tools/install_photos.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from win_social import brand  # noqa: E402
from win_social.config import ROOT, force_utf8  # noqa: E402

STAGING = ROOT / "assets" / "_staging"
MANIFEST = STAGING / "candidates.json"
SELECTED = STAGING / "selected.txt"
POOL = brand.BACKGROUND_DIR
SOURCES = POOL / "SOURCES.md"

CANVAS = brand.CANVAS["portrait"]
JPEG_QUALITY = 88


def read_selection() -> list[tuple[str, str]]:
    if not SELECTED.is_file():
        raise SystemExit(f"No accept list at {SELECTED}")
    chosen = []
    for line in SELECTED.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        chosen.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return chosen


def crop_to_canvas(image: Image.Image) -> Image.Image:
    """Identical to the contact sheet's crop - approve what you install."""
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
    return image.resize(CANVAS, Image.LANCZOS)


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="actually install; otherwise this is a dry run")
    args = parser.parse_args()

    if not MANIFEST.is_file():
        raise SystemExit("No candidates staged. Run tools/fetch_photos.py.")
    by_id = {c["id"]: c for c in json.loads(
        MANIFEST.read_text(encoding="utf-8"))}

    chosen = read_selection()
    print(f"{len(chosen)} selected\n")

    counters: dict[str, int] = {}
    planned: list[tuple[str, str, dict]] = []
    missing: list[str] = []

    for pid, theme in chosen:
        candidate = by_id.get(pid)
        if not candidate or not (STAGING / candidate["file"]).is_file():
            missing.append(pid)
            continue
        prefix = theme or "neutral"
        counters[prefix] = counters.get(prefix, 0) + 1
        planned.append((pid, f"{prefix}-{counters[prefix]:02d}.jpg", candidate))

    if missing:
        print(f"!! not staged, skipped: {', '.join(missing)}\n")

    neutral = sum(1 for _, name, _ in planned if name.startswith("neutral-"))
    for pid, name, candidate in planned:
        print(f"  {pid:>10}  ->  {name:24} {candidate.get('photographer')}")
    print(f"\n{len(planned)} to install | {neutral} theme-neutral "
          f"({100 * neutral // max(1, len(planned))}%)")

    if not args.write:
        print("\nDry run. Add --write to install.")
        return 0

    POOL.mkdir(parents=True, exist_ok=True)
    rows = []
    for pid, name, candidate in planned:
        source = STAGING / candidate["file"]
        image = crop_to_canvas(Image.open(source).convert("RGB"))
        image.save(POOL / name, "JPEG", quality=JPEG_QUALITY, optimize=True)
        rows.append((name, candidate))
        print(f"  wrote {name}")

    lines = [
        "# Background pool sources",
        "",
        f"{len(rows)} photographs, all from Pexels under the Pexels License:",
        "free for commercial use, no attribution required. Credit is recorded",
        "here anyway so any file can be traced back to its original.",
        "",
        "Note on the licence: photos of identifiable people must not be used",
        "in a way that implies those people endorse the Academy. These are",
        "backgrounds behind a line of text, which is fine - do not caption one",
        "as a student, a trainer or a testimonial.",
        "",
        "| file | photographer | source | search |",
        "| --- | --- | --- | --- |",
    ]
    for name, candidate in rows:
        lines.append(
            f"| {name} | {candidate.get('photographer')} | "
            f"{candidate.get('pexels_url')} | {candidate.get('query')} |")
    lines.append("")
    lines.append("Topping the pool up: tools/fetch_photos.py, then")
    lines.append("tools/contact_sheet.py, then LOOK at the sheets, then add")
    lines.append("ids to assets/_staging/selected.txt and run this script.")
    SOURCES.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {SOURCES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
