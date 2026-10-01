"""Run admin browser regression using an isolated in-memory service and headless Chrome.

Usage: .venv/bin/python tests/run_admin_browser.py
Requires google-chrome on PATH; no production database or model is used.

The browser loads the real ``/admin/console`` route and its real assets. Only for
this run, a test harness (fetch-boundary interception) and the regression checks
are injected into the console HTML response, so no test hooks live in production.
"""
import os
import sys
import tempfile
import threading
import subprocess
import socket
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for key in list(os.environ):
    if key.startswith(("MEMORY_", "EMBED_", "OLLAMA_", "FASTEMBED_", "SENTENCE_TRANSFORMERS_")):
        os.environ.pop(key)
work = tempfile.TemporaryDirectory(prefix="remember-ui-test-")
os.environ.update({"MEMORY_STORAGE":"sqlite", "MEMORY_STORAGE_PATH":":memory:",
                   "MEMORY_DATA_DIR": work.name,
                   "MEMORY_AUDIT_PATH":str(Path(work.name) / "audit.jsonl"),
                   "EMBED_PROVIDER":"ollama", "EMBED_PRELOAD":"false",
                   "OLLAMA_BASE_URL":"http://127.0.0.1:1"})
from app.service import MemoryService
from app.transport.admin import AdminAPI
from app.config.settings import Settings
from app import audit
from tests.conftest import FakeProvider
from app.embedding.embed import EmbeddingSpec
from app.storage.sqlite import SQLiteStorage
from starlette.applications import Starlette
import uvicorn
audit.configure()
service = MemoryService(Settings.from_env(),
    SQLiteStorage(':memory:', embedding_spec=EmbeddingSpec('test','smoke',2).as_dict()), FakeProvider())
admin = AdminAPI(service)
service.embed_query = lambda _: [1.,0.]
words = ['cache', 'cache clear', 'rare_id'] + ['word%03d' % i for i in range(60)]
for i, word in enumerate(words):
    service.storage.add('memory %s' % word, [1.,0.], namespace='a', metadata={'keywords':[word]},
                        index_embeddings={('keywords',word): [1.,0.] if word.startswith('cache') else [0.,1.]})

harness = (ROOT / 'tests/browser/smoke_harness.js').read_text()
checks = (ROOT / 'tests/browser/admin_regression.js').read_text()
HEAD_SCRIPT = '<script>' + harness + '</script>'
BODY_SCRIPT = '<script type="module">' + checks + '</script>'


