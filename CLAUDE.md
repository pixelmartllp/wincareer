# CLAUDE.md — Win Career Academy daily social pipeline

Read this before touching anything here. Almost every rule below is a defect
that actually shipped, or nearly did. The code looks over-careful in places;
those are the places that bit.

The owner is **Sanjeev** (@axisuv), running the **Win Career Academy** brand —
facebook.com/TheWinCareer and the linked Instagram @thewincareer. The Academy
teaches spoken English, communication and interview skills to working
professionals; **Mandeepa Garg** is the Director and the face of the brand.
He often writes in Hinglish; reply in the language he used. Report what
actually happened, not what the code intends to happen.

This is a sibling of the Shashi Pallava pipeline in `D:\Shi`, deliberately
separate: own credentials, own ledger, own `WIN_META_*` environment names.
Neither brand can publish to the other's Page.

---

## 1. Standing decisions (do not re-litigate)

### 1.1 The creative is `dark_hero`

Near-black ground with a warm desk-light pool, kicker, stacked headline,
free-demo block, the mentor faded in on the right, footer with the number and
the WhatsApp QR. The palette came from the **MET** reference the owner sent;
the stacked hook and the prominence of the offer from **SKH**; the restraint
from **COMEX** with the content stripped back.

Two earlier layouts survive and still work — `split_light` (light panel, photo
curving in from the right) and `photo_dark` (full-bleed photograph, type
seated into the lower third). `dark_hero` is the default. Do not delete the
others; they are how a different brief gets served without rewriting.

**The owner's verdict that reshaped this layout:** the first dark creative
"said nothing about learning English and nothing about free demo classes".
Both *were* present — a chip in the corner and a caption-sized pill — and both
were too small to register. On an ad the product must be louder than the
daily insight. Hence the kicker (`LEARN ENGLISH SPEAKING`) in the display face
above the headline, and the offer (`FREE DEMO CLASSES`) as its own block at
roughly twice its former size.

### 1.2 Never generate a likeness of Mandeepa Garg

The owner asked for her "in different angles" and, when told there was only
one photograph, asked for them to be created. **Declined, and that stands.**
She is a real, identifiable person; synthetic photographs of her published on
a live Page read as genuine, and her own consent is not ours to assume.

What is done instead: `mentor_variant()` gives four crop tightnesses from one
photograph so consecutive days are not a reprint. That is a smaller
improvement than it sounds — four framings of one photograph is still one
photograph.

The real fix is 6–8 real photographs in `assets/mentor/`. The layout rotates
through whatever is in that folder, keyed on the content id, so adding files
needs no code change.

Two things that cannot be done to the existing photograph:

- **No mirroring.** The Academy's logo is on the wall behind her; flipping
  reverses the lettering.
- **No left-side placement.** She stands in the right of the frame, so masking
  the left half shows the wall with her head cropped away. The `side="left"`
  path is kept for a photograph composed the other way, but it cannot be
  selected automatically without knowing where the subject stands.

The one photograph in `assets/mentor/` was recovered from the Page's own
Instagram, because no source files exist locally and what the owner supplied
was 206×206.

### 1.3 Everything on the creative is measured, not assumed

Each of these is a bug that rendered and looked plausible:

- **Scrim targets are strict.** The first pass used 96/78; every creative
  "passed" and the type was still fighting the photograph. They are 74/62 now.
  Judge on a **percentile (88th), not the mean** — office photographs average
  comfortable while every window and white shirt in them is blown. Judge the
  **band the text occupies**, not the frame. **The accent fails first** — it is
  smaller and lower contrast, so it gets the stricter target and the scrim has
  to satisfy it.
- **One scrim, not one per band.** Two scrims compound unevenly and leave a
  visible step across the frame.
