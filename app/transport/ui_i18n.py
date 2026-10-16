"""Server-side localization of the fixed admin console template.

Only the static ``web/index.html`` template is translated here, using explicit
``data-i18n*`` markers. User content, dynamic messages rendered by the frontend
modules, and machine-facing payloads are never touched. Translation resources
live in ``web/static/locales/<locale>.json`` and are also injected into the page
as a non-executing JSON node for the frontend ``i18n.js`` module.

Marker contract:

* ``data-i18n`` marks an element whose **text content** is replaced. The element
  must contain text only; a child element is a template error (see
  ``TemplateMarkerError``). Empty containers are filled with the translation.
* ``data-i18n-placeholder`` / ``data-i18n-title`` / ``data-i18n-aria-label``
  replace the named attribute; the element may contain anything.

Translated values are escaped for their destination context so catalog text is
always shown literally, never interpreted as markup or entities.
"""
import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser

from app.config.configuration import PROJECT_ROOT

SUPPORTED_LOCALES = ("zh-CN", "en")
DEFAULT_LOCALE = "zh-CN"
LOCALES_DIR = PROJECT_ROOT / "web" / "static" / "locales"
CATALOG_ELEMENT_ID = "i18n-catalog"

TEXT_MARKER = "data-i18n"

# Void elements never have separate end tags, so they must not open a frame.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

# Marker -> destination attribute for attribute translation.
_ATTR_MARKERS = (
    ("data-i18n-placeholder", "placeholder"),
    ("data-i18n-title", "title"),
    ("data-i18n-aria-label", "aria-label"),
)


class TemplateMarkerError(ValueError):
    """Raised when a ``data-i18n`` marker cannot be applied unambiguously."""


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


def _escape_text(value):
    return (value.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))


def _escape_attr(value):
    return (value.replace("&", "&amp;")
                 .replace('"', "&quot;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))


def _set_attr(raw_start_tag, attr, value):
    """Set/replace a double-quoted attribute inside a raw start tag string."""
    pattern = re.compile(r'(\s' + re.escape(attr) + r'\s*=\s*)"[^"]*"')
    escaped = _escape_attr(value)
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

    def __init__(self, catalog, fallback, locale):
        super().__init__(convert_charrefs=False)
        self.catalog = catalog
        self.fallback = fallback
        self.locale = locale
        self.out = []
        # Open non-void elements. Each frame keeps its tag and, for a text
        # marker, the decoded text collected so far.
        self.stack = []
        self.marked = None  # innermost frame whose text is being replaced

    def _translate(self, key):
        return _lookup(self.catalog, key, self.fallback)

    def _localize_start(self, tag, attrs):
        raw = self.get_starttag_text() or f"<{tag}>"
        attrd = {name: (value if value is not None else "") for name, value in attrs}
        if tag == "html":
            raw = _set_attr(raw, "lang", normalize_locale(self.locale))
        for marker, attr in _ATTR_MARKERS:
            key = attrd.get(marker)
            if key:
                raw = _set_attr(raw, attr, self._translate(key))
        return raw, attrd.get(TEXT_MARKER)

    def _open(self, tag, attrs):
        if self.marked is not None:
            raise TemplateMarkerError(
                f"<{self.marked['tag']} {TEXT_MARKER}={self.marked['key']!r}> "
                f"contains child <{tag}>; put the text in its own element")
        raw, key = self._localize_start(tag, attrs)
        self.out.append(raw)
        if tag in _VOID_ELEMENTS:
            if key:
                raise TemplateMarkerError(
                    f"<{tag}> is a void element and cannot carry {TEXT_MARKER}={key!r}")
            return
        frame = {"tag": tag, "key": key, "text": []}
        self.stack.append(frame)
        if key:
            self.marked = frame

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if self.marked is not None:
            raise TemplateMarkerError(
                f"<{self.marked['tag']} {TEXT_MARKER}={self.marked['key']!r}> "
                f"contains child <{tag}/>; put the text in its own element")
        raw, key = self._localize_start(tag, attrs)
        if key:
            raise TemplateMarkerError(
                f"<{tag}/> is self-closing and cannot carry {TEXT_MARKER}={key!r}")
        self.out.append(raw)

    def handle_endtag(self, tag):
        frame = None
        if self.stack and self.stack[-1]["tag"] == tag:
            frame = self.stack.pop()
        if frame is not None and frame is self.marked:
            self.out.append(_escape_text(self._translate(frame["key"])))
            self.marked = None
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.marked is not None:
            self.marked["text"].append(data)
        else:
            self.out.append(data)

    def handle_entityref(self, name):
        self._append_ref(f"&{name};")

    def handle_charref(self, name):
        self._append_ref(f"&#{name};")

    def _append_ref(self, raw_ref):
        if self.marked is not None:
            # Decode so the reconstructed text matches what the browser shows,
            # then re-escape the final translation as text.
            self.marked["text"].append(html.unescape(raw_ref))
        else:
            self.out.append(raw_ref)

    def handle_comment(self, data):
        if self.marked is None:
            self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")

    def handle_unknown_decl(self, data):
        self.out.append(f"<![{data}]>")


def _json_for_script(payload):
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Escaping '<' as a JSON unicode escape keeps the payload valid JSON while
    # preventing premature </script> termination and HTML comment smuggling.
    return (text.replace("<", "\\u003c")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))


def render_localized_html(html_text, locale):
    """Translate the fixed template and inject the catalog + ``<html lang>``."""
    resolved = normalize_locale(locale)
    catalog = load_catalog(resolved)
    fallback = load_catalog(DEFAULT_LOCALE)
    translator = _TemplateTranslator(catalog, fallback, resolved)
    translator.feed(html_text)
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
