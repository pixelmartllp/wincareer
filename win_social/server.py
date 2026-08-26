"""MCP server for posting to the Win Career Academy Page on Meta.

Page: facebook.com/TheWinCareer

Every publishing tool is a dry run unless it is called with confirm=True.
The dry run reports exactly what would be sent - the resolved image path, the
caption, the character and hashtag counts, and the scheduled time in IST - so
the post can be checked before it reaches a live audience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import (assets, config, content, meta_api, pipeline,
               renderer, state)
from .config import ConfigError
from .meta_api import GraphClient, MetaAPIError

# Instagram's own limits. Facebook's are far looser, so these are the ones
# worth checking before a cross-post silently gets rejected.
IG_CAPTION_LIMIT = 2200
IG_HASHTAG_LIMIT = 30

PLATFORM_CHOICES = ("facebook", "instagram", "both")

mcp = FastMCP(
    "win-social",
    instructions=(
        "Daily creative generation and publishing for Win Career Academy "
        "(facebook.com/TheWinCareer) and its linked Instagram.\n\n"
        "Normal flow: check_setup -> generate_daily_creatives -> "
        "preview_creative -> publish_batch(confirm=true).\n\n"
        "For a one-off that is not part of the daily rotation, post_text and "
        "post_photo take arbitrary copy and images.\n\n"
        "Publishing tools are dry-run by default. Never pass confirm=True "
        "unless the user has explicitly approved that specific post."
    ),
)


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, indent=2, ensure_ascii=False,
                      default=str)


def _err(message: str, **payload: Any) -> str:
    return json.dumps({"ok": False, "error": message, **payload}, indent=2,
                      ensure_ascii=False, default=str)


def _caption_report(text: str) -> dict[str, Any]:
    hashtags = [w for w in text.split() if w.startswith("#")]
    report: dict[str, Any] = {
        "characters": len(text),
        "hashtags": len(hashtags),
    }
    warnings = []
    if len(text) > IG_CAPTION_LIMIT:
        warnings.append(
            f"Caption is {len(text)} characters; Instagram rejects anything "
            f"over {IG_CAPTION_LIMIT}. Facebook will accept it.")
    if len(hashtags) > IG_HASHTAG_LIMIT:
        warnings.append(
            f"{len(hashtags)} hashtags; Instagram allows {IG_HASHTAG_LIMIT}.")
    if warnings:
        report["warnings"] = warnings
    return report


def _platforms(value: str) -> tuple[str, ...]:
    value = (value or "both").strip().lower()
    if value in ("both", "all", ""):
        return ("facebook", "instagram")
    chosen = tuple(p.strip() for p in value.split(",") if p.strip())
    unknown = [p for p in chosen if p not in PLATFORM_CHOICES]
    if unknown:
        raise ValueError(f"Unknown platform(s): {', '.join(unknown)}. "
                         f"Use facebook, instagram or both.")
    return chosen


# --------------------------------------------------------------------------
# Setup and diagnostics
# --------------------------------------------------------------------------

@mcp.tool()
def check_setup() -> str:
    """Health check: credentials, Page access, Instagram link, media folder.

    Run this first. It reports exactly what still needs configuring.
    """
    report: dict[str, Any] = {"meta": config.config_status()}
    todo: list[str] = []

    if not report["meta"]["ready_for_facebook"]:
        todo.append(
            f"Add page_id and access_token to {config.CONFIG_FILE} "
            f"(copy config.example.json, then see README.md). The Win Career "
            f"Academy Page is not covered by the Shashi Pallava token - it "
            f"needs its own.")
    if not report["meta"]["ready_for_instagram"]:
        todo.append("Add ig_user_id for Instagram posting - run "
                    "discover_meta_accounts once the token works.")

    media = sorted(p.name for p in config.MEDIA_DIR.glob("*")
                   if p.suffix.lower() in meta_api.MIME_BY_SUFFIX)
    report["media"] = {"folder": str(config.MEDIA_DIR), "count": len(media),
                       "files": media[:25]}

    report["ledger"] = state.summary()

    if report["meta"]["ready_for_facebook"]:
        try:
            report["live_check"] = GraphClient().verify()
        except (MetaAPIError, ConfigError) as exc:
            report["live_check"] = {"ok": False, "error": str(exc)}
            todo.append(f"Credentials are set but the API rejected them: {exc}")

    report["next_steps"] = todo or [
        "Ready. Try post_text(message=..., confirm=False) for a dry run."]
    return _ok(**report)


@mcp.tool()
def discover_meta_accounts() -> str:
    """List the Facebook Pages this token manages, with their Instagram IDs.

    Use this to find the page_id and ig_user_id for Win Career Academy.
    """
    try:
        return _ok(**GraphClient().discover())
    except (MetaAPIError, ConfigError) as exc:
        return _err(str(exc))


@mcp.tool()
def verify_meta_credentials() -> str:
    """Check the token, the Page and the Instagram link are all working."""
    try:
        return _ok(**GraphClient().verify())
    except (MetaAPIError, ConfigError) as exc:
        return _err(str(exc))


@mcp.tool()
def set_account_ids(page_id: str = "", ig_user_id: str = "") -> str:
    """Save the Page and Instagram IDs into config.json.

    Only the IDs. The access token is deliberately not settable here - a
    token passed through a tool call ends up in the conversation transcript,
    so paste it straight into config.json instead.
    """
    updates = {k: v for k, v in
               {"page_id": page_id, "ig_user_id": ig_user_id}.items() if v}
    if not updates:
        return _err("Nothing to save - pass page_id and/or ig_user_id.")
    try:
        return _ok(saved=list(updates), status=config.save_config(updates))
    except (OSError, ValueError) as exc:
        return _err(f"Could not write {config.CONFIG_FILE}: {exc}")


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

@mcp.tool()
def post_text(message: str, link: str = "", schedule_time: str = "",
              confirm: bool = False) -> str:
    """Post text - optionally with a link preview - to the Facebook Page.

    Args:
        message: The post body.
        link: Optional URL; Facebook renders it as a link preview.
        schedule_time: Optional "YYYY-MM-DD HH:MM" (read as IST) to schedule
            instead of posting now. Must be 10 minutes to 180 days ahead.
        confirm: Must be True to actually post. False returns a dry run.

    This posts publicly to a live Page. Only pass confirm=True when the user
    has explicitly approved this specific post.
    """
    if not message.strip() and not link:
        return _err("Give a message, a link, or both.")

    schedule_unix = None
    if schedule_time:
        try:
            schedule_unix = meta_api.parse_schedule(schedule_time)
        except ValueError as exc:
            return _err(str(exc))

    plan = {
        "platform": "facebook",
        "kind": "link" if link else "text",
        "message": message,
        "link": link or None,
        "scheduled_for": (meta_api.describe_time(schedule_unix)
                          if schedule_unix else "immediately"),
        **_caption_report(message),
    }

    if not confirm:
        return _ok(dry_run=True, would_post=plan,
                   note="Nothing was posted. Call again with confirm=true.")

    try:
        client = GraphClient()
        result = client.post_text(message, link=link,
                                  schedule_unix=schedule_unix)
    except (MetaAPIError, ConfigError, ValueError) as exc:
        state.record("facebook", plan["kind"], ok=False, error=str(exc),
                     message=message[:200])
        return _err(str(exc))

    row = state.record("facebook", plan["kind"], ok=True,
                       post_id=result.get("post_id"),
                       permalink=result.get("permalink"),
                       scheduled_for=result.get("scheduled_for"),
                       message=message[:200])
    return _ok(posted=result, ledger_row=row)


@mcp.tool()
def post_photo(image_path: str, message: str = "", platforms: str = "facebook",
               schedule_time: str = "", confirm: bool = False) -> str:
    """Post one photo with a caption to Facebook and/or Instagram.

    Args:
        image_path: Absolute path, or a path relative to the project folder
            (e.g. "media/batch-01.jpg").
        message: The caption.
        platforms: "facebook", "instagram", or "both". Defaults to facebook.
        schedule_time: Optional "YYYY-MM-DD HH:MM" (IST). Facebook only -
            Instagram's API has no scheduling.
        confirm: Must be True to actually post. False returns a dry run.

    This posts publicly to a live Page. Only pass confirm=True when the user
    has explicitly approved this specific post.
    """
    try:
        targets = _platforms(platforms)
    except ValueError as exc:
        return _err(str(exc))

    schedule_unix = None
    if schedule_time:
        if "instagram" in targets:
            return _err(
                "Instagram's Content Publishing API cannot schedule. Post to "
                "facebook with schedule_time, and post to instagram at the "
                "time you want it live.")
        try:
            schedule_unix = meta_api.parse_schedule(schedule_time)
        except ValueError as exc:
            return _err(str(exc))

    try:
        resolved = GraphClient._checked_image(image_path)
    except MetaAPIError as exc:
        return _err(str(exc), media_folder=str(config.MEDIA_DIR))

    plan = {
        "platforms": list(targets),
        "image": str(resolved),
        "image_kb": round(resolved.stat().st_size / 1024, 1),
        "caption": message,
        "scheduled_for": (meta_api.describe_time(schedule_unix)
                          if schedule_unix else "immediately"),
        **_caption_report(message),
    }

    if not confirm:
        return _ok(dry_run=True, would_post=plan,
                   note="Nothing was posted. Call again with confirm=true.")

    try:
        client = GraphClient()
    except ConfigError as exc:
        return _err(str(exc))

    results: dict[str, Any] = {}
    problems: list[str] = []

    if "facebook" in targets:
        try:
            results["facebook"] = client.post_photo(
                resolved, message, schedule_unix=schedule_unix)
            state.record("facebook", "photo", ok=True,
                         post_id=results["facebook"].get("post_id"),
                         permalink=results["facebook"].get("permalink"),
                         scheduled_for=results["facebook"].get("scheduled_for"),
                         image=resolved.name, message=message[:200])
        except (MetaAPIError, ValueError) as exc:
            problems.append(f"Facebook: {exc}")
            state.record("facebook", "photo", ok=False, error=str(exc),
                         image=resolved.name, message=message[:200])

    if "instagram" in targets:
        try:
            results["instagram"] = client.post_instagram_photo(
                message, image_path=resolved)
            state.record("instagram", "photo", ok=True,
                         post_id=results["instagram"].get("media_id"),
                         permalink=results["instagram"].get("permalink"),
                         image=resolved.name, message=message[:200])
        except (MetaAPIError, ConfigError, ValueError) as exc:
            problems.append(f"Instagram: {exc}")
            state.record("instagram", "photo", ok=False, error=str(exc),
                         image=resolved.name, message=message[:200])

    payload = {"posted": results, "image": str(resolved)}
    if problems:
        return _err("; ".join(problems), **payload)
    return _ok(**payload)


@mcp.tool()
def post_photos(image_paths: str, message: str = "", schedule_time: str = "",
                confirm: bool = False) -> str:
    """Post several photos as one Facebook album-style post.

    Args:
        image_paths: Pipe-separated paths, e.g. "media/a.jpg|media/b.jpg".
        message: The caption for the whole post.
        schedule_time: Optional "YYYY-MM-DD HH:MM" (IST).
        confirm: Must be True to actually post. False returns a dry run.

    Facebook only - Instagram carousels are not handled here.
    """
    paths = [p.strip() for p in image_paths.split("|") if p.strip()]
    if len(paths) < 2:
        return _err("Give at least two paths, pipe-separated. For a single "
                    "photo use post_photo.")

    schedule_unix = None
    if schedule_time:
        try:
            schedule_unix = meta_api.parse_schedule(schedule_time)
        except ValueError as exc:
            return _err(str(exc))

    try:
        resolved = [GraphClient._checked_image(p) for p in paths]
    except MetaAPIError as exc:
        return _err(str(exc), media_folder=str(config.MEDIA_DIR))

    plan = {
        "platform": "facebook",
        "images": [str(p) for p in resolved],
        "caption": message,
        "scheduled_for": (meta_api.describe_time(schedule_unix)
                          if schedule_unix else "immediately"),
        **_caption_report(message),
    }

    if not confirm:
        return _ok(dry_run=True, would_post=plan,
                   note="Nothing was posted. Call again with confirm=true.")

    try:
        result = GraphClient().post_photos(resolved, message,
                                           schedule_unix=schedule_unix)
    except (MetaAPIError, ConfigError, ValueError) as exc:
        state.record("facebook", "photos", ok=False, error=str(exc),
                     images=[p.name for p in resolved])
        return _err(str(exc))

    row = state.record("facebook", "photos", ok=True,
                       post_id=result.get("post_id"),
                       permalink=result.get("permalink"),
                       scheduled_for=result.get("scheduled_for"),
                       images=[p.name for p in resolved],
                       message=message[:200])
    return _ok(posted=result, ledger_row=row)


# --------------------------------------------------------------------------
# The scheduled queue
# --------------------------------------------------------------------------

@mcp.tool()
def list_scheduled_posts(limit: int = 25) -> str:
    """Everything waiting in the Page's scheduled queue."""
    try:
        return _ok(**GraphClient().scheduled_posts(limit))
    except (MetaAPIError, ConfigError) as exc:
        return _err(str(exc))