- **The logo is plated** on both navy and near-black. Roughly a sixth of its
  ink — THE, ACADEMY, the strapline — is black, and the CAREER ribbon's dark
  red and purple have almost no contrast on near-black. `load_logo_light_ink`
  rescues the lettering but not the ribbon; it also skips *saturated* dark
  pixels, because judging on brightness alone ate the ribbon's edges. Plating
  is the Academy's own device — see their flyers.
- **`fit_logo` sizes by both dimensions.** The sibling pipeline's width-only
  calculation rendered the logo at 98px on a 1080 canvas and it vanished.
- **The QR is plated too.** The supplied artwork is black modules on
  transparency: on navy the "white" modules become navy and no scanner will
  read it. And it is **verified by decoding it back out of the finished
  JPEG** — `tools/verify_creative.py`. A QR that stops scanning looks perfectly
  fine in review.
  - Decode the **footer crop**, upscaled — not the whole frame. OpenCV's
    detector regularly fails to locate a small QR inside a large busy image
    even when the code is perfect, which produced a false alarm once already.
- **Headlines shrink until the longest single word fits the column.** Wrapping
  cannot save a word wider than its measure; REMEMBER, UNPREPARED and
  VOCABULARY ran straight across the photograph.
- **Line spacing is 1.02.** Anton's caps collide below about 1.0.
- **The feature card fits its font to the space**, not the other way round.
  Sizing the card from the layout and trusting the content to be smaller ran
  the row off both edges of the canvas.

### 1.4 Photographs: look before installing

`tools/fetch_photos.py` → `tools/contact_sheet.py` → **look at every sheet** →
add ids to `assets/_staging/selected.txt` → `tools/install_photos.py --write`.

`selected.txt` is an **accept list, not a reject list**. A photo nobody looked
at never reaches the pool.

The contact sheet crops exactly as the renderer does and overlays the zones
that are spoken for. On this pool it caught, in order: burnt-in **Canva and
Pexels watermarks**, a subject in a **party hat**, a man **packing his desk
into a box** (reads as leaving a job — the opposite of the message), and a
**US flag**. Metadata showed nothing wrong with any of them.

**Queries must be India-first.** The first sweep of 77 results was almost
entirely Western models; only the one explicitly Indian query returned Indian
people. On an Indian brand's Page that reads as bought-in stock.

Pool today: **33 photographs, 20 theme-neutral (60%)**. Keep the neutral
majority — it is what guarantees a photo survives the recent-use exclusion.

**Known weakness:** the empty-room photographs (conference rooms, auditorium
seats) are visibly weaker than the people ones. They read as office stock, not
as English teaching. One was auto-selected for a live day and had to be
swapped by hand.

### 1.5 Timing is decided by us, not by GitHub

GitHub's scheduler has fired these crons **4.5 to 6 hours late**, and on
27 Aug 2026 dropped every scheduled run on both this repo and `spallava` for
an entire day. Treat "the day is quiet" as a cause in its own right and check
the run list before blaming credentials.

Two mechanisms handle it:

- **Facebook posts are scheduled**, not fired. A run arriving at 2am queues
  the post for **09:00 IST** instead of publishing into the dark. Once the
  slot has passed the post goes immediately rather than being pushed to
  tomorrow — tomorrow belongs to tomorrow's creative.
- **Instagram cannot be scheduled at all** (its Content Publishing API only
  publishes now), so it gets a **07:00–21:00 IST window** and defers outside
  it. A deferral writes **no ledger row** and does **not** colour the run red:
  nothing was attempted and nothing went wrong.

The crons are set at **00:05, 03:05, 06:35 and 09:35 IST** — early enough that
the drift still lands before 09:00. The original 09:00/12:00/15:30 crons meant
the earliest run of the day landed at 14:55 IST, the slot had always passed,
and every post fell through to mid-afternoon. **This compensates for a number
GitHub can change without telling anyone** — re-read the run times against the
ledger occasionally rather than assuming it still holds.

