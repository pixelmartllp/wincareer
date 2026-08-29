"""Generating a day's creatives and publishing them.

    content_bank.json ─┐
                       ├─> generate_day() ─> output/<date>/NN-<id>.jpg
    assets/backgrounds ┘                     + batch.json (captions, status)
                                                    │
                                             publish_day() ─> Facebook, Instagram
                                                    │
                                             state/state.json (ledger)

Generation never posts. Publishing never renders. Keeping them apart is what
lets a day be regenerated after a bad batch without any risk of it going out,
and lets a failed post be retried without re-rendering.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import assets, brand, content, meta_api, renderer, state
from .config import ConfigError
from .meta_api import IST, GraphClient, MetaAPIError

PLATFORMS = ("facebook", "instagram")

# When the audience should see the post, in IST. This is not when the run
# happens - GitHub's scheduler has fired these crons up to fifteen hours
# late, which put one day's creative on the Page at 00:26 and the next at
# 20:02. Facebook can be told to publish at a fixed time, so the runner's
# arrival time stops mattering.
POST_HOUR, POST_MINUTE = 9, 0

# Instagram has no scheduling at all - its Content Publishing API only
# publishes now. So it is simply not allowed to post outside daylight; a run
# that arrives at 2am defers, and the next one picks it up.
IG_WINDOW = (7, 21)


def preferred_slot(now: datetime | None = None) -> int | None:
    """Unix time for today's posting slot, or None to publish immediately.

    Returns None once the slot has passed, rather than pushing to tomorrow -
    tomorrow belongs to tomorrow's creative, and a late post today is far
    better than two posts colliding on one day.
    """
    now = now or datetime.now(tz=IST)
    slot = now.replace(hour=POST_HOUR, minute=POST_MINUTE, second=0,
                       microsecond=0)
    if slot - now >= meta_api.SCHEDULE_MIN:
        return int(slot.timestamp())
    return None


def instagram_is_awake(now: datetime | None = None) -> bool:
    now = now or datetime.now(tz=IST)
    return IG_WINDOW[0] <= now.hour < IG_WINDOW[1]


def today() -> str:
    return datetime.now(tz=IST).strftime("%Y-%m-%d")


def batch_dir(day: str) -> Path:
    return brand.OUTPUT_DIR / day


def batch_file(day: str) -> Path:
    return batch_dir(day) / "batch.json"


def list_batches() -> list[str]:
    if not brand.OUTPUT_DIR.is_dir():
        return []
    return sorted(p.name for p in brand.OUTPUT_DIR.iterdir()
                  if p.is_dir() and (p / "batch.json").is_file())


def load_batch(day: str) -> dict[str, Any]:
    path = batch_file(day)
    if not path.is_file():
        raise FileNotFoundError(
            f"No batch for {day}. Generate one first. "
            f"Available: {list_batches()[-8:] or 'none'}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_batch(batch: dict[str, Any]) -> None:
    path = batch_file(batch["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def find_item(batch: dict[str, Any], index: int) -> dict[str, Any]:
    for item in batch["items"]:
        if item["index"] == index:
            return item
    raise ValueError(f"No item {index} in the {batch['date']} batch "
                     f"(1..{len(batch['items'])}).")


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def generate_day(count: int = 1, day: str | None = None,
                 canvas: str = "portrait", theme: str | None = None,
                 layout: str | None = None,
                 overwrite: bool = False) -> dict[str, Any]:
    """Render a day's creatives and write batch.json. Posts nothing."""
    day = day or today()
    brand.ensure_dirs()

    if batch_file(day).is_file() and not overwrite:
        raise FileExistsError(
            f"A batch for {day} already exists. Pass overwrite to replace it.")

    layout = layout or renderer.DEFAULT_LAYOUT

    # A day is decided once. Every later run for the same date rebuilds the
    # identical creative rather than choosing again - see state.set_plan for
    # why that matters in the cloud, where output/ never survives a run.
    plan = state.plan_for(day)
    if plan and len(plan) == count and not theme:
        bank = {e["id"]: e for e in content.load_bank()["entries"]}
        entries = [bank[p["content_id"]] for p in plan
                   if p["content_id"] in bank]
        pinned = [p["background"] for p in plan]
        if len(entries) != count:            # bank edited since; re-decide
            entries, pinned = content.select(count, theme=theme), []
    else:
        entries, pinned = content.select(count, theme=theme), []

    items: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        if pinned:
            background = brand.BACKGROUND_DIR / pinned[index - 1]
            if not background.is_file():
                background = assets.pick_background(
                    theme=entry["theme"], exclude=state.recent_backgrounds())
        else:
            background = assets.pick_background(
                theme=entry["theme"], exclude=state.recent_backgrounds())
        out_path = batch_dir(day) / f"{index:02d}-{entry['id']}.jpg"

        meta = renderer.render(entry, background, out_path,
                               canvas=canvas, layout=layout)

        item = {
            "index": index,
            "content_id": entry["id"],
            "theme": entry["theme"],
            "headline": entry["headline"],
            "accent": entry.get("accent"),
            "caption_facebook": content.build_caption(entry, "facebook"),
            "caption_instagram": content.build_caption(entry, "instagram"),
            "status": {platform: "pending" for platform in PLATFORMS},
            **meta,
        }
        items.append(item)
        if not pinned:
            state.mark_used(entry["id"], Path(meta["background"]).name)

    if not pinned:
        state.set_plan(day, [{"content_id": i["content_id"],
                              "background": i["background"]} for i in items])

    batch = {
        "date": day,
        "generated_at": datetime.now(tz=IST).isoformat(),
        "count": len(items),
        "canvas": canvas,
        "layout": layout,
        "items": items,
    }
    save_batch(batch)
    return batch