@mcp.tool()
def publish_scheduled_now(post_id: str, confirm: bool = False) -> str:
    """Release a scheduled post immediately instead of waiting.

    Args:
        post_id: From list_scheduled_posts.
        confirm: Must be True to actually publish it.
    """
    if not confirm:
        return _ok(dry_run=True, would_publish=post_id,
                   note="Nothing was published. Call again with confirm=true.")
    try:
        result = GraphClient().publish_now(post_id)
    except (MetaAPIError, ConfigError) as exc:
        state.record("facebook", "publish_scheduled", ok=False,
                     post_id=post_id, error=str(exc))
        return _err(str(exc))
    state.record("facebook", "publish_scheduled", ok=True, post_id=post_id,
                 permalink=f"https://www.facebook.com/{post_id}")
    return _ok(published=post_id, response=result,
               permalink=f"https://www.facebook.com/{post_id}")


@mcp.tool()
def reschedule_post(post_id: str, schedule_time: str,
                    confirm: bool = False) -> str:
    """Move a scheduled post to a different time.

    Args:
        post_id: From list_scheduled_posts.
        schedule_time: "YYYY-MM-DD HH:MM" (IST) or a full ISO timestamp.
        confirm: Must be True to actually change it.
    """
    try:
        schedule_unix = meta_api.parse_schedule(schedule_time)
    except ValueError as exc:
        return _err(str(exc))

    when = meta_api.describe_time(schedule_unix)
    if not confirm:
        return _ok(dry_run=True, would_reschedule=post_id, to=when,
                   note="Nothing was changed. Call again with confirm=true.")
    try:
        GraphClient().reschedule(post_id, schedule_unix)
    except (MetaAPIError, ConfigError) as exc:
        return _err(str(exc))
    return _ok(rescheduled=post_id, scheduled_for=when)


