"""Tests for Web UI localization (i18n)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import Settings
from ui.aggregator import derive_ui_health
from ui.i18n import build_i18n_context, load_locale_messages, resolve_ui_lang

_I18N_DIR = Path(__file__).resolve().parents[1] / "app" / "ui" / "static" / "i18n"


def _flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys.update(_flatten_keys(value, path))
        else:
            keys.add(path)
    return keys


def test_settings_parse_supported_langs():
    settings = Settings(dns_debug_ui_supported_langs="ru,en,en")
    assert settings.dns_debug_ui_supported_langs == ["ru", "en"]


def test_resolve_ui_lang_defaults_to_configured():
    settings = Settings(dns_debug_ui_default_lang="ru", dns_debug_ui_supported_langs=["en", "ru"])
    assert resolve_ui_lang(settings) == "ru"


def test_resolve_ui_lang_query_param():
    settings = Settings(dns_debug_ui_default_lang="en", dns_debug_ui_supported_langs=["en", "ru"])
    assert resolve_ui_lang(settings, query_lang="ru") == "ru"


def test_resolve_ui_lang_invalid_falls_back():
    settings = Settings(dns_debug_ui_default_lang="en", dns_debug_ui_supported_langs=["en", "ru"])
    assert resolve_ui_lang(settings, query_lang="fr") == "en"


def test_locale_json_key_parity():
    en = load_locale_messages("en")
    ru = load_locale_messages("ru")
    en_keys = _flatten_keys(en)
    ru_keys = _flatten_keys(ru)
    missing_in_ru = en_keys - ru_keys
    missing_in_en = ru_keys - en_keys
    assert not missing_in_ru, f"RU missing keys: {sorted(missing_in_ru)[:10]}"
    assert not missing_in_en, f"EN missing keys: {sorted(missing_in_en)[:10]}"


def test_locale_files_valid_json_on_disk():
    for lang in ("en", "ru"):
        path = _I18N_DIR / f"{lang}.json"
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "dashboard" in data


def test_build_i18n_context_disabled():
    settings = Settings(dns_debug_ui_i18n_enabled=False)
    ctx = build_i18n_context(settings)
    assert ctx["enabled"] is False
    assert ctx["lang"] == "en"
    assert "messages" in ctx


def test_build_i18n_context_page_title():
    settings = Settings(dns_debug_ui_i18n_enabled=True, dns_debug_ui_default_lang="en")
    ctx = build_i18n_context(settings, query_lang="ru")
    assert ctx["lang"] == "ru"
    assert ctx["pageTitle"]
    assert ctx["messages"]["dashboard"]["page_title"]


def test_derive_ui_health_signal_params_additive():
    settings = Settings()
    health = derive_ui_health(
        settings,
        total_queries=100,
        error_count=10,
        success_ratio=0.9,
        p95_ms=150.0,
        garbage_ratio=0.2,
        error_qps=6.0,
        mtr_verdict="ok",
        mtr_enabled=False,
    )
    assert health["level"] in ("ok", "degraded", "critical")
    for signal in health["signals"]:
        assert "code" in signal
        assert "message" in signal
        if signal["code"] in ("high_error_rate", "elevated_errors", "latency_p95_high", "noisy_ratio_high", "error_storm"):
            assert "params" in signal


def test_dashboard_template_data_i18n_keys_exist():
    """Static keys used in dashboard.html should exist in locale bundles."""
    template_path = Path(__file__).resolve().parents[1] / "app" / "ui" / "templates" / "dashboard.html"
    html = template_path.read_text(encoding="utf-8")
    import re

    keys = set(re.findall(r'data-i18n(?:-title|-placeholder|-aria-label)?="([^"]+)"', html))
    en = load_locale_messages("en")

    def has_key(bundle: dict, key: str) -> bool:
        node = bundle
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    missing = [k for k in keys if not has_key(en, k)]
    assert not missing, f"Template keys missing from en.json: {missing}"
