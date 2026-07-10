"""UI localization helpers — load locale JSON bundles for server-side injection."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import Settings

_I18N_DIR = Path(__file__).resolve().parent / "static" / "i18n"
_STORAGE_KEY = "dns-debug-lang"


def _normalize_lang(code: str) -> str:
    return (code or "en").strip().lower().split("-")[0]


def resolve_ui_lang(settings: Settings, query_lang: str | None = None) -> str:
    """Pick active UI language from query param, then configured default."""
    supported = {_normalize_lang(x) for x in settings.dns_debug_ui_supported_langs}
    if not supported:
        supported = {"en"}

    if query_lang:
        candidate = _normalize_lang(query_lang)
        if candidate in supported:
            return candidate

    default = _normalize_lang(settings.dns_debug_ui_default_lang)
    if default in supported:
        return default
    return "en" if "en" in supported else next(iter(sorted(supported)))


@lru_cache(maxsize=8)
def load_locale_messages(lang: str) -> dict[str, Any]:
    code = _normalize_lang(lang)
    path = _I18N_DIR / f"{code}.json"
    if not path.is_file():
        path = _I18N_DIR / "en.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_i18n_context(settings: Settings, query_lang: str | None = None) -> dict[str, Any]:
    enabled = settings.dns_debug_ui_i18n_enabled
    supported = [_normalize_lang(x) for x in settings.dns_debug_ui_supported_langs] or ["en"]
    if "en" not in supported:
        supported = ["en", *supported]
    supported = list(dict.fromkeys(supported))

    default_lang = resolve_ui_lang(settings, None)
    lang = resolve_ui_lang(settings, query_lang) if enabled else "en"
    messages = load_locale_messages(lang) if enabled else load_locale_messages("en")

    page_title = _lookup(messages, "dashboard.page_title") or "DNS Debug Dashboard"

    return {
        "enabled": enabled,
        "defaultLang": default_lang,
        "supportedLangs": supported,
        "localeStorage": settings.dns_debug_ui_locale_storage_enabled,
        "storageKey": _STORAGE_KEY,
        "lang": lang,
        "messages": messages,
        "pageTitle": page_title,
    }


def _lookup(messages: dict[str, Any], key: str) -> str | None:
    node: Any = messages
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return str(node) if node is not None else None
