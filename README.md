# win-social — posting to Win Career Academy on Meta

An MCP server that posts to the **Win Career Academy** Facebook Page
([facebook.com/TheWinCareer](https://www.facebook.com/TheWinCareer)) and, once
an Instagram Business account is linked to it, to that Instagram too.

It is a sibling of the `shashi-social` server in `D:\Shi`, deliberately kept
separate: its own credentials, its own ledger, its own environment-variable
names. Nothing here can post to the Shashi Pallava Page and nothing there can
post here.

---

## 1. What it does

| Tool | What it does |
| --- | --- |
| `check_setup` | Credentials, Page access, Instagram link, media folder, ledger. Run first. |
| `discover_meta_accounts` | Pages the token manages, with their Instagram IDs. |
| `verify_meta_credentials` | Token validity, scopes, expiry; Page and Instagram reachable. |
| `set_account_ids` | Saves `page_id` / `ig_user_id` into `config.json`. |
| `post_text` | Text post, optionally with a link preview, optionally scheduled. |
| `post_photo` | One photo + caption to Facebook, Instagram, or both. |
| `post_photos` | Several photos as one Facebook post. |
| `list_scheduled_posts` | The Page's scheduled queue. |
| `publish_scheduled_now` | Release a scheduled post early. |
| `reschedule_post` | Move a scheduled post to another time. |
| `delete_post` | Delete a post, live or scheduled. Permanent. |
| `page_recent_posts` | What is actually live on the Page now. |
| `post_history` | This server's own ledger — including failed attempts. |
| `list_media` | Images sitting in `media/` ready to post. |
| `content_bank_status` | How many post ideas remain before the rotation repeats. |
| `background_pool` | The photographs available, split by theme, plus logo health. |
| `generate_daily_creatives` | Render the day's creatives with captions. Posts nothing. |
| `list_batch` / `list_batches` | A day's creatives and their status; every generated day. |
| `preview_creative` | Return a rendered creative as an image to look at. |
| `regenerate_creative` | Re-render one, with a different layout or photograph. |
| `edit_creative_caption` | Replace a caption before publishing. |
| `publish_batch` | Publish a generated day to Facebook and/or Instagram. |

**Every posting tool is a dry run unless called with `confirm=True`.** The dry
run reports the resolved image path, the caption, its character and hashtag
counts, and the scheduled time in IST. That is the moment to check the post —
after `confirm=True` it is live to a real audience.

---

## 2. Setup

### 2.1 Credentials

The Win Career Academy Page needs **its own access token**. The token used by
the Shashi Pallava pipeline manages only *Shashi Pallava*, *AP Machines Tools*
and *PiXelmart LLP* — Win Career Academy is not among them, so that token
cannot post here.

Two ways to get one:

- **Add the Page to the existing app/system user** (least work, one token to
  maintain). In Meta Business Suite → Business settings → Accounts → Pages,
  add Win Career Academy, then under Users → System users assign it to the
  same system user, with *Manage Page* access. Generate a token with
  `pages_manage_posts`, `pages_read_engagement`, and — for Instagram —
  `instagram_basic` and `instagram_content_publish`. A system-user token does
  not expire, which is what makes unattended posting possible.
- **Fresh Graph API Explorer token** for an account that admins the Page:
  short-lived user token → exchange for a long-lived one → read the Page
  token off `me/accounts`. `win_social/meta_api.py` has
  `exchange_for_long_lived()` and `page_token_from_user_token()` for exactly
  this.

Then:

```bash
cp config.example.json config.json
# paste page_id and access_token into config.json
.venv/Scripts/python.exe win_post.py discover   # confirms the Page is visible
.venv/Scripts/python.exe win_post.py verify     # confirms the token and scopes
```

`config.json` is gitignored. Never commit it, never paste it into a message or
an external service.

The `set_account_ids` tool writes `page_id` and `ig_user_id` for you. It
deliberately **cannot** write the token: anything passed through a tool call
lands in the conversation transcript, so the token goes into `config.json` by
hand.

### 2.2 Environment variables (for CI, where there is no `config.json`)

`WIN_META_PAGE_ID`, `WIN_META_ACCESS_TOKEN`, `WIN_META_IG_USER_ID`,
`WIN_META_APP_ID`, `WIN_META_APP_SECRET`, `WIN_META_API_VERSION`.

The `WIN_` prefix is not decoration. The Shashi pipeline on this machine reads
the generic `META_*` names; an unprefixed variable set user-wide would let one
brand's credentials publish to the other brand's Page.

`config.json` wins over the environment when both are set.

### 2.3 Registering the MCP server

`.mcp.json` in this folder already declares it, so a Claude Code session
started in `D:\Shi\PM\win` picks it up:

```json
{
  "mcpServers": {
    "win-social": {
      "command": "D:\\Shi\\.venv\\Scripts\\python.exe",
      "args": ["-m", "win_social.server"],
      "env": { "PYTHONPATH": "D:\\Shi\\PM\\win" }
    }
  }
}
```

It reuses the `D:\Shi\.venv` interpreter, which already has `mcp` and
`requests`. To register it globally instead:

```bash
claude mcp add win-social -s user -e PYTHONPATH=D:\Shi\PM\win -- D:\Shi\.venv\Scripts\python.exe -m win_social.server
```

---

## 3. Posting

Drop images into `media/` (jpg, png or webp — Meta rejects anything else) and
pass either an absolute path or one relative to this folder.

Through the MCP tools:

```
post_text(message="Admissions open for the 2026 batch.", confirm=false)
post_text(message="...", confirm=true)

post_photo(image_path="media/batch-poster.jpg", message="...",
           platforms="both", confirm=true)

post_text(message="...", schedule_time="2026-08-27 09:30", confirm=true)
```

Or from the command line, which does the same work without an MCP client:

```bash
.venv/Scripts/python.exe win_post.py status
.venv/Scripts/python.exe win_post.py text "Admissions open" --link https://... --confirm
.venv/Scripts/python.exe win_post.py photo media/poster.jpg -m "Caption" --both --confirm
.venv/Scripts/python.exe win_post.py scheduled
.venv/Scripts/python.exe win_post.py history --failures
```

Nothing posts without `--confirm` / `confirm=True`.

### Scheduling

`schedule_time` accepts `YYYY-MM-DD HH:MM` — read as **IST**, because a bare
time meaning UTC would silently post five and a half hours early — or a full
ISO timestamp with an offset. Facebook's own bounds apply: at least 10 minutes
ahead, at most 180 days. Both are checked before anything is sent.

**Instagram cannot be scheduled.** Its Content Publishing API only publishes
now. `post_photo` refuses the combination rather than posting immediately and
letting you think it was queued.

### Instagram specifics

Instagram will not take a file upload, only a public URL. The photo is
uploaded to the Facebook Page as an *unpublished, temporary* photo, that CDN
URL feeds the Instagram container, and the scratch photo is deleted afterwards.
No third-party image host is involved.

Instagram's limits are the tight ones and the dry run warns about both: 2200
caption characters, 30 hashtags. Facebook is far looser.

---

## 4. Did it actually post?

"The tool returned" is not "it went out". Two independent checks:

```bash
.venv/Scripts/python.exe win_post.py history     # this server's ledger
.venv/Scripts/python.exe win_post.py recent      # what Meta says is live
```

`state/state.json` records **every attempt, failures included** — Meta's own
edges only show what is live right now, so a failed post leaves no trace there.
The ledger is what makes a bad day diagnosable.

Errors worth recognising, learned from the sibling pipeline:

- `OAuthException (code 190): Bad signature` — the token is wrong or revoked.
  Regenerate it; a token that works elsewhere proves nothing about this one.
- `code 1: please reduce the amount of data` — a transient Graph flake. Retry.
- `(#200) ... requires pages_manage_posts` — the token is missing a scope, or
  it is a user token that was never exchanged for the Page token.

---

## 5. Layout

```
win_social/
  config.py     credentials, paths, WIN_META_* env fallback
  meta_api.py   Graph client: text, link, photo, multi-photo, schedule, IG
  state.py      the ledger
  server.py     the MCP tools and the confirm guard
win_post.py     CLI front end
media/          images to post (gitignored)
state/          the ledger (gitignored)
config.json     credentials (gitignored, never commit)
```

## 6. The daily pipeline

```
content_bank.json ─┐
                   ├─> generate_day() ─> output/<date>/NN-<id>.jpg
assets/backgrounds ┘                     + batch.json (captions, status)
                                                │
                                         publish_day() ─> Facebook, Instagram
                                                │
                                         state/state.json (ledger)
```

Generation never posts and publishing never renders. That separation is what
lets a bad batch be regenerated with no risk of it going out, and a failed
post be retried without re-rendering.

### Creatives

Two layouts, both 1080x1350:

- **`split_light`** (default) - light panel carrying the type on the left, the
  photograph curving in from the right. The curve is the device the Academy's
  own flyers already use. On a light ground the logo goes on bare, with no
  plate, and no scrim is needed at all: the type sits on a flat brand colour,
  so contrast is fixed by construction.
- **`photo_dark`** - full-bleed photograph with the type seated into the lower
  third. Here the scrim *is* measured: the strip the text occupies is judged
  on a brightness percentile, and the scrim deepens until the ink separates.
  The accent line gets the stricter target because it always fails first.

Every creative carries the logo, the standing line
(`Upgrade your English, Upgrade your CAREER!`), the `BUSINESS ENGLISH` chip,
the feature row, and a footer with the number and the WhatsApp QR.

### Running a day

```bash
.venv/Scripts/python.exe win_post.py generate            # renders, posts nothing
.venv/Scripts/python.exe win_post.py batch               # captions and status
.venv/Scripts/python.exe win_post.py publish             # dry run
.venv/Scripts/python.exe win_post.py publish --confirm   # live
.venv/Scripts/python.exe win_post.py daily --confirm     # both, idempotent
```

`daily` is what the workflow calls. It checks the ledger first and exits
without posting if the day already went out, which is what makes three
scheduled runs safe.

### Automation

`.github/workflows/daily.yml` runs at 09:00, 12:00 and 15:30 IST. Three runs
because GitHub's scheduler regularly fires a cron two or three hours late; on
the sibling pipeline an 08:07 job repeatedly landed after 10:30 and the day
was missed. Only the first run that gets through does any work.

It runs on **ubuntu**, not windows, because every brand font ships in
`assets/fonts/`. A guard step proves that rather than assuming it - if a font
file goes missing the run fails loudly instead of quietly rendering the brand
in Arial.

Secrets needed: `WIN_META_ACCESS_TOKEN`, `WIN_META_PAGE_ID`,
`WIN_META_IG_USER_ID`.

### Topping up the photographs

```bash
python tools/fetch_photos.py --per-query 10   # Pexels -> assets/_staging/
python tools/contact_sheet.py                 # then LOOK at the sheets
#   add the ids you want to assets/_staging/selected.txt
python tools/install_photos.py --write        # crop, name, write SOURCES.md
python tools/verify_creative.py output/<date> # QR still scans?
```

The contact sheet is not optional. On this pool it caught burnt-in Canva and
Pexels watermarks, a subject in a party hat, a man packing his desk into a box
(reads as leaving a job), and a US flag. Metadata showed nothing wrong with
any of them.

`selected.txt` is an accept list, not a reject list. A photo nobody looked at
never reaches the pool.

## 7. Not in scope yet

Video and Reels (they need Meta's resumable upload protocol), Instagram
carousels, Stories, comment moderation, and Page insights. Each is a
self-contained addition to `meta_api.py` plus one tool in `server.py`.