Known gap, left deliberately: if the only run of a day arrives after 21:00,
Instagram defers and the next day's runs work on the next day's plan, so that
day's Instagram post is missed while Facebook's goes out.

### 1.6 A day is decided once

`state.set_plan()` pins the date to a content id and a background the first
time it is chosen, and every later run for that date rebuilds the identical
creative.

This is not tidiness. `output/` is gitignored, so a cloud runner starts every
run with nothing on disk and regenerates from scratch — and each regeneration
picked a *different* quote. The duplicate guard keys on the content id, so a
later run had nothing to recognise and would post a second, different creative
the same day. The first live day did exactly this: run 1 posted `w036`, run 2
built `w008`. With Instagram failing separately the day never registered as
complete, so all three of the next day's runs would have posted their own.

Verified by deleting `output/` between three generate calls: all three
produced the same creative and the bank was charged one entry.

### 1.7 The ledger is committed on purpose

`state/state.json` is the cloud run's **only** memory of what it already
posted. It was gitignored once — inherited from an earlier posting-only
design — which would have made `git add` silently do nothing, the ledger never
commit, and **every run believe it was the first**. The `.gitignore` now says
why, and the workflow's add step **fails loudly** rather than being forced
past with `-f`.

---

## 2. Definition of done

Before telling the owner a day is fine, confirm all three. The repo is public,
so the GitHub REST API answers without a token.

```bash
git fetch && git log origin/main --format='%h %ad %an %s' --date=iso -6
#   "Update publish state [skip ci]" by "win-social bot" = a run wrote the ledger

curl -sS "https://api.github.com/repos/pixelmartllp/wincareer/actions/runs?per_page=8"
#   run_started_at tells you the real drift; conclusion tells you failure vs no-show

git show origin/main:state/state.json      # ok:true rows for both platforms
```

Pass = a bot ledger commit exists for the day, and `ok: true` rows exist for
both platforms. Anything else is a failure — say so plainly and name which
stage broke.

**A green run does not mean a post went out.** One green run the owner sent as
proof was a dry run; the real post was in the red run before it. The truth is
always the ledger.

Errors worth recognising:

- `OAuthException (code 190): Bad signature` — the token is wrong or revoked.
- `OAuthException (code 10): Application does not have permission` — on
  Instagram this is a **missing scope**, not a missing asset. Scopes are baked
  into a token when it is generated; assigning the Instagram account to the
  system user afterwards does **not** add them. The token has to be
  regenerated with `instagram_basic` and `instagram_content_publish`. This cost
  three days of failed Instagram posts, partly because the first diagnosis —
  that asset assignment would be enough — was wrong.
- `code 1: please reduce the amount of data` — a transient Graph flake, retry.

`Actions → Check credentials` runs `win_post.py verify` against the repo's own
secrets, read-only, and **names the missing scope**. Use it before concluding
anything about the cloud token: a token that works on this laptop proves
nothing about the copy in the secret.

---

## 3. How it works

```
content_bank.json ─┐
                   ├─> generate_day() ─> output/<date>/NN-<id>.jpg
assets/backgrounds ┘                     + batch.json (captions, status)
assets/mentor      ┘                            │
                                         publish_day() ─> Facebook, Instagram
                                                │
                                         state/state.json (ledger + plans)
```

Generation never posts and publishing never renders. A bad batch can be
regenerated with no risk of it going out; a failed post can be retried without
re-rendering.

`.github/workflows/daily.yml` calls `win_post.py daily`, which is idempotent:
it reads the ledger first and exits without posting if the day already went
out. That is what makes four scheduled runs safe.

It runs on **ubuntu, not windows** — every brand font ships in
`assets/fonts/`, and a guard step proves each role resolves inside the repo
rather than assuming it. Without that, a deleted font file would silently
render the brand in Arial.

