"""Credentials and paths for the Win Career Academy poster.

config.json is the source of truth on this machine; WIN_META_* environment
variables fill the gaps so the same code runs in CI, where there is no
config.json to read.

The environment names are prefixed WIN_ on purpose. The sibling Shashi
Pallava pipeline on this machine reads the generic META_* names, and an
unprefixed variable set user-wide would let one brand's credentials publish
to the other brand's Page.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "state" / "state.json"
MEDIA_DIR = ROOT / "media"

DEFAULT_API_VERSION = "v25.0"
PAGE_URL = "https://www.facebook.com/TheWinCareer"
BRAND_NAME = "Win Career Academy"

REQUIRED_KEYS = ("page_id", "access_token")

ENV_MAP = {
    "app_id": "WIN_META_APP_ID",
    "app_secret": "WIN_META_APP_SECRET",
    "page_id": "WIN_META_PAGE_ID",
    "ig_user_id": "WIN_META_IG_USER_ID",
    "access_token": "WIN_META_ACCESS_TOKEN",
    "api_version": "WIN_META_API_VERSION",
    # Only used when topping up the background pool, never by a posting run.
    "pexels_api_key": "PEXELS_API_KEY",
}


class ConfigError(RuntimeError):
    pass


def force_utf8() -> None:
    """Print UTF-8 whatever the console codepage is.

    Every entry point needs this. Captions carry emoji and Pexels
    photographer names carry accents; the console here defaults to cp1252,
    which cannot encode either, and the process dies on the print rather than
    on anything that matters.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _is_placeholder(value: Any) -> bool:
    """Treat an untouched template value as missing.

    Otherwise copying config.example.json makes setup look finished and the
    first call fails with a confusing token error instead of "not filled in".
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return not stripped or stripped.startswith("YOUR_")


def load_config() -> dict[str, Any]:
    config: dict[str, Any] = {}
    if CONFIG_FILE.is_file():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config.json is not valid JSON: {exc}") from exc

    config = {k: v for k, v in config.items()
              if not str(k).startswith("_") and not _is_placeholder(v)}

    for key, env in ENV_MAP.items():
        if config.get(key):
            continue
        value = os.environ.get(env)
        if value and not _is_placeholder(value):
            config[key] = value

    config.setdefault("api_version", DEFAULT_API_VERSION)
    return config


def config_status() -> dict[str, Any]:
    """What is configured - without ever echoing the token back."""
    config = load_config()
    token = str(config.get("access_token") or "")
    ready_fb = all(config.get(k) for k in REQUIRED_KEYS)
    return {
        "brand": BRAND_NAME,
        "page_url": PAGE_URL,
        "config_file": str(CONFIG_FILE),
        "config_file_exists": CONFIG_FILE.is_file(),
        "app_id_set": bool(config.get("app_id")),
        "app_secret_set": bool(config.get("app_secret")),
        "page_id": config.get("page_id") or None,
        "ig_user_id": config.get("ig_user_id") or None,
        "access_token_set": bool(token),
        "access_token_preview": (token[:6] + "..." + token[-4:])
                                if len(token) > 12 else None,
        "api_version": config.get("api_version"),
        "ready_for_facebook": ready_fb,
        "ready_for_instagram": ready_fb and bool(config.get("ig_user_id")),
        "pexels_key_set": bool(config.get("pexels_api_key")),
    }


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge values into config.json, creating it if needed."""
    existing: dict[str, Any] = {}
    if CONFIG_FILE.is_file():
        existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    existing.update({k: v for k, v in updates.items() if v not in (None, "")})
    CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return config_status()