@mcp.tool()
def delete_post(post_id: str, confirm: bool = False) -> str:
    """Delete a post - live or scheduled. This cannot be undone.

    Args:
        post_id: From list_scheduled_posts or page_recent_posts.
        confirm: Must be True to actually delete it.
    """
    if not confirm:
        return _ok(dry_run=True, would_delete=post_id,
                   note="Nothing was deleted. Call again with confirm=true. "
                        "Deleting a post is permanent.")
    try:
        result = GraphClient().delete_object(post_id)
    except (MetaAPIError, ConfigError) as exc:
        return _err(str(exc))
    state.record("facebook", "delete", ok=True, post_id=post_id)
    return _ok(deleted=post_id, response=result)


# --------------------------------------------------------------------------
# Reading back
# --------------------------------------------------------------------------

@mcp.tool()
def page_recent_posts(limit: int = 10) -> str:
    """What is actually live on the Facebook Page right now."""
    try:
        return _ok(**GraphClient().recent_posts(limit))
    except (MetaAPIError, ConfigError) as exc:
        return _err(str(exc))


@mcp.tool()
def post_history(limit: int = 20, only_failures: bool = False) -> str:
    """Attempts recorded by this server, newest first - failures included."""
    return _ok(summary=state.summary(),
               posts=state.history(limit, only_failures))


