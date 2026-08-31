"""Pull background candidates from Pexels into a staging folder.

This lives in the repo on purpose. The equivalent script for the sibling
Shashi pipeline was written in a scratch folder, was lost, and had to be
rewritten from scratch the next time the pool needed topping up.

Nothing here touches assets/backgrounds/. Candidates land in
assets/_staging/, get looked at through tools/contact_sheet.py, and only
what survives that review is installed by tools/install_photos.py.

    python tools/fetch_photos.py                 # every query, default count
    python tools/fetch_photos.py --per-query 12
    python tools/fetch_photos.py --query "job interview" --per-query 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from win_social.config import ROOT, force_utf8, load_config  # noqa: E402

STAGING = ROOT / "assets" / "_staging"
MANIFEST = STAGING / "candidates.json"

SEARCH_URL = "https://api.pexels.com/v1/search"

# The canvas is 1080x1350, so a photo has to be at least that on both sides
# to survive the crop without being upscaled. Pexels' "original" is usually
# far larger; this only rejects the genuinely small ones.
MIN_WIDTH = 1080
MIN_HEIGHT = 1350

# What the brand is actually about. Each query is also the theme prefix a
# surviving photo gets, so these names match content_bank.json themes where
# they can.
QUERIES: dict[str, str] = {
    # India-first. The Academy's own creatives use Indian models, and a daily
    # feed of European faces would read as bought-in stock on an Indian
    # brand's page. The first sweep proved the point: of 77 results only the
    # one explicitly Indian query returned Indian people.
    "indian business professional": "",
    "indian woman office": "",
    "indian man office": "",
    "indian office meeting": "communication",
    "indian corporate team": "communication",
    "indian teacher classroom": "practice",
    "indian student studying": "practice",
    "indian woman entrepreneur": "confidence",
    "indian professional portrait": "confidence",
    "india business people": "career_growth",

    # A few deliberately face-free frames. They always read correctly, they
    # never date, and they are the safe fallback when no person-photo suits
    # the line.
    "empty conference room": "",
    "auditorium seats empty": "public_speaking",
    "microphone stage light": "public_speaking",
    "notebook pen desk": "practice",
}


def api_key() -> str:
    key = load_config().get("pexels_api_key")
    if not key:
        raise SystemExit(
            "No pexels_api_key in config.json (or PEXELS_API_KEY in the "
            "environment). Get one free at https://www.pexels.com/api/")
    return str(key)


def search(key: str, query: str, per_page: int) -> list[dict]:
    try:
        response = requests.get(
            SEARCH_URL, timeout=60,
            headers={"Authorization": key},
            params={"query": query, "orientation": "portrait",
                    "size": "large", "per_page": per_page})
    except requests.RequestException as exc:
        print(f"  ! network error: {exc}")
        return []

    if response.status_code == 401:
        raise SystemExit("Pexels rejected the API key (401). Check config.json.")
    if response.status_code == 429:
        print("  ! rate limited (429) - waiting 60s")
        time.sleep(60)
        return []
    if not response.ok:
        print(f"  ! HTTP {response.status_code}")
        return []

    return response.json().get("photos", [])


def download(url: str, dest: Path) -> bool:
    try:
        response = requests.get(url, timeout=120)
    except requests.RequestException as exc:
        print(f"  ! download failed: {exc}")
        return False
    if not response.ok or not response.content.startswith(b"\xff\xd8"):
        print(f"  ! not a JPEG (HTTP {response.status_code})")
        return False
    dest.write_bytes(response.content)
    return True


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-query", type=int, default=8)
    parser.add_argument("--query", default="",
                        help="run one ad-hoc query instead of the built-in set")
    parser.add_argument("--theme", default="",
                        help="theme prefix for an ad-hoc query")
    args = parser.parse_args()

    key = api_key()
    STAGING.mkdir(parents=True, exist_ok=True)

    queries = ({args.query: args.theme} if args.query else QUERIES)

    existing: dict[str, dict] = {}
    if MANIFEST.is_file():
        existing = {c["id"]: c for c in json.loads(
            MANIFEST.read_text(encoding="utf-8"))}

    kept = 0
    for query, theme in queries.items():
        print(f"\n{query!r}  (theme: {theme or 'neutral'})")
        photos = search(key, query, args.per_query)
        print(f"  {len(photos)} results")

        for photo in photos:
            pid = str(photo.get("id"))
            # Known *and* still on disk. Checking the manifest alone would
            # skip anything whose file had been cleared out, leaving an entry
            # that can never be re-downloaded and an install that fails on a
            # missing file.
            if pid in existing and (STAGING / existing[pid]["file"]).is_file():
                continue
            width, height = photo.get("width", 0), photo.get("height", 0)
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                print(f"  - {pid}: too small ({width}x{height})")
                continue

            url = (photo.get("src") or {}).get("large2x") or \
                  (photo.get("src") or {}).get("original")
            if not url:
                continue

            dest = STAGING / f"{pid}.jpg"
            if not download(url, dest):
                continue

            existing[pid] = {
                "id": pid,
                "file": dest.name,
                "query": query,
                "theme": theme,
                "width": width,
                "height": height,
                "photographer": photo.get("photographer"),
                "photographer_url": photo.get("photographer_url"),
                "pexels_url": photo.get("url"),
                "alt": photo.get("alt"),
            }
            kept += 1
            print(f"  + {pid}  {width}x{height}  {photo.get('photographer')}")

        time.sleep(1)   # stay well inside 200 requests/hour

    MANIFEST.write_text(
        json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
        encoding="utf-8")

    print(f"\nnew this run: {kept}")
    print(f"staged total: {len(existing)}")
    print(f"folder:       {STAGING}")
    print("\nNext: python tools/contact_sheet.py  - and LOOK at it before "
          "installing anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