class InjectHarness:
    """Inject the test-only harness into the real console HTML response.

    A ``?theme=...`` query seeds the theme environment *before* the synchronous
    bootstrap runs, without adding test hooks to production code.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not (scope.get("type") == "http" and scope.get("path") == "/admin/console"
                and scope.get("method") == "GET"):
            await self.app(scope, receive, send)
            return
        from urllib.parse import parse_qs
        theme = (parse_qs(scope.get("query_string", b"").decode()).get("theme") or [""])[0]
        seed = ""
        if theme in ("light", "dark", "system"):
            seed = ("<script>try{localStorage.setItem('remember-me.theme.v1','"
                    + theme + "')}catch(e){}</script>")
        if theme == "invalid":
            seed = "<script>localStorage.setItem('remember-me.theme.v1','bogus')</script>"
        if theme == "default":
            seed = "<script>localStorage.removeItem('remember-me.theme.v1')</script>"
        if theme == "system":
            seed += """<script>
            (() => {
              const original = window.matchMedia.bind(window);
              const listeners = new Set();
              const query = '(prefers-color-scheme: dark)';
              const media = {matches:false,
                addEventListener:(type, fn) => {if(type==='change') listeners.add(fn)},
                addListener:fn => listeners.add(fn)};
              window.matchMedia = q => q === query ? media : original(q);
              window.__themeMedia = {set(value) {
                media.matches = value;
                listeners.forEach(fn => fn({matches:value}));
              }};
            })();
            </script>"""
        if theme == "blocked":
            seed = ("<script>Object.defineProperty(window,'localStorage',"
                    "{get(){throw new Error('storage disabled')}})</script>")
        messages = []

        async def capture(message):
            messages.append(message)

        await self.app(scope, receive, capture)
        start = next(m for m in messages if m["type"] == "http.response.start")
        raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        text = raw.decode("utf-8")
        # Seed must run before the synchronous theme bootstrap.
        if seed:
            marker = '<script>'
            idx = text.find(marker)
            if idx == -1:
                raise AssertionError("theme bootstrap script missing from console HTML")
            text = text[:idx] + seed + text[idx:]
        text = text.replace("</head>", HEAD_SCRIPT + "</head>").replace("</body>", BODY_SCRIPT + "</body>")
        payload = text.encode("utf-8")
        headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(payload)).encode()))
        await send({"type": "http.response.start", "status": start["status"], "headers": headers})
        await send({"type": "http.response.body", "body": payload})


app = InjectHarness(Starlette(routes=admin.routes))
sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
t = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True); t.start()

# Desktop viewports (CSS px) and browser zoom factors. Width is measured by the
# page as window.innerWidth; combinations below 1024 CSS px are informational
# only, not part of the acceptance range.
VIEWPORTS = ["1024,768", "1280,720", "1366,768", "1920,1080", "2560,1440"]
SCALES = ["1", "1.25", "1.5"]

# Theme checks run at a couple of viewports in both color schemes instead of
# doubling the whole matrix. The URL carries the scenario so the regression
# module can branch (see admin_regression.js).
THEME_VIEWPORTS = ["1440,900", "768,800"]
THEME_SCHEMES = ["light", "dark"]
THEME_EDGE_CASES = ["system", "blocked", "default", "invalid"]


def run_case(profile, label, window_size, scale, shot_dir, query, theme=None):
    """Run the browser regression at one viewport/scale, saving a screenshot."""
    shot = Path(shot_dir) / (label + ".png")
    url = f'http://127.0.0.1:{port}/admin/console?smoke=' + query
    if theme:
        url += '&theme=' + theme
    cmd = ['google-chrome', '--headless', '--no-sandbox', '--disable-gpu',
           '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
           '--user-data-dir=' + profile, '--window-size=' + window_size,
           '--force-device-scale-factor=' + scale,
           '--screenshot=' + str(shot), '--dump-dom',
           '--virtual-time-budget=40000', url]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=90), shot


def check(proc):
    import re
    import html
    found = re.search(r'<pre id="smoke-result">(.*?)</pre>', proc.stdout, re.S)
    text = html.unescape(found.group(1)) if found else ''
    return text.startswith('PASS:'), text


try:
    # Screenshots are temporary by default. Set UI_SHOT_DIR to keep them for
    # manual review (e.g. UI_SHOT_DIR=/tmp/ui-shots); they are never committed.
    import contextlib
    keep_dir = os.environ.get("UI_SHOT_DIR")
    if keep_dir:
        os.makedirs(keep_dir, exist_ok=True)
    _shots_ctx = contextlib.nullcontext(keep_dir) if keep_dir \
        else tempfile.TemporaryDirectory(prefix='remember-ui-shots-')
    with tempfile.TemporaryDirectory(prefix='keyword-chrome-') as profile, _shots_ctx as shots:
        failures = []
        for window_size in VIEWPORTS:
            for scale in SCALES:
                label = window_size.replace(',', 'x') + '@' + scale
                proc, shot = run_case(profile, label, window_size, scale, shots, label)
                ok, text = check(proc)
                print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                      ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
                if not ok:
                    failures.append(label)
        # Theme foundation scenarios: light/dark at desktop and narrow widths.
        for window_size in THEME_VIEWPORTS:
            for scheme in THEME_SCHEMES:
                label = 'theme-' + window_size.replace(',', 'x') + '-' + scheme
                proc, shot = run_case(profile, label, window_size, '1', shots,
                                      label, theme=scheme)
                ok, text = check(proc)
                print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                      ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
                if not ok:
                    failures.append(label)
        for scheme in THEME_EDGE_CASES:
            label = 'theme-1280x800-' + scheme
            proc, shot = run_case(profile, label, '1280,800', '1', shots,
                                  label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
            if not ok:
                failures.append(label)
        for subject, scheme in (('memories', 'light'), ('memories', 'dark'),
                                ('modal', 'dark'), ('collapsed', 'dark')):
            label = 'theme-1440x900-' + subject + '-' + scheme
            proc, shot = run_case(profile, label, '1440,900', '1', shots,
                                  label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
            if not ok:
                failures.append(label)
        label = 'theme-1280x800-refresh-dark'
        proc, shot = run_case(profile, label, '1280,800', '1', shots,
                              label, theme='dark')
        ok, text = check(proc)
        print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
              ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
        if not ok:
            failures.append(label)
        if failures:
            print('FAIL: scenarios failed: ' + ', '.join(failures))
            sys.exit(1)
        print('Screenshots (not committed): ' + shots +
              (' (retained for review)' if keep_dir else ' (temporary; removed on exit)'))
finally:
    server.should_exit = True; t.join(timeout=5); sock.close(); service.storage.close()
    audit.close()
    work.cleanup()