@mcp.tool()
def list_media() -> str:
    """Images available to post from the project's media folder."""
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    files = [{"name": p.name, "path": str(p),
              "kb": round(p.stat().st_size / 1024, 1)}
             for p in sorted(config.MEDIA_DIR.glob("*"))
             if p.suffix.lower() in meta_api.MIME_BY_SUFFIX]
    return _ok(folder=str(config.MEDIA_DIR), count=len(files), files=files)


# --------------------------------------------------------------------------
# Creatives
# --------------------------------------------------------------------------

@mcp.tool()
def content_bank_status() -> str:
    """How many post ideas are left before the rotation repeats."""
    try:
        return _ok(**content.bank_stats())
    except content.ContentBankError as exc:
        return _err(str(exc))


@mcp.tool()
def background_pool() -> str:
    """The photographs available, and how they are split across themes."""
    return _ok(logo=assets.logo_report(), **assets.pool_report())


@mcp.tool()
def generate_daily_creatives(count: int = 1, date: str = "", theme: str = "",
                             layout: str = "", canvas: str = "portrait",
                             overwrite: bool = False) -> str:
    """Render the day's creatives with captions. Publishes nothing.

    Args:
        count: How many to make. One a day is the cadence.
        date: YYYY-MM-DD. Defaults to today (IST).
        theme: Restrict to one theme. Empty means any.
        layout: split_light (default, light panel + photo) or photo_dark
            (full-bleed photograph, type seated into the lower third).
        canvas: portrait, square or story.
        overwrite: Replace an existing batch for that date.
    """
    try:
        batch = pipeline.generate_day(
            count=count, day=date or None, canvas=canvas,
            theme=theme or None, layout=layout or None, overwrite=overwrite)
    except FileExistsError as exc:
        return _err(str(exc), hint="Pass overwrite=true to replace it.")
    except (ValueError, FileNotFoundError, content.ContentBankError,
            assets.AssetError, renderer.RenderError) as exc:
        return _err(str(exc))

    unseated = [i["content_id"] for i in batch["items"] if not i["text_seated"]]
    return _ok(
        date=batch["date"], count=batch["count"], layout=batch["layout"],
        folder=str(pipeline.batch_dir(batch["date"])),
        items=[{k: i.get(k) for k in
                ("index", "content_id", "theme", "headline", "accent",
                 "layout", "background", "text_seated", "image_path")}
               for i in batch["items"]],
        warning=(f"Type did not seat on {', '.join(unseated)} - preview before "
                 f"publishing.") if unseated else None,
        next_step="preview_creative, then publish_batch(confirm=true).")


