"""Choosing what to post, and writing the caption that goes with it.

The bank is a flat list of entries. Selection walks it without repeating
until it has to, and captions are assembled here rather than stored per
entry so the CTA, the phone number and the mentor's name can be changed in
one place instead of seventy-two.
"""

from __future__ import annotations

import json
import random
from typing import Any

from . import brand, state

BANK_FILE = brand.ROOT / "content_bank.json"

# Instagram allows 30; the Page's own posts run about a dozen. Facebook
# treats a wall of tags as spam, so it gets a short set.
IG_TAG_LIMIT = 16
FB_TAG_LIMIT = 5

CORE_TAGS = ("WinCareerAcademy", "SpokenEnglish", "BusinessEnglish",
             "CommunicationSkills", "CareerGrowth")

THEME_TAGS: dict[str, tuple[str, ...]] = {
    "spoken_english": ("EnglishSpeaking", "FluentEnglish", "LearnEnglish"),
    "confidence": ("ConfidenceBuilding", "SelfConfidence", "SpeakWithConfidence"),
    "public_speaking": ("PublicSpeaking", "PresentationSkills", "StagePresence"),
    "interview": ("InterviewSkills", "InterviewPreparation", "JobInterview"),
    "career_growth": ("CareerDevelopment", "CareerUpgrade", "GetPromoted"),
    "communication": ("EffectiveCommunication", "CorporateCommunication",
                      "WorkplaceCommunication"),
    "personality": ("PersonalityDevelopment", "SoftSkills", "ProfessionalGrowth"),
    "leadership": ("LeadershipSkills", "LeadTheRoom", "ManagerSkills"),
    "mindset": ("GrowthMindset", "SelfImprovement", "LearnEveryday"),
    "practice": ("DailyPractice", "EnglishPractice", "SkillBuilding"),
}

AUDIENCE_TAGS = ("WorkingProfessionals", "CorporateTraining", "EnglishForWork")


class ContentBankError(RuntimeError):
    pass


def load_bank() -> dict[str, Any]:
    if not BANK_FILE.is_file():
        raise ContentBankError(f"Content bank not found at {BANK_FILE}")
    try:
        bank = json.loads(BANK_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContentBankError(f"content_bank.json is not valid JSON: {exc}") from exc

    entries = bank.get("entries")
    if not entries:
        raise ContentBankError("content_bank.json has no entries.")

    seen: set[str] = set()
    for entry in entries:
        for field in ("id", "theme", "headline"):
            if not entry.get(field):
                raise ContentBankError(
                    f"Entry {entry.get('id', '?')} is missing {field!r}")
        if entry["id"] in seen:
            raise ContentBankError(f"Duplicate content id: {entry['id']}")
        seen.add(entry["id"])
    return bank


def bank_stats() -> dict[str, Any]:
    bank = load_bank()
    entries = bank["entries"]
    used = set(state.used_content())
    themes: dict[str, int] = {}
    for entry in entries:
        themes[entry["theme"]] = themes.get(entry["theme"], 0) + 1
    return {
        "file": str(BANK_FILE),
        "total": len(entries),
        "used": len([e for e in entries if e["id"] in used]),
        "remaining": len([e for e in entries if e["id"] not in used]),
        "themes": dict(sorted(themes.items())),
        "cycles_completed": state.load()["rotation"]["cycles"],
    }


def select(count: int = 1, theme: str | None = None,
           seed: int | None = None) -> list[dict[str, Any]]:
    """Pick the next `count` entries, avoiding anything already used.

    When the bank runs dry the rotation resets rather than raising. A day
    that cannot find an unused quote should still post something - going
    silent is a worse failure than repeating after seventy-two days.
    """
    bank = load_bank()
    entries = bank["entries"]
    if theme:
        entries = [e for e in entries if e["theme"] == theme]
        if not entries:
            raise ContentBankError(f"No entries for theme {theme!r}")

    rng = random.Random(seed)
    chosen: list[dict[str, Any]] = []
    taken: set[str] = set()

    for _ in range(count):
        used = set(state.used_content()) | taken
        pool = [e for e in entries if e["id"] not in used]
        if not pool:
            state.reset_content_cycle()
            pool = [e for e in entries if e["id"] not in taken]
        if not pool:
            raise ContentBankError("Nothing left to select, even after reset.")
        entry = rng.choice(pool)
        chosen.append(entry)
        taken.add(entry["id"])
    return chosen


# --------------------------------------------------------------------------
# Captions
# --------------------------------------------------------------------------

def hashtags(theme: str, limit: int) -> list[str]:
    tags: list[str] = []
    for tag in (*CORE_TAGS, *THEME_TAGS.get(theme, ()), *AUDIENCE_TAGS):
        if tag not in tags:
            tags.append(tag)
    return [f"#{t}" for t in tags[:limit]]


def build_caption(entry: dict[str, Any], platform: str) -> str:
    """Assemble the post caption.

    The body comes from the bank; everything after it - the strapline, the
    call to action, the number, the mentor - is assembled here so changing
    the phone number is one edit, not seventy-two.
    """
    limit = IG_TAG_LIMIT if platform == "instagram" else FB_TAG_LIMIT

    parts = [entry.get("caption", "").strip()]
    parts.append("")
    parts.append(brand.STRAPLINE)
    parts.append("")
    parts.append(f"{brand.CTA_LINE} - {brand.COURSE_NOTE.lower()}, so every "
                 f"session is yours.")
    parts.append(f"Call or WhatsApp: {brand.PHONE_DISPLAY}")
    parts.append(f"Classes by {brand.MENTOR}")
    parts.append("")
    parts.append(" ".join(hashtags(entry["theme"], limit)))

    return "\n".join(p for p in parts if p is not None).strip()
