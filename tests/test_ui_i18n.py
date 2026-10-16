"""Chinese/English UI localization: catalog integrity and server rendering."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.config.configuration import PROJECT_ROOT
from app.transport import ui_i18n
from app.transport.admin import create_admin_routes

LOCALES_DIR = PROJECT_ROOT / "web" / "static" / "locales"
INDEX_PATH = PROJECT_ROOT / "web" / "index.html"
MARKER_ATTRS = ("data-i18n", "data-i18n-placeholder", "data-i18n-title", "data-i18n-aria-label")


def _catalog(locale):
    return json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def _placeholders(message):
    return set(re.findall(r"\{(\w+)\}", message))


def _marked_keys():
    html = INDEX_PATH.read_text(encoding="utf-8")
    keys = set()
    for attr in MARKER_ATTRS:
        keys |= set(re.findall(rf'{attr}="([^"]+)"', html))
    return keys


def test_catalogs_have_identical_keys_and_placeholders():
    zh, en = _catalog("zh-CN"), _catalog("en")
    assert zh and en
    assert set(zh) == set(en), (sorted(set(zh) - set(en)), sorted(set(en) - set(zh)))
    for key in zh:
        assert _placeholders(zh[key]) == _placeholders(en[key]), key


def test_every_marked_key_exists_in_both_catalogs():
    zh, en = _catalog("zh-CN"), _catalog("en")
    marked = _marked_keys()
    assert marked, "index.html declares no translation markers"
    for key in sorted(marked):
        assert key in zh, f"missing zh resource: {key}"
        assert key in en, f"missing en resource: {key}"


@pytest.mark.parametrize("locale", ["zh-CN", "en"])
def test_render_localized_sets_lang_and_injects_catalog(locale):
    html = INDEX_PATH.read_text(encoding="utf-8")
    out = ui_i18n.render_localized_html(html, locale)
    assert f'<html lang="{locale}"' in out
    match = re.search(
        r'<script type="application/json" id="i18n-catalog"[^>]*>(.*?)</script>', out, re.S)
    assert match, "catalog node missing"
    payload = json.loads(match.group(1))
    assert payload["locale"] == locale
    assert payload["messages"]


def test_english_payload_includes_chinese_fallback():
    html = INDEX_PATH.read_text(encoding="utf-8")
    pattern = r'id="i18n-catalog"[^>]*>(.*?)</script>'
    en_payload = json.loads(re.search(pattern, ui_i18n.render_localized_html(html, "en"), re.S).group(1))
    assert en_payload["fallback"] == "zh-CN"
    assert en_payload["fallbackMessages"]["nav.memories"] == "记忆"
    # The default locale does not duplicate its own messages as a fallback.
    zh_payload = json.loads(re.search(pattern, ui_i18n.render_localized_html(html, "zh-CN"), re.S).group(1))
    assert "fallbackMessages" not in zh_payload


def test_unknown_locale_falls_back_to_default():
    out = ui_i18n.render_localized_html('<html lang="zh-CN"><body></body></html>', "fr")
    assert '<html lang="zh-CN"' in out
    assert 'data-locale="zh-CN"' in out


def test_catalog_injection_escapes_script_breakout():
    payload = {"locale": "en", "messages": {"x": "</script><script>alert(1)</script>"}}
    encoded = ui_i18n._json_for_script(payload)
    assert "</script" not in encoded
    assert json.loads(encoded)["messages"]["x"] == "</script><script>alert(1)</script>"


def test_localized_text_does_not_double_interpolate():
    # A caller-provided value containing a placeholder must not be re-expanded.
    out = ui_i18n._json_for_script(
        {"locale": "en", "messages": {"a": "{b}", "b": "value"}})
    assert json.loads(out)["messages"]["a"] == "{b}"


def test_console_route_localizes_html(admin, monkeypatch):
    client = TestClient(Starlette(routes=create_admin_routes(admin.service, debug=False)))
    zh = client.get("/admin/console")
    assert zh.status_code == 200
    assert '<html lang="zh-CN"' in zh.text
    assert 'id="i18n-catalog"' in zh.text
    assert zh.headers["cache-control"] == "no-store"

    monkeypatch.setattr(admin.service, "settings", SimpleNamespace(LANGUAGE="en"))
    en = client.get("/admin/console")
    assert '<html lang="en"' in en.text