@mcp.tool()
def list_batch(date: str = "") -> str:
    """A day's creatives with their captions and publish status."""
    try:
        return _ok(**pipeline.load_batch(date or pipeline.today()))
    except FileNotFoundError as exc:
        return _err(str(exc), available=pipeline.list_batches()[-8:])


@mcp.tool()
def list_batches() -> str:
    """Every generated day."""
    return _ok(batches=pipeline.list_batches())


@mcp.tool()
def preview_creative(date: str = "", index: int = 1) -> Any:
    """Return a rendered creative as an image so it can be looked at.

    Annotated Any because it returns either an image or a JSON error string,
    and a union of the two has no pydantic schema.
    """
    try:
        batch = pipeline.load_batch(date or pipeline.today())
        item = pipeline.find_item(batch, index)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))

    path = Path(item["image_path"])
    if not path.is_file():
        return _err(f"Image missing on disk: {path}")
    return MCPImage(path=path)


@mcp.tool()
def regenerate_creative(date: str = "", index: int = 1, layout: str = "",
                        background: str = "") -> str:
    """Re-render one creative, optionally with a different layout or photo.

    Args:
        layout: split_light or photo_dark.
        background: A filename from background_pool. Empty picks a fresh one.
    """
    try:
        item = pipeline.regenerate_item(
            date or pipeline.today(), index,
            layout=layout or None, background=background or None)
    except (FileNotFoundError, ValueError, assets.AssetError,
            renderer.RenderError) as exc:
        return _err(str(exc))
    return _ok(regenerated={k: item.get(k) for k in
                            ("index", "content_id", "layout", "background",
                             "text_seated", "image_path")},
               available_layouts=list(renderer.LAYOUTS))


@mcp.tool()
def edit_creative_caption(date: str = "", index: int = 1,
                          platform: str = "both", caption: str = "") -> str:
    """Replace a creative's caption before it is published.

    Args:
        platform: facebook, instagram, or both.
    """
    if not caption.strip():
        return _err("caption cannot be empty")
    if platform not in ("facebook", "instagram", "both"):
        return _err("platform must be facebook, instagram or both")

    try:
        batch = pipeline.load_batch(date or pipeline.today())
        item = pipeline.find_item(batch, index)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))

    targets = ("facebook", "instagram") if platform == "both" else (platform,)
    for target in targets:
        item[f"caption_{target}"] = caption
    pipeline.save_batch(batch)
    return _ok(index=index, updated=list(targets))


@mcp.tool()
def publish_batch(date: str = "", platforms: str = "both",
                  confirm: bool = False, force: bool = False) -> str:
    """Publish a generated day to Facebook and/or Instagram.

    Args:
        platforms: "both", "facebook", "instagram".
        confirm: Must be True to actually post. False returns a dry run.
        force: Post again even if the ledger says it already went out today.

    This posts publicly to a live Page. Only pass confirm=True when the user
    has explicitly approved publishing this batch.
    """
    try:
        targets = _platforms(platforms)
    except ValueError as exc:
        return _err(str(exc))

    try:
        summary = pipeline.publish_day(date or None, platforms=targets,
                                       dry_run=not confirm, force=force)
    except (FileNotFoundError, ValueError, ConfigError) as exc:
        return _err(str(exc))

    if not confirm:
        summary["note"] = ("Dry run - nothing was posted. Call again with "
                           "confirm=true.")
    return _ok(**summary)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
