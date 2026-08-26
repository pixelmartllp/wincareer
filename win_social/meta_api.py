"""Meta Graph API client for the Win Career Academy Page.

Covers what this brand actually posts: plain text, a link, one photo, several
photos, and any of those scheduled for later. Instagram takes the same photo
when an ig_user_id is configured.

Instagram will not accept a file upload - it needs a publicly reachable URL.
Rather than adding a third-party image host, the photo goes up to the
Facebook Page as an *unpublished, temporary* photo and the CDN URL Meta hands
back feeds the Instagram container. Everything stays inside Meta.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .config import (DEFAULT_API_VERSION, REQUIRED_KEYS, ROOT, ConfigError,
                     load_config)

GRAPH = "https://graph.facebook.com"

# Instagram rejects containers that never finish processing; cap the wait.
IG_POLL_ATTEMPTS = 30
IG_POLL_SECONDS = 3

# Facebook's own bounds for scheduled_publish_time.
SCHEDULE_MIN = timedelta(minutes=10)
SCHEDULE_MAX = timedelta(days=180)

IST = timezone(timedelta(hours=5, minutes=30))

MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".png": "image/png", ".webp": "image/webp"}


class MetaAPIError(RuntimeError):
    """A Graph call failed. Carries the message Meta actually returned."""

    def __init__(self, message: str, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.payload = payload or {}


def parse_schedule(when: str) -> int:
    """Turn a human time into the unix seconds Graph wants.

    Accepts a unix timestamp, an ISO time with an explicit offset, or a bare
    "YYYY-MM-DD HH:MM" which is read as IST - the owner and the audience are
    both in India, and a bare time meaning UTC would silently post 5.5 hours
    early.
    """
    text = str(when).strip()
    if not text:
        raise ValueError("No schedule time given.")

    if text.isdigit():
        stamp = datetime.fromtimestamp(int(text), tz=timezone.utc)
    else:
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Could not read {text!r} as a time. Use "
                f"YYYY-MM-DD HH:MM (IST) or a full ISO timestamp."
            ) from exc
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)

    delta = stamp - datetime.now(tz=timezone.utc)
    if delta < SCHEDULE_MIN:
        raise ValueError(
            f"{stamp.astimezone(IST):%Y-%m-%d %H:%M} IST is less than 10 "
            f"minutes away. Facebook rejects that - pick a later time, or "
            f"post it now instead of scheduling."
        )
    if delta > SCHEDULE_MAX:
        raise ValueError("Facebook will not schedule more than 180 days out.")
    return int(stamp.timestamp())


def describe_time(unix_seconds: int | str) -> str:
    return (datetime.fromtimestamp(int(unix_seconds), tz=IST)
            .strftime("%Y-%m-%d %H:%M IST"))


class GraphClient:
    def __init__(self, config: dict[str, Any] | None = None,
                 timeout: int = 120):
        self.config = config or load_config()
        self.timeout = timeout
        missing = [k for k in REQUIRED_KEYS if not self.config.get(k)]
        if missing:
            raise ConfigError(
                f"Missing Meta credentials for Win Career Academy: "
                f"{', '.join(missing)}. Fill them into config.json or set the "
                f"WIN_META_* environment variables - see README.md."
            )
        self.token = str(self.config["access_token"])
        self.page_id = str(self.config["page_id"])
        self.ig_user_id = str(self.config.get("ig_user_id") or "")
        self.base = f"{GRAPH}/{self.config.get('api_version', DEFAULT_API_VERSION)}"
        self._page_token: str | None = None

    # -- plumbing ---------------------------------------------------------

    @property
    def page_token(self) -> str:
        """The token to publish with - always the Page's own.

        Content has to be created *as the Page*. A user or system-user token
        fails with a misleading publish_actions-is-deprecated error, so the
        configured token is exchanged for the Page token once and cached.
        """
        if self._page_token is None:
            try:
                response = self._request("GET", self.page_id,
                                         params={"fields": "access_token"})
                self._page_token = response.get("access_token") or self.token
            except MetaAPIError:
                # Already a Page token, or the field is not exposed - the
                # configured token is the best we have.
                self._page_token = self.token
        return self._page_token

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 data: dict | None = None, files: dict | None = None,
                 as_page: bool = False) -> dict:
        url = f"{self.base}/{path.lstrip('/')}"
        params = dict(params or {})
        params.setdefault("access_token",
                          self.page_token if as_page else self.token)

        try:
            response = requests.request(method, url, params=params, data=data,
                                        files=files, timeout=self.timeout)
        except requests.RequestException as exc:
            raise MetaAPIError(f"Network error calling {path}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            raise MetaAPIError(
                f"Graph API returned non-JSON (HTTP {response.status_code}) "
                f"for {path}: {response.text[:300]}"
            ) from None

        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            raise MetaAPIError(
                f"{err.get('type', 'GraphError')} "
                f"(code {err.get('code')}): {err.get('message')}", payload)
        if not response.ok:
            raise MetaAPIError(
                f"HTTP {response.status_code} for {path}: {payload}",
                payload if isinstance(payload, dict) else None)
        return payload

    def get(self, path: str, **params) -> dict:
        return self._request("GET", path, params=params)

    # -- diagnostics ------------------------------------------------------

    def verify(self) -> dict[str, Any]:
        """Check the token, the Page and any linked Instagram account."""
        result: dict[str, Any] = {"ok": True, "checks": {}, "problems": []}

        try:
            result["checks"]["page"] = self.get(
                self.page_id, fields="id,name,link,fan_count,username")
        except MetaAPIError as exc:
            result["ok"] = False
            result["problems"].append(f"Page check failed: {exc}")

        try:
            data = self.get("debug_token", input_token=self.token).get("data", {})
            expires = data.get("expires_at")
            result["checks"]["token"] = {
                "type": data.get("type"),
                "app_id": data.get("app_id"),
                "is_valid": data.get("is_valid"),
                "expires_at": describe_time(expires) if expires else None,
                "never_expires": expires == 0,
                "scopes": data.get("scopes"),
            }
            if not data.get("is_valid"):
                result["ok"] = False
                result["problems"].append("Access token is not valid.")
            for scope in ("pages_manage_posts", "pages_read_engagement"):
                if scope not in (data.get("scopes") or []):
                    result["problems"].append(f"Token is missing scope: {scope}")
        except MetaAPIError as exc:
            result["problems"].append(f"Token introspection failed: {exc}")

        if self.ig_user_id:
            try:
                result["checks"]["instagram"] = self.get(
                    self.ig_user_id,
                    fields="id,username,followers_count,media_count")
            except MetaAPIError as exc:
                result["ok"] = False
                result["problems"].append(f"Instagram check failed: {exc}")
        else:
            result["problems"].append(
                "No ig_user_id configured - Instagram posting is disabled.")

        return result

    def discover(self) -> dict[str, Any]:
        """Pages this token can manage, with their linked IG accounts."""
        pages = self.get(
            "me/accounts",
            fields="id,name,link,instagram_business_account{id,username}")
        return {"pages": [{
            "page_id": p.get("id"),
            "page_name": p.get("name"),
            "page_link": p.get("link"),
            "ig_user_id": (p.get("instagram_business_account") or {}).get("id"),
            "ig_username": (p.get("instagram_business_account") or {}).get("username"),
        } for p in pages.get("data", [])]}

    # -- facebook publishing ----------------------------------------------

    def post_text(self, message: str, link: str = "",
                  schedule_unix: int | None = None) -> dict[str, Any]:
        """Post to the Page feed - text, or text plus a link preview."""
        if not message.strip() and not link:
            raise ValueError("A text post needs a message (or a link).")
        data: dict[str, Any] = {"message": message}
        if link:
            data["link"] = link
        data.update(self._schedule_fields(schedule_unix))
        response = self._request("POST", f"{self.page_id}/feed", data=data,
                                 as_page=True)
        return self._decorate(response, schedule_unix)

    def post_photo(self, image_path: Path, message: str,
                   schedule_unix: int | None = None) -> dict[str, Any]:
        """Upload one photo to the Page feed."""
        image_path = self._checked_image(image_path)
        data: dict[str, Any] = {"message": message}
        data.update(self._schedule_fields(schedule_unix))
        with open(image_path, "rb") as handle:
            files = {"source": (image_path.name, handle,
                                self._mime(image_path))}
            response = self._request("POST", f"{self.page_id}/photos",
                                     data=data, files=files, as_page=True)
        return self._decorate(response, schedule_unix)

    def post_photos(self, image_paths: list[Path], message: str,
                    schedule_unix: int | None = None) -> dict[str, Any]:
        """Multi-photo post: upload each unpublished, attach them to one story."""
        media_ids = []
        for path in image_paths:
            uploaded = self._upload_unpublished(self._checked_image(path))
            if not uploaded.get("id"):
                raise MetaAPIError(f"Upload of {path} returned no id.")
            media_ids.append(uploaded["id"])

        data: dict[str, Any] = {"message": message}
        for i, media_id in enumerate(media_ids):
            data[f"attached_media[{i}]"] = json.dumps({"media_fbid": media_id})
        data.update(self._schedule_fields(schedule_unix))
        response = self._request("POST", f"{self.page_id}/feed", data=data,
                                 as_page=True)
        response["photo_ids"] = media_ids
        return self._decorate(response, schedule_unix)

    @staticmethod
    def _schedule_fields(schedule_unix: int | None) -> dict[str, Any]:
        if schedule_unix is None:
            return {}
        return {"published": "false", "scheduled_publish_time": schedule_unix}

    @staticmethod
    def _decorate(response: dict[str, Any],
                  schedule_unix: int | None) -> dict[str, Any]:
        """Add the permalink, or the scheduled time, to a raw Graph response."""
        result = dict(response)
        post_id = response.get("post_id") or response.get("id")
        result["post_id"] = post_id
        if schedule_unix is not None:
            result["scheduled_for"] = describe_time(schedule_unix)
            result["scheduled_publish_time"] = schedule_unix
            result["published"] = False
        else:
            result["published"] = True
            if post_id:
                result["permalink"] = f"https://www.facebook.com/{post_id}"
        return result

    # -- scheduled queue ---------------------------------------------------

    def scheduled_posts(self, limit: int = 25) -> dict[str, Any]:
        """Everything sitting in the Page's scheduled queue.

        /scheduled_posts is the documented edge but is not exposed on every
        Page; /promotable_posts filtered to unpublished is the fallback.
        """
        fields = "id,message,created_time,scheduled_publish_time,is_published"
        try:
            payload = self._request(
                "GET", f"{self.page_id}/scheduled_posts",
                params={"fields": fields, "limit": limit}, as_page=True)
        except MetaAPIError:
            payload = self._request(
                "GET", f"{self.page_id}/promotable_posts",
                params={"fields": fields, "limit": limit,
                        "is_published": "false"}, as_page=True)

        posts = []
        for post in payload.get("data", []):
            when = post.get("scheduled_publish_time")
            posts.append({
                "post_id": post.get("id"),
                "message": (post.get("message") or "")[:200],
                "scheduled_for": describe_time(when) if when else None,
                "is_published": post.get("is_published"),
            })
        return {"scheduled": posts, "count": len(posts)}

    def publish_now(self, post_id: str) -> dict[str, Any]:
        """Release a scheduled post immediately."""
        return self._request("POST", post_id, data={"is_published": "true"},
                             as_page=True)

    def reschedule(self, post_id: str, schedule_unix: int) -> dict[str, Any]:
        return self._request("POST", post_id,
                             data={"scheduled_publish_time": schedule_unix},
                             as_page=True)

    def delete_object(self, object_id: str) -> dict[str, Any]:
        return self._request("DELETE", object_id, as_page=True)

    def try_delete(self, object_id: str) -> bool:
        try:
            self.delete_object(object_id)
            return True
        except MetaAPIError:
            return False

    # -- instagram ---------------------------------------------------------

    def post_instagram_photo(self, caption: str,
                             image_path: Path | None = None,
                             image_url: str | None = None) -> dict[str, Any]:
        """Create an Instagram container from a photo and publish it."""
        if not self.ig_user_id:
            raise ConfigError(
                "ig_user_id is not configured - cannot post to Instagram. "
                "Run discover_meta_accounts to find it.")

        scratch_photo_id: str | None = None
        if image_url is None:
            if image_path is None:
                raise ValueError("Provide either image_path or image_url.")
            image_url, scratch_photo_id = self.host_image(Path(image_path))

        try:
            container = self._request(
                "POST", f"{self.ig_user_id}/media",
                data={"image_url": image_url, "caption": caption},
                as_page=True)
            creation_id = container.get("id")
            if not creation_id:
                raise MetaAPIError(f"No container id returned: {container}")

            self._await_container(creation_id)
            published = self._request(
                "POST", f"{self.ig_user_id}/media_publish",
                data={"creation_id": creation_id}, as_page=True)
            media_id = published.get("id")
            return {
                "media_id": media_id,
                "creation_id": creation_id,
                "permalink": self._ig_permalink(media_id),
                "published": True,
            }
        finally:
            if scratch_photo_id:
                self.try_delete(scratch_photo_id)

    def _ig_permalink(self, media_id: str | None) -> str | None:
        if not media_id:
            return None
        try:
            return self._request("GET", media_id,
                                 params={"fields": "permalink"},
                                 as_page=True).get("permalink")
        except MetaAPIError:
            return None

    def _await_container(self, creation_id: str) -> None:
        last = "UNKNOWN"
        for _ in range(IG_POLL_ATTEMPTS):
            status = self._request("GET", creation_id,
                                   params={"fields": "status_code,status"},
                                   as_page=True)
            last = status.get("status_code", "UNKNOWN")
            if last == "FINISHED":
                return
            if last in ("ERROR", "EXPIRED"):
                raise MetaAPIError(
                    f"Instagram container {creation_id} failed with "
                    f"{last}: {status.get('status')}")
            time.sleep(IG_POLL_SECONDS)
        raise MetaAPIError(
            f"Instagram container {creation_id} still {last} after "
            f"{IG_POLL_ATTEMPTS * IG_POLL_SECONDS}s.")

    # -- image hosting -----------------------------------------------------

    def _upload_unpublished(self, image_path: Path) -> dict[str, Any]:
        with open(image_path, "rb") as handle:
            files = {"source": (image_path.name, handle,
                                self._mime(image_path))}
            return self._request("POST", f"{self.page_id}/photos",
                                 data={"published": "false"}, files=files,
                                 as_page=True)

    def host_image(self, image_path: Path) -> tuple[str, str]:
        """A public URL for a local image, via a temporary Page photo.

        Returns (public_url, photo_id) so the caller can delete it after.
        """
        image_path = self._checked_image(image_path)
        with open(image_path, "rb") as handle:
            files = {"source": (image_path.name, handle,
                                self._mime(image_path))}
            response = self._request(
                "POST", f"{self.page_id}/photos",
                data={"published": "false", "temporary": "true"},
                files=files, as_page=True)
        photo_id = response.get("id")
        if not photo_id:
            raise MetaAPIError(f"Unpublished upload returned no id: {response}")
        return self.get_photo_url(photo_id), photo_id

    def get_photo_url(self, photo_id: str) -> str:
        info = self._request("GET", photo_id, params={"fields": "images"},
                             as_page=True)
        images = info.get("images") or []
        if not images:
            raise MetaAPIError(f"Photo {photo_id} has no image URLs yet.")
        best = max(images, key=lambda i: i.get("width", 0) * i.get("height", 0))
        url = best.get("source")
        if not url:
            raise MetaAPIError(f"Photo {photo_id} returned no source URL.")
        return url

    @staticmethod
    def _checked_image(image_path: Path | str) -> Path:
        """Resolve an image path, allowing one relative to the project root."""
        path = Path(image_path).expanduser()
        if not path.is_absolute() and not path.is_file():
            candidate = ROOT / path
            if candidate.is_file():
                path = candidate
        if not path.is_file():
            raise MetaAPIError(f"Image not found: {path}")
        if path.suffix.lower() not in MIME_BY_SUFFIX:
            raise MetaAPIError(
                f"{path.name}: Meta accepts jpg, png or webp, not "
                f"{path.suffix or 'a file with no extension'}.")
        return path

    @staticmethod
    def _mime(path: Path) -> str:
        return MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")

    # -- reading -----------------------------------------------------------

    def recent_posts(self, limit: int = 10) -> dict[str, Any]:
        payload = self._request(
            "GET", f"{self.page_id}/posts",
            params={"fields": "id,message,created_time,permalink_url",
                    "limit": limit}, as_page=True)
        return {"posts": [{
            "post_id": p.get("id"),
            "created_time": p.get("created_time"),
            "message": (p.get("message") or "")[:300],
            "permalink": p.get("permalink_url"),
        } for p in payload.get("data", [])]}


# --------------------------------------------------------------------------
# Token helpers
# --------------------------------------------------------------------------

def exchange_for_long_lived(app_id: str, app_secret: str,
                            short_lived_token: str,
                            api_version: str = DEFAULT_API_VERSION) -> dict[str, Any]:
    """Turn a ~1 hour user token into a ~60 day one."""
    response = requests.get(
        f"{GRAPH}/{api_version}/oauth/access_token", timeout=60,
        params={"grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_lived_token})
    payload = response.json()
    if "error" in payload:
        raise MetaAPIError(payload["error"].get("message", str(payload)),
                           payload)
    return payload


def page_token_from_user_token(user_token: str, page_id: str,
                               api_version: str = DEFAULT_API_VERSION) -> str:
    """Fetch the Page access token.

    Derived from a long-lived user token it does not expire, which is what
    makes unattended posting possible.
    """
    response = requests.get(f"{GRAPH}/{api_version}/me/accounts", timeout=60,
                            params={"access_token": user_token, "limit": 100})
    payload = response.json()
    if "error" in payload:
        raise MetaAPIError(payload["error"].get("message", str(payload)),
                           payload)
    for page in payload.get("data", []):
        if str(page.get("id")) == str(page_id):
            return page["access_token"]
    names = [f"{p.get('name')} ({p.get('id')})" for p in payload.get("data", [])]
    raise MetaAPIError(
        f"Page {page_id} not found in this user's Pages. "
        f"Available: {names or 'none'}")