```bash
.venv/Scripts/python.exe win_post.py generate           # renders, posts nothing
.venv/Scripts/python.exe win_post.py batch              # captions and status
.venv/Scripts/python.exe win_post.py publish            # dry run
.venv/Scripts/python.exe win_post.py publish --confirm  # live
.venv/Scripts/python.exe win_post.py publish --now --confirm   # skip both timing rules
.venv/Scripts/python.exe win_post.py daily --confirm    # what the workflow runs
.venv/Scripts/python.exe win_post.py history --failures
.venv/Scripts/python.exe win_post.py recent             # what Meta says is live
```

Use `.venv/Scripts/python.exe` from `D:\Shi` — bare `python` on this machine
hits the Microsoft Store stub. `gh` is **not installed**; use `curl` against
the public REST API.

**Console encoding:** `config.force_utf8()` is called by every entry point.
The console here is cp1252 and dies on emoji in captions and accents in
photographer names. It crashed twice before it was made shared.

---

## 4. Safety rules

- **Publishing is live.** Nothing posts without `--confirm` / `confirm=True`.
  Never pass it without the owner approving that specific post.
- **Never use `dry_run=False` in a test.** A guard test written against a
  future date did not match the ledger's date check and posted two real
  creatives to Facebook and Instagram. The owner chose to keep them; they are
  in the ledger with a note saying they came from a local test.
- **`config.json` holds live credentials** and is gitignored. Never commit it,
  never paste it anywhere. Same for the repo secrets.
- **The repo is public.** No credentials are in it, but the content bank, the
  code and the post history are visible. `sample/` was removed for this reason.
- **`state/state.json` is committed.** Never gitignore it, never hand-edit the
  ledger to make a day look successful — and never delete a real row.

---

## 5. Brand

Palette, fonts, canvas sizes and every piece of standing copy live in
`win_social/brand.py`. Change them there.

The palette is **not invented** — it was sampled from twelve of the Page's own
creatives by bucketing every pixel: navy `#0c243c`, orange `#fc540c`, ribbon
red `#cc0c24`, blue `#0c3c9c`.

Fonts are Anton (display), Oswald (display_alt) and Montserrat (body), all
OFL, all committed. Oswald and Montserrat are **variable** fonts — a role
wanting weight must select a named instance, or it silently renders Regular.
Check `brand.font_report()`.

Standing copy: `STRAPLINE` ("Upgrade your English, Upgrade your CAREER!" — the
tail is coloured orange, and an assert enforces that it really is a suffix),
`CATEGORY`, `OFFER`, `CALL_LINE`, `MENTOR`, `PHONE`. The QR resolves to
`https://wa.me/918837888293` — verified by decoding, not taken on trust.

Content bank: 72 entries across ten themes. New entries need a unique `id`, a
`theme` from that list, a short `headline` (3–6 words — it is set in Anton
caps), an `accent`, and a `caption`. Captions are assembled in `content.py`,
not stored per entry, so the phone number is one edit rather than seventy-two.

---

## 6. Where things stand (31 Aug 2026)

- **Live and posting daily**, both platforms, from the cloud with no local PC.
- Posted 26–30 Aug. 28 Aug was Facebook only — Instagram's scope was still
  missing. 29 and 30 Aug went to both.
- **64 of 72 content entries left** before the rotation repeats. Read the real
  number from `win_post.py bank` rather than trusting this line — a generate
  spends an entry the moment it pins a day, dry run or not.
- **Instagram works** as of 29 Aug, after the token was regenerated with the
  right scopes.
- **Post times are the open question.** The retimed crons went in on 31 Aug and
  have not yet had a day to prove themselves. Before that, posts landed at
  00:26, 13:29 and 14:56 IST. If they still land in the afternoon, measure the
  drift again from the run list and move the crons earlier.
- **One mentor photograph.** This is the biggest open item and only the owner
  can close it.
- `tools/` scripts live in the repo on purpose — the equivalent scripts for the
  sibling pipeline were written in a scratch folder, lost, and had to be
  rewritten.
