"""Server-side localization of the fixed admin console template.

Only the static ``web/index.html`` template is translated here, using explicit
``data-i18n*`` markers. User content, dynamic messages rendered by the frontend
modules, and machine-facing payloads are never touched. Translation resources
live in ``web/static/locales/<locale>.json`` and are also injected into the page
as a non-executing JSON node for the frontend ``i18n.js`` module.
"""
import json
import re
from functools import lru_cache
from html.parser import HTMLParser

from app.config.configuration import PROJECT_ROOT

SUPPORTED_LOCALES = ("zh-CN", "en")
DEFAULT_LOCALE = "zh-CN"
LOCALES_DIR = PROJECT_ROOT / "web" / "static" / "locales"
CATALOG_ELEMENT_ID = "i18n-catalog"

# Marker -> destination attribute for attribute translation.
_ATTR_MARKERS = (
    ("data-i18n-placeholder", "placeholder"),
    ("data-i18n-title", "title"),
    ("data-i18n-aria-label", "aria-label"),
)


def normalize_locale(locale):
    """Return a supported locale, falling back to the default."""
    return locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def load_catalog(locale):
    """Load one locale catalog. Missing or invalid files yield an empty dict."""
    path = LOCALES_DIR / f"{normalize_locale(locale)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def _lookup(catalog, key, fallback):
    value = catalog.get(key)
    if value is None:
        value = fallback.get(key)
    return key if value is None else value


def _set_attr(raw_start_tag, attr, value):
    """Set/replace a double-quoted attribute inside a raw start tag string."""
    pattern = re.compile(r'(\s' + re.escape(attr) + r'\s*=\s*)"[^"]*"')
    escaped = value.replace('"', "&quot;")
    if pattern.search(raw_start_tag):
        return pattern.sub(
            lambda m: m.group(1) + '"' + escaped + '"', raw_start_tag, count=1)
    close = raw_start_tag.rfind(">")
    if close == -1:
        return raw_start_tag
    head = raw_start_tag[:close].rstrip()
    if head.endswith("/"):
        head = head[:-1].rstrip()
        return f'{head} {attr}="{escaped}" />'
    return f'{head} {attr}="{escaped}">'


class _TemplateTranslator(HTMLParser):
    """Rewrites ``data-i18n*`` markers while preserving the original markup."""

    _ATTR_NAMES = {marker for marker, _ in _ATTR_MARKERS}

    def __init__(self, catalog, fallback):
        super().__init__(convert_charrefs=False)
        self.catalog = catalog
        self.fallback = fallback
        self.out = []
        self.stack = []

    def _translate(self, key):
        return _lookup(self.catalog, key, self.fallback)

    def _localize_start(self, tag, attrs):
        raw = self.get_starttag_text() or f"<{tag}>"
        attrd = {name: (value if value is not None else "") for name, value in attrs}
        if tag == "html":
            raw = _set_attr(raw, "lang", normalize_locale(self.catalog.get("_locale")))
        for marker, attr in _ATTR_MARKERS:
            key = attrd.get(marker)
            if key:
                raw = _set_attr(raw, attr, self._translate(key))
        return raw, attrd.get("data-i18n")

    def handle_starttag(self, tag, attrs):
        raw, key = self._localize_start(tag, attrs)
        self.stack.append({"tag": tag, "key": key})
        self.out.append(raw)

    def handle_startendtag(self, tag, attrs):
        raw, _ = self._localize_start(tag, attrs)
        self.out.append(raw)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.stack and self.stack[-1]["key"] and data.strip():
            self.out.append(self._translate(self.stack[-1]["key"]))
        else:
            self.out.append(data)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")


def _json_for_script(payload):
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Escaping '<' as a JSON unicode escape keeps the payload valid JSON while
    # preventing premature </script> termination and HTML comment smuggling.
    return (text.replace("<", "\\u003c")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))


def render_localized_html(html, locale):
    """Translate the fixed template and inject the catalog + ``<html lang>``."""
    resolved = normalize_locale(locale)
    catalog = load_catalog(resolved)
    fallback = load_catalog(DEFAULT_LOCALE)
    # _locale is consumed by the translator for <html lang>; not emitted to JS.
    translator = _TemplateTranslator({**catalog, "_locale": resolved}, fallback)
    translator.feed(html)
    translator.close()
    output = "".join(translator.out)

    payload = {"locale": resolved, "messages": catalog, "fallback": DEFAULT_LOCALE}
    if resolved != DEFAULT_LOCALE and fallback:
        # Frontend t() falls back to the default locale before showing the key.
        payload["fallbackMessages"] = fallback
    node = (
        f'<script type="application/json" id="{CATALOG_ELEMENT_ID}" '
        f'data-locale="{resolved}">{_json_for_script(payload)}</script>'
    )
    if "</body>" in output:
        output = output.replace("</body>", node + "\n</body>", 1)
    else:
        output += node
    return output