def regenerate_item(day: str, index: int, layout: str | None = None,
                    background: str | None = None) -> dict[str, Any]:
    """Re-render one creative, optionally with a different layout or photo."""
    batch = load_batch(day)
    item = find_item(batch, index)

    entry = {
        "id": item["content_id"],
        "theme": item["theme"],
        "headline": item["headline"],
        "accent": item.get("accent"),
    }
    if background:
        photo = brand.BACKGROUND_DIR / background
        if not photo.is_file():
            raise ValueError(f"No such background: {background}")
    else:
        photo = assets.pick_background(
            theme=item["theme"],
            exclude=state.recent_backgrounds() + [item["background"]])

    meta = renderer.render(entry, photo, Path(item["image_path"]),
                           canvas=batch.get("canvas", "portrait"),
                           layout=layout or batch.get("layout",
                                                      renderer.DEFAULT_LAYOUT))
    item.update(meta)
    save_batch(batch)
    return item


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

def publish_item(day: str, index: int, platforms: tuple[str, ...] = PLATFORMS,
                 dry_run: bool = True, force: bool = False,
                 immediate: bool = False) -> dict[str, Any]:
    """Publish one creative. Dry run unless told otherwise.

    `immediate` skips both timing rules: no Facebook scheduling, no Instagram
    window. It is for a person posting by hand, who has already decided that
    now is the right moment.
    """
    batch = load_batch(day)
    item = find_item(batch, index)
    image = Path(item["image_path"])
    if not image.is_file():
        raise FileNotFoundError(f"Creative missing on disk: {image}")

    slot = None if immediate else preferred_slot()
    ig_awake = immediate or instagram_is_awake()

    plan: dict[str, Any] = {
        "date": day, "index": index, "content_id": item["content_id"],
        "image": str(image), "platforms": list(platforms),
        "dry_run": dry_run, "results": {},
        "facebook_publishes": (meta_api.describe_time(slot) if slot
                               else "immediately"),
        "instagram_window_open": ig_awake,
    }

    if dry_run:
        plan["captions"] = {p: item[f"caption_{p}"] for p in platforms}
        return plan

    client = GraphClient()
    for platform in platforms:
        if not force and state.already_posted(day, platform, item["content_id"]):
            plan["results"][platform] = {"skipped": "already posted today"}
            continue

        if platform == "instagram" and not ig_awake:
            # Deferred, not failed. No ledger row: nothing was attempted, and
            # a failure row here would both mislead the next reader and turn
            # a correctly-behaving run red.
            item["status"][platform] = "deferred"
            plan["results"][platform] = {
                "deferred": f"outside the {IG_WINDOW[0]:02d}:00-"
                            f"{IG_WINDOW[1]:02d}:00 IST window"}
            continue

        caption = item[f"caption_{platform}"]
        try:
            if platform == "facebook":
                response = client.post_photo(image, caption,
                                             schedule_unix=slot)
                post_id = response.get("post_id")
                permalink = response.get("permalink")
            else:
                response = client.post_instagram_photo(caption,
                                                       image_path=image)
                post_id = response.get("media_id")
                permalink = response.get("permalink")

            scheduled_for = response.get("scheduled_for")
            item["status"][platform] = "scheduled" if scheduled_for else "posted"
            plan["results"][platform] = {"ok": True, "post_id": post_id,
                                         "permalink": permalink,
                                         "scheduled_for": scheduled_for}
            state.record(platform, "creative", ok=True,
                         content_id=item["content_id"], post_id=post_id,
                         permalink=permalink, scheduled_for=scheduled_for,
                         image=image.name)
        except (MetaAPIError, ConfigError, ValueError) as exc:
            item["status"][platform] = "failed"
            plan["results"][platform] = {"ok": False, "error": str(exc)}
            state.record(platform, "creative", ok=False,
                         content_id=item["content_id"], error=str(exc),
                         image=image.name)

    save_batch(batch)
    return plan


def publish_day(day: str | None = None, platforms: tuple[str, ...] = PLATFORMS,
                dry_run: bool = True, force: bool = False,
                stop_on_error: bool = False,
                immediate: bool = False) -> dict[str, Any]:
    """Publish a whole day's batch."""
    day = day or today()
    batch = load_batch(day)

    summary: dict[str, Any] = {"date": day, "dry_run": dry_run,
                               "platforms": list(platforms), "items": []}
    for item in batch["items"]:
        result = publish_item(day, item["index"], platforms=platforms,
                              dry_run=dry_run, force=force,
                              immediate=immediate)
        summary["items"].append(result)
        if stop_on_error and any(
                r.get("ok") is False for r in result["results"].values()):
            summary["stopped_early"] = True
            break

    posted = sum(1 for i in summary["items"]
                 for r in i["results"].values() if r.get("ok"))
    failed = sum(1 for i in summary["items"]
                 for r in i["results"].values() if r.get("ok") is False)
    deferred = sum(1 for i in summary["items"]
                   for r in i["results"].values() if r.get("deferred"))
    summary["posted"] = posted
    summary["failed"] = failed
    summary["deferred"] = deferred
    # A deferral is the system working, so it must not colour the run red.
    # Only a real failure, or a run that managed nothing at all, does.
    summary["ok"] = failed == 0 and (dry_run or posted > 0 or deferred > 0)
    return summary
