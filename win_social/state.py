"""Everything this pipeline has to remember between runs.

Two things live here, in one committed file:

  * the **ledger** - every publish attempt, successes and failures alike.
    Meta's own edges only show what is live now, so a failed post leaves no
    trace there. The ledger is what makes a bad day diagnosable.
  * the **rotation** - which quotes have been used and which photographs
    were used recently, so neither repeats until it has to.

state/state.json is committed on purpose. A cloud run has no other memory of
what it already posted; gitignoring it would make every run think it was the
first one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import STATE_FILE
from .meta_api import IST

# How many recently-used photographs to keep out of the running. Must stay
# comfortably below the pool size: on the sibling pipeline the window grew to
# the size of the pool, every candidate was excluded, and the renderer fell
# back to fake backgrounds without saying so.
RECENT_BACKGROUNDS = 10

EMPTY: dict[str, Any] = {
    "posts": [],
    "rotation": {"used_content": [], "recent_backgrounds": [], "cycles": 0},
    # What each day is supposed to post, pinned the first time it is decided.
    "plans": {},
}


def load() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return json.loads(json.dumps(EMPTY))
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # A truncated ledger must not stop the day from posting.
        return json.loads(json.dumps(EMPTY))
    data.setdefault("posts", [])
    data.setdefault("plans", {})
    rotation = data.setdefault("rotation", {})
    rotation.setdefault("used_content", [])
    rotation.setdefault("recent_backgrounds", [])
    rotation.setdefault("cycles", 0)
    return data


def save(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                          encoding="utf-8")


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

def record(platform: str, kind: str, ok: bool, **details: Any) -> dict[str, Any]:
    """Append one attempt - successful or not - and return the row."""
    now = datetime.now(tz=IST)
    row: dict[str, Any] = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M IST"),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "platform": platform,
        "kind": kind,
        "ok": ok,
    }
    row.update({k: v for k, v in details.items() if v is not None})

    data = load()
    data["posts"].append(row)
    save(data)
    return row


def history(limit: int = 20, only_failures: bool = False) -> list[dict[str, Any]]:
    posts = load()["posts"]
    if only_failures:
        posts = [p for p in posts if not p.get("ok")]
    return posts[-limit:][::-1]


def posts_on(date: str) -> list[dict[str, Any]]:
    return [p for p in load()["posts"] if p.get("date") == date]


def already_posted(date: str, platform: str, content_id: str) -> bool:
    """Has this exact creative already gone out on this platform today?

    The guard against a cutoff run double-posting what an earlier run
    already published.
    """
    return any(p.get("ok") and p.get("platform") == platform
               and p.get("content_id") == content_id
               and p.get("date") == date
               for p in load()["posts"])


def summary() -> dict[str, Any]:
    data = load()
    posts = data["posts"]
    days = sorted({p.get("date") for p in posts if p.get("date")})
    return {
        "total_attempts": len(posts),
        "successful": sum(1 for p in posts if p.get("ok")),
        "failed": sum(1 for p in posts if not p.get("ok")),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "content_used": len(data["rotation"]["used_content"]),
        "rotation_cycles": data["rotation"]["cycles"],
        "ledger": str(STATE_FILE),
    }


# --------------------------------------------------------------------------
# Rotation
# --------------------------------------------------------------------------

def used_content() -> list[str]:
    return list(load()["rotation"]["used_content"])


def recent_backgrounds() -> list[str]:
    return list(load()["rotation"]["recent_backgrounds"])


def mark_used(content_id: str, background: str) -> None:
    data = load()
    rotation = data["rotation"]
    if content_id and content_id not in rotation["used_content"]:
        rotation["used_content"].append(content_id)
    if background:
        recent = [b for b in rotation["recent_backgrounds"] if b != background]
        recent.append(background)
        rotation["recent_backgrounds"] = recent[-RECENT_BACKGROUNDS:]
    save(data)


# --------------------------------------------------------------------------
# Day plans
# --------------------------------------------------------------------------

def plan_for(date: str) -> list[dict[str, str]]:
    """What this date is supposed to post, if it has already been decided."""
    return list(load()["plans"].get(date, []))


def set_plan(date: str, items: list[dict[str, str]]) -> None:
    """Pin a date to a specific creative, once.

    output/ is gitignored, so a cloud runner starts every run with no
    creatives on disk and regenerates from scratch. Without a pinned plan
    each run picks a *different* quote, and because the duplicate guard keys
    on the content id, a second run would happily post a second, different
    creative the same day. The first live run hit exactly that setup: one
    run posted w036, the next generated w008.

    Pinning makes regeneration reproduce the same creative, so a later run
    can retry the platform that failed instead of inventing a new post.
    """
    data = load()
    data["plans"][date] = items
    # Keep this from growing forever; a fortnight is far more history than
    # any retry needs.
    for old in sorted(data["plans"])[:-14]:
        del data["plans"][old]
    save(data)


def reset_content_cycle() -> int:
    """Start the quote rotation over, counting the completed cycle.

    Called when the bank runs out. Resetting rather than hard-failing is
    deliberate: a day that cannot find an unused quote should still post
    something rather than go silent.
    """
    data = load()
    data["rotation"]["used_content"] = []
    data["rotation"]["cycles"] += 1
    save(data)
    return data["rotation"]["cycles"]
