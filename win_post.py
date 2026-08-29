"""Command line front end for the Win Career Academy poster.

The MCP server is the normal way in; this is here so posting can be tested,
scripted or run from Task Scheduler without an MCP client.

    .venv/Scripts/python.exe win_post.py status
    .venv/Scripts/python.exe win_post.py discover
    .venv/Scripts/python.exe win_post.py text "Admissions open" --link https://...
    .venv/Scripts/python.exe win_post.py photo media/poster.jpg -m "Caption" --both
    .venv/Scripts/python.exe win_post.py scheduled
    .venv/Scripts/python.exe win_post.py history

Nothing posts without --confirm.
"""

from __future__ import annotations

import argparse
import json
import sys

from win_social import config, content, meta_api, pipeline, state
from win_social.config import ConfigError, force_utf8
from win_social.meta_api import GraphClient, MetaAPIError


def show(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def cmd_status(args) -> int:
    status = config.config_status()
    show({"config": status, "ledger": state.summary()})
    if not status["ready_for_facebook"]:
        print("\nNot ready: fill page_id and access_token into "
              f"{config.CONFIG_FILE}", file=sys.stderr)
        return 2
    return 0


def cmd_discover(args) -> int:
    show(GraphClient().discover())
    return 0


def cmd_verify(args) -> int:
    result = GraphClient().verify()
    show(result)
    return 0 if result["ok"] else 1


def cmd_text(args) -> int:
    schedule_unix = meta_api.parse_schedule(args.at) if args.at else None
    if not args.confirm:
        show({"dry_run": True, "message": args.message, "link": args.link,
              "scheduled_for": (meta_api.describe_time(schedule_unix)
                                if schedule_unix else "immediately")})
        print("\nNothing posted. Add --confirm to publish.", file=sys.stderr)
        return 0

    client = GraphClient()
    try:
        result = client.post_text(args.message, link=args.link or "",
                                  schedule_unix=schedule_unix)
    except (MetaAPIError, ValueError) as exc:
        state.record("facebook", "text", ok=False, error=str(exc),
                     message=args.message[:200])
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    state.record("facebook", "text", ok=True, post_id=result.get("post_id"),
                 permalink=result.get("permalink"),
                 scheduled_for=result.get("scheduled_for"),
                 message=args.message[:200])
    show(result)
    return 0


def cmd_photo(args) -> int:
    schedule_unix = meta_api.parse_schedule(args.at) if args.at else None
    targets = ["facebook"]
    if args.both:
        targets.append("instagram")
    elif args.instagram_only:
        targets = ["instagram"]

    if schedule_unix and "instagram" in targets:
        print("Instagram cannot be scheduled through the API.", file=sys.stderr)
        return 2

    path = GraphClient._checked_image(args.image)
    if not args.confirm:
        show({"dry_run": True, "image": str(path), "platforms": targets,
              "caption": args.message,
              "scheduled_for": (meta_api.describe_time(schedule_unix)
                                if schedule_unix else "immediately")})
        print("\nNothing posted. Add --confirm to publish.", file=sys.stderr)
        return 0

    client = GraphClient()
    failed = False
    results = {}
    if "facebook" in targets:
        try:
            results["facebook"] = client.post_photo(
                path, args.message, schedule_unix=schedule_unix)
            state.record("facebook", "photo", ok=True, image=path.name,
                         post_id=results["facebook"].get("post_id"),
                         permalink=results["facebook"].get("permalink"),
                         scheduled_for=results["facebook"].get("scheduled_for"),
                         message=args.message[:200])
        except (MetaAPIError, ValueError) as exc:
            failed = True
            results["facebook"] = {"error": str(exc)}
            state.record("facebook", "photo", ok=False, error=str(exc),
                         image=path.name, message=args.message[:200])
    if "instagram" in targets:
        try:
            results["instagram"] = client.post_instagram_photo(
                args.message, image_path=path)
            state.record("instagram", "photo", ok=True, image=path.name,
                         post_id=results["instagram"].get("media_id"),
                         permalink=results["instagram"].get("permalink"),
                         message=args.message[:200])
        except (MetaAPIError, ConfigError, ValueError) as exc:
            failed = True
            results["instagram"] = {"error": str(exc)}
            state.record("instagram", "photo", ok=False, error=str(exc),
                         image=path.name, message=args.message[:200])
    show(results)
    return 1 if failed else 0


def cmd_generate(args) -> int:
    """Render the day's creatives. Posts nothing."""
    try:
        batch = pipeline.generate_day(
            count=args.count, day=args.date or None, canvas=args.canvas,
            theme=args.theme or None, layout=args.layout or None,
            overwrite=args.overwrite)
    except FileExistsError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    show({"date": batch["date"], "count": batch["count"],
          "layout": batch["layout"],
          "items": [{k: i[k] for k in
                     ("index", "content_id", "theme", "headline", "layout",
                      "background", "text_seated", "image_path")}
                    for i in batch["items"]]})

    unseated = [i["content_id"] for i in batch["items"] if not i["text_seated"]]
    if unseated:
        print(f"\nWARNING: type did not seat on {', '.join(unseated)} - "
              f"look before publishing.", file=sys.stderr)
        return 1
    return 0


def cmd_batch(args) -> int:
    show(pipeline.load_batch(args.date or pipeline.today()))
    return 0


def cmd_batches(args) -> int:
    show({"batches": pipeline.list_batches()})
    return 0


def cmd_publish(args) -> int:
    """Publish a generated batch. Dry run unless --confirm."""
    platforms = tuple(p.strip() for p in args.platforms.split(",") if p.strip())         if args.platforms else pipeline.PLATFORMS
    summary = pipeline.publish_day(
        args.date or None, platforms=platforms,
        dry_run=not args.confirm, force=args.force, immediate=args.now)
    show(summary)
    if not args.confirm:
        print("\nDry run. Add --confirm to publish.", file=sys.stderr)
        return 0
    return 0 if summary["ok"] else 1


def cmd_daily(args) -> int:
    """Generate today's creative if needed, then publish it. Idempotent.

    Every scheduled run calls this, and only the first one that gets through
    does any work. That is deliberate: GitHub's cron regularly fires two or
    three hours late, so the day needs more than one chance to go out - and
    a later run must not post a second copy of what already went.
    """
    day = args.date or pipeline.today()
    platforms = pipeline.PLATFORMS

    done = [p for p in platforms if any(
        row.get("ok") and row.get("platform") == p
        for row in state.posts_on(day))]
    if len(done) == len(platforms):
        show({"date": day, "action": "nothing to do",
              "already_posted": done})
        return 0

    try:
        batch = pipeline.load_batch(day)
        action = "existing batch"
    except FileNotFoundError:
        batch = pipeline.generate_day(count=args.count, day=day)
        action = "generated"

    unseated = [i["content_id"] for i in batch["items"] if not i["text_seated"]]
    if unseated:
        # Say it, but do not stop the day. An unseated headline is a bad
        # creative; no post at all is a worse one, and the ledger keeps the
        # evidence either way.
        print(f"WARNING: type did not seat on {', '.join(unseated)}",
              file=sys.stderr)

    if not args.confirm:
        show({"date": day, "action": action, "dry_run": True,
              "already_posted": done,
              "would_publish": [i["content_id"] for i in batch["items"]]})
        print("\nDry run. Add --confirm to publish.", file=sys.stderr)
        return 0

    summary = pipeline.publish_day(day, platforms=platforms, dry_run=False)
    show({"date": day, "action": action, "posted": summary["posted"],
          "failed": summary["failed"], "ok": summary["ok"],
          "results": [i["results"] for i in summary["items"]]})
    return 0 if summary["ok"] else 1


def cmd_bank(args) -> int:
    show(content.bank_stats())
    return 0


def cmd_scheduled(args) -> int:
    show(GraphClient().scheduled_posts(args.limit))
    return 0


def cmd_recent(args) -> int:
    show(GraphClient().recent_posts(args.limit))
    return 0


def cmd_history(args) -> int:
    show({"summary": state.summary(),
          "posts": state.history(args.limit, args.failures)})
    return 0


def cmd_delete(args) -> int:
    if not args.confirm:
        print(f"Would delete {args.post_id}. Add --confirm. This is permanent.")
        return 0
    show(GraphClient().delete_object(args.post_id))
    state.record("facebook", "delete", ok=True, post_id=args.post_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Post to the Win Career Academy Facebook Page.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="what is configured").set_defaults(
        func=cmd_status)
    sub.add_parser("discover", help="pages this token manages").set_defaults(
        func=cmd_discover)
    sub.add_parser("verify", help="check token, page and instagram").set_defaults(
        func=cmd_verify)

    text = sub.add_parser("text", help="post text, optionally with a link")
    text.add_argument("message")
    text.add_argument("--link", default="")
    text.add_argument("--at", default="", metavar="YYYY-MM-DD HH:MM",
                      help="schedule instead of posting now (IST)")
    text.add_argument("--confirm", action="store_true")
    text.set_defaults(func=cmd_text)

    photo = sub.add_parser("photo", help="post one photo")
    photo.add_argument("image")
    photo.add_argument("-m", "--message", default="")
    photo.add_argument("--both", action="store_true",
                       help="post to Instagram as well")
    photo.add_argument("--instagram-only", action="store_true")
    photo.add_argument("--at", default="", metavar="YYYY-MM-DD HH:MM")
    photo.add_argument("--confirm", action="store_true")
    photo.set_defaults(func=cmd_photo)

    generate = sub.add_parser("generate", help="render the day's creatives")
    generate.add_argument("--count", type=int, default=1)
    generate.add_argument("--date", default="", metavar="YYYY-MM-DD")
    generate.add_argument("--canvas", default="portrait")
    generate.add_argument("--theme", default="")
    generate.add_argument("--layout", default="")
    generate.add_argument("--overwrite", action="store_true")
    generate.set_defaults(func=cmd_generate)

    batch = sub.add_parser("batch", help="show a day's batch")
    batch.add_argument("--date", default="", metavar="YYYY-MM-DD")
    batch.set_defaults(func=cmd_batch)

    sub.add_parser("batches", help="list generated days").set_defaults(
        func=cmd_batches)

    publish = sub.add_parser("publish", help="publish a generated batch")
    publish.add_argument("--date", default="", metavar="YYYY-MM-DD")
    publish.add_argument("--platforms", default="",
                         help="facebook,instagram (default both)")
    publish.add_argument("--force", action="store_true",
                         help="post again even if already posted today")
    publish.add_argument("--now", action="store_true",
                         help="ignore the 09:00 IST slot and the Instagram "
                              "window; post this moment")
    publish.add_argument("--confirm", action="store_true")
    publish.set_defaults(func=cmd_publish)

    daily = sub.add_parser(
        "daily", help="generate today's creative if needed, then publish it")
    daily.add_argument("--date", default="", metavar="YYYY-MM-DD")
    daily.add_argument("--count", type=int, default=1)
    daily.add_argument("--confirm", action="store_true")
    daily.set_defaults(func=cmd_daily)

    sub.add_parser("bank", help="content bank status").set_defaults(
        func=cmd_bank)

    scheduled = sub.add_parser("scheduled", help="the scheduled queue")
    scheduled.add_argument("--limit", type=int, default=25)
    scheduled.set_defaults(func=cmd_scheduled)

    recent = sub.add_parser("recent", help="what is live on the page")
    recent.add_argument("--limit", type=int, default=10)
    recent.set_defaults(func=cmd_recent)

    history = sub.add_parser("history", help="this tool's own ledger")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--failures", action="store_true")
    history.set_defaults(func=cmd_history)

    delete = sub.add_parser("delete", help="delete a post (permanent)")
    delete.add_argument("post_id")
    delete.add_argument("--confirm", action="store_true")
    delete.set_defaults(func=cmd_delete)

    return parser


def main() -> int:
    force_utf8()
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 3
    except (MetaAPIError, ValueError, content.ContentBankError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
