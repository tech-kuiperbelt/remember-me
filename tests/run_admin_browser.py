"""Run admin browser regression using an isolated in-memory service and headless Chrome.

Usage: .venv/bin/python tests/run_admin_browser.py
Requires google-chrome on PATH; no production database or model is used.

The browser loads the real ``/admin/console`` route and its real assets. Only for
this run, a test harness (fetch-boundary interception) and the regression checks
are injected into the console HTML response, so no test hooks live in production.
"""
import os
import json
import math
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


class BrowserProvider(FakeProvider):
    """Match the two-dimensional profile used by the browser fixtures."""
    def __init__(self):
        self.spec = EmbeddingSpec('test', 'smoke', 2)

    def embed_document(self, text):
        return [1., 0.]


config_debug_case = sys.argv[1:] == ['--case', 'config-debug']
browser_runtime = None
if config_debug_case:
    from app.runtime import Runtime
    (Path(work.name) / 'config.jsonc').write_text('{"debug":true}')
    browser_runtime = Runtime(Settings.from_config(work.name, create=True),
                              provider_factory=lambda **_: BrowserProvider(), configure_logging=False)
    browser_runtime.start()
    service = browser_runtime.service
else:
    service = MemoryService(Settings.from_env(),
        SQLiteStorage(':memory:', embedding_spec=EmbeddingSpec('test','smoke',2).as_dict()), BrowserProvider())
debug_disabled = sys.argv[1:] in (['--case', 'debug-disabled'], ['--case', 'retrieval-debug-disabled'], ['--case', 'help-disabled'])
admin = AdminAPI(runtime=browser_runtime) if config_debug_case else AdminAPI(service, debug=not debug_disabled)
service.embed_query = lambda _: [1.,0.]
words = ['cache', 'cache clear', 'rare_id'] + ['word%03d' % i for i in range(60)]
for i, word in enumerate(words):
    service.storage.add('memory %s' % word, [1.,0.], namespace='a',
                        metadata={'keywords':[word], 'categories':['design' if i % 2 == 0 else 'bugfix']},
                        index_embeddings={('keywords',word): [1.,0.] if word.startswith('cache') else [0.,1.]})

harness = (ROOT / 'tests/browser/smoke_harness.js').read_text()
checks = (ROOT / 'tests/browser/admin_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/memories_filters_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/memories_search_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/memories_acceptance_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/memories_visual_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/cards_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/detail_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/create_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/stats_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/retrieval_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/audit_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/config_debug_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/ops_regression.js').read_text()
checks += '\n' + (ROOT / 'tests/browser/help_regression.js').read_text()
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
        if theme in ("light", "neutral", "dark", "system"):
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
        # Observe the real bootstrap before modules mount, without production hooks.
        boot_end = text.index('</script>') + len('</script>')
        text = text[:boot_end] + "<script>window.__themeFirstPaint={theme:document.documentElement.dataset.theme,preference:document.documentElement.dataset.themePreference,favicon:document.getElementById('brand-favicon')?.getAttribute('href')}</script>" + text[boot_end:]
        # Seed must run before the synchronous theme bootstrap.
        if seed:
            marker = '<script>'
            idx = text.find(marker)
            if idx == -1:
                raise AssertionError("theme bootstrap script missing from console HTML")
            text = text[:idx] + seed + text[idx:]
        extra = ""
        scenario = (parse_qs(scope.get("query_string", b"").decode()).get("smoke") or [""])[0]
        if 'filters-error' in scenario:
            extra = """<script>window.__smoke.setOverride(url => url === '/api/stats'
              ? new Response(JSON.stringify({detail:'Initial Stats failure'}),
                {status:500,headers:{'content-type':'application/json'}}) : undefined);</script>"""
        text = text.replace("</head>", HEAD_SCRIPT + extra + "</head>").replace("</body>", BODY_SCRIPT + "</body>")
        payload = text.encode("utf-8")
        headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(payload)).encode()))
        await send({"type": "http.response.start", "status": start["status"], "headers": headers})
        await send({"type": "http.response.body", "body": payload})


app = InjectHarness(Starlette(routes=admin.routes))
sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
t = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True); t.start()

# Desktop window sizes and device pixel density (DPR) factors, not browser zoom. Width is measured by the
# page as window.innerWidth; combinations below 1024 CSS px are informational
# only, not part of the acceptance range.
VIEWPORTS = ["1024,768", "1280,720", "1366,768", "1920,1080", "2560,1440"]
SCALES = ["1", "1.25", "1.5"]

# Theme checks run at a couple of viewports in both color schemes instead of
# doubling the whole matrix. The URL carries the scenario so the regression
# module can branch (see admin_regression.js).
THEME_VIEWPORTS = ["1440,900", "768,800"]
THEME_SCHEMES = ["light", "neutral", "dark"]
THEME_EDGE_CASES = ["system", "blocked", "default", "invalid"]


def run_case(profile, label, window_size, scale, shot_dir, query, theme=None, zoom=1):
    """Run the browser regression at one viewport/scale, saving a screenshot."""
    # Chrome's actual browser page-zoom preference, separate from device scale.
    preferences = Path(profile) / 'Default' / 'Preferences'
    preferences.parent.mkdir(parents=True, exist_ok=True)
    prefs = json.loads(preferences.read_text()) if preferences.exists() else {}
    prefs.setdefault('partition', {})['default_zoom_level'] = {'x': math.log(zoom) / math.log(1.2)}
    preferences.write_text(json.dumps(prefs))
    shot = Path(shot_dir) / (label + ".png")
    url = f'http://127.0.0.1:{port}/admin/console?smoke=' + query
    if theme:
        url += '&theme=' + theme
    # Real frame timing is required to measure theme transitions; virtual-time
    # dump-dom can advance timers while leaving control colors at the old theme.
    if query.startswith(('search-layout-', 'search-wrap-', 'cards-', 'stats-layout-', 'retrieval-layout-', 'audit-layout-', 'ops-', 'help-')) or (query.startswith('theme-') and zoom == 1):
        from tests.chrome_viewport import run_viewport
        width, height = map(int, window_size.split(','))
        result = run_viewport(url, profile, width, height, shot,
                              verify_search_keyboard=query == "search-layout-1920-light",
                              verify_stats_keyboard=query == "stats-layout-1460-all-light",
                              verify_retrieval_keyboard=query == "retrieval-layout-1460-7-light",
                              verify_help_keyboard=query == "help-functional",
                              verify_theme_keyboard=query == "ops-layout-1440-settings-neutral",
                              capture_help_document=query.startswith("help-layout-"))
        shot.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n')
        return subprocess.CompletedProcess([], 0, '<pre id="smoke-result">' + result['text'] + '</pre>', ''), shot
    cmd = ['google-chrome', '--headless', '--no-sandbox', '--disable-gpu',
           '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
           '--user-data-dir=' + profile, '--window-size=' + window_size,
           '--force-device-scale-factor=' + scale,
           '--screenshot=' + str(shot), '--dump-dom',
           '--virtual-time-budget=40000', url]
    import re
    import html
    metrics = None
    # Window dimensions include Chrome decorations; adjust once for the declared
    # acceptance content viewport and verify actual CSS dimensions afterwards.
    for attempt in range(2):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        match = re.search(r'<pre id="smoke-metrics"[^>]*>(.*?)</pre>', proc.stdout, re.S)
        if not match:
            break
        metrics = json.loads(html.unescape(match.group(1)))
        target_height = int(window_size.split(',')[1]) / zoom
        if query.startswith('acceptance-') and attempt == 0 and abs(metrics['height'] - target_height) > 2:
            adjustment = round((target_height - metrics['height']) * zoom)
            option = next(i for i, arg in enumerate(cmd) if arg.startswith('--window-size='))
            width, height = map(int, window_size.split(','))
            cmd[option] = f'--window-size={width},{height + adjustment}'
            continue
        break
    if query.startswith('acceptance-'):
        if not metrics:
            raise AssertionError('Acceptance viewport metrics missing: ' + label)
        width, height = map(int, window_size.split(','))
        if abs(metrics['width'] - width / zoom) > 2 or abs(metrics['height'] - height / zoom) > 2:
            raise AssertionError('Acceptance viewport mismatch: ' + str(metrics))
    if metrics:
        metrics.update(window=window_size, device_scale=float(scale), browser_zoom=zoom)
        shot.with_suffix('.json').write_text(json.dumps(metrics, indent=2) + '\n')
        if zoom != 1 and (abs(metrics['dpr'] - zoom * float(scale)) > .02
                         or abs(metrics['width'] - int(window_size.split(',')[0]) / zoom) > 2):
            raise AssertionError('Browser zoom preference did not apply: ' + str(metrics))
    if not check(proc)[0]:
        shot.with_suffix(".html").write_text(proc.stdout)
    return proc, shot


def selected(label):
    # Select one scenario or a related group; the default runs the full matrix.
    if len(sys.argv) == 3 and sys.argv[1] == '--case-prefix':
        return label.startswith(sys.argv[2])
    return len(sys.argv) == 1 or sys.argv[1:] == ['--case', label]


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
        neutral_cases = [
            ('settings', 'theme-1440x900-neutral', '1440,900', 1),
            ('settings-ready', 'ops-layout-1440-settings-neutral', '1440,1000', 1),
            ('settings-narrow', 'theme-390x844-neutral', '390,844', 1),
            ('settings-zoom', 'theme-zoom-neutral', '1440,1000', 2),
            ('refresh', 'theme-1280x800-refresh-neutral', '1280,800', 1),
            ('memories', 'theme-1440x900-memories-neutral', '1440,900', 1),
            ('search', 'theme-1440x900-search-neutral', '1440,900', 1),
            ('filters', 'theme-1440x900-filters-neutral', '1440,900', 1),
            ('detail', 'theme-1440x900-modal-neutral', '1440,900', 1),
            ('create', 'create-1440x900-edit-neutral', '1440,900', 1),
            ('maintenance', 'ops-layout-1440-maintenance-neutral', '1440,1000', 1),
            ('maintenance-narrow', 'ops-layout-390-maintenance-neutral', '390,1000', 1),
            ('model-error', 'ops-expanded-390-neutral', '390,1000', 1),
            ('stats', 'stats-layout-1460-all-neutral', '1460,900', 1),
            ('stats-narrow', 'stats-layout-390-all-neutral', '390,900', 1),
            ('retrieval', 'retrieval-layout-1460-7-neutral', '1460,1000', 1),
            ('help', 'help-layout-home-1440-neutral', '1440,1000', 1),
            ('help-narrow', 'help-layout-home-390-neutral', '390,1000', 1),
        ]
        for subject, query, size, zoom in neutral_cases:
            label = 'neutral-' + subject
            if not selected(label): continue
            proc, shot = run_case(profile, label, size, '1', shots, query, theme='neutral', zoom=zoom)
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append(label)
        help_cases = [(name, '1440,900', 'light') for name in
                      ('help-disabled', 'help-functional', 'help-deeplink', 'help-refresh', 'help-invalid', 'help-model')]
        help_cases += [(f'help-layout-{topic}-{width}-{scheme}', f'{width},1000', scheme)
                       for topic in ('home', 'connect', 'memory', 'configuration')
                       for width in (1440, 390) for scheme in ('light', 'dark')]
        help_cases += [('help-layout-home-1024-light', '1024,1000', 'light'),
                       ('help-layout-rules-390-dark', '390,1000', 'dark'),
                       ('help-layout-stats-390-light', '390,1000', 'light')]
        for label, size, scheme in help_cases:
            if not selected(label): continue
            # Debug is a startup setting; run this case separately with --case.
            if label == 'help-disabled' and not debug_disabled: continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append(label)
        ops_cases = [('ops-functional', '1440,1000', 'light'), ('ops-restart', '1440,900', 'light'), ('ops-shutdown', '1440,900', 'light'), ('ops-restart-timeout', '1440,900', 'light')]
        ops_cases += [('ops-layout-sidebar-390-maintenance-light', '390,1000', 'light'), ('ops-expanded-390-dark', '390,1000', 'dark'), ('ops-confirm-layout-390-light', '390,844', 'light')]
        ops_cases += [(f'ops-layout-{width}-{tab}-{scheme}', f'{width},1000', scheme) for width in (1440,1024,390) for tab in ('settings','maintenance') for scheme in ('light','dark')]
        for label, size, scheme in ops_cases:
            if not selected(label): continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append(label)
        if selected('config-debug'):
            proc, shot = run_case(profile, 'config-debug', '1440,900', '1', shots, 'config-debug', theme='light')
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + 'config-debug -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append('config-debug')
        for label, size, scheme in [('audit-functional', '1440,900', 'light'), ('audit-layout-narrow', '390,844', 'light'), ('audit-layout-dark', '1440,900', 'dark'), ('audit-layout-list-light', '1440,900', 'light'), ('audit-layout-list-dark', '1440,900', 'dark'), ('audit-layout-list-medium', '820,900', 'light'), ('audit-layout-list-narrow', '390,844', 'light'), ('audit-layout-list-narrow-expanded', '390,844', 'light'), ('audit-layout-list-below', '1320,900', 'light'), ('audit-layout-list-above', '1380,900', 'light')]:
            if not selected(label): continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append(label)
        if selected('keywords-removal'):
            proc, shot = run_case(profile, 'keywords-removal', '1440,900', '1', shots, 'keywords-removal', theme='light')
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + 'keywords-removal -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append('keywords-removal')
        retrieval_cases = [('retrieval-functional', '1440,800', 'light'), ('retrieval-debug-disabled', '1280,800', 'light'), ('retrieval-reload', '1280,800', 'light')]
        retrieval_cases += [(f'retrieval-layout-{width}-{mode}-{scheme}', f'{width},1000', scheme) for width in (390,820,1120,1460) for mode in ('7','30','90','empty','nofeedback') for scheme in ('light','dark')]
        for label, size, scheme in retrieval_cases:
            if not selected(label): continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, output = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) + ('' if ok else ' :: ' + output), flush=True)
            if not ok: failures.append(label)
        stats_cases = [('stats-functional', '1440,700', 'light')]
        stats_cases += [(f'stats-layout-{width}-{mode}-{scheme}', f'{width},900', scheme)
                        for width in (390, 820, 1120, 1460)
                        for mode in ('all', 'single', 'long') for scheme in ('light', 'dark')]
        for label, size, scheme in stats_cases:
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])), flush=True)
            if not ok:
                failures.append(label)
        card_cases = [('cards-functional', '1440,900', 'light')]
        card_cases += [(f'cards-layout-{width}-{scheme}', f'{width},900', scheme)
                       for width in (390, 768, 1024, 1440) for scheme in ('light', 'dark')]
        for label, size, scheme in card_cases:
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])), flush=True)
            if not ok:
                failures.append(label)
        search_cases = [('search-functional', '1280,800', 'light', 1),
                        ('search-wrap-functional', '1920,1080', 'light', 1)]
        search_cases += [(f'search-layout-{width}-{scheme}', f'{width},900', scheme, 1)
                         for width in (390, 768, 1024, 1440, 1920) for scheme in ('light', 'dark')]
        search_cases += [(f'search-zoom-{zoom}-{scheme}', '1920,1080', scheme, zoom)
                         for zoom in (1, 1.25) for scheme in ('light', 'dark')]
        for label, size, scheme, zoom in search_cases:
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme, zoom=zoom)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])), flush=True)
            if not ok:
                failures.append(label)
        for window_size in VIEWPORTS:
            for scale in SCALES:
                label = window_size.replace(',', 'x') + '@' + scale
                if not selected(label):
                    continue
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
                if not selected(label):
                    continue
                proc, shot = run_case(profile, label, window_size, '1', shots,
                                      label, theme=scheme)
                ok, text = check(proc)
                print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                      ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
                if not ok:
                    failures.append(label)
        for scheme in THEME_EDGE_CASES:
            label = 'theme-1280x800-' + scheme
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, '1280,800', '1', shots,
                                  label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
            if not ok:
                failures.append(label)
        for subject, scheme in (('memories', 'light'), ('memories', 'dark'),
                                ('filters', 'light'), ('filters', 'dark'), ('filters-error', 'light'),
                                ('search', 'light'), ('search', 'dark'), ('advanced', 'light'), ('advanced', 'dark'),
                                ('modal', 'dark'), ('collapsed', 'dark')):
            label = 'theme-1440x900-' + subject + '-' + scheme
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, '1440,900', '1', shots,
                                  label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
            if not ok:
                failures.append(label)
        label = 'theme-1280x800-refresh-dark'
        if selected(label):
            proc, shot = run_case(profile, label, '1280,800', '1', shots,
                                  label, theme='dark')
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])))
            if not ok:
                failures.append(label)
        # 24 layout combinations, four constrained popovers, and real browser zoom.
        acceptance = [(f'acceptance-{size.replace(",", "x")}-{scheme}-{sidebar}-{mode}', size, scheme, 1)
                      for size in ('1024,768', '1440,900', '1920,1080')
                      for scheme in ('light', 'dark') for sidebar in ('expanded', 'collapsed')
                      for mode in ('browse', 'search')]
        acceptance += [(f'acceptance-1024x768-{scheme}-expanded-{popover}', '1024,768', scheme, 1)
                       for scheme in ('light', 'dark') for popover in ('categories', 'keywords')]
        acceptance += [(f'acceptance-1920x1080-{scheme}-expanded-keywords', '1920,1080', scheme, 1)
                       for scheme in ('light', 'dark')]
        acceptance += [(f'acceptance-zoom-{zoom}-{scheme}-expanded-{mode}', '1920,1080', scheme, zoom)
                       for zoom in (1, 1.25, 1.5) for scheme in ('light', 'dark')
                       for mode in ('browse', 'search')]
        acceptance += [(f'acceptance-1024x768-{scheme}-expanded-{mode}', '1024,768', scheme, 1)
                       for scheme in ('light', 'dark') for mode in ('lexical', 'anchor')]
        for label, size, scheme, zoom in acceptance:
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme, zoom=zoom)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) +
                  ('' if ok else ' :: ' + (text or proc.stderr[-400:])), flush=True)
            if not ok:
                failures.append(label)
        if selected('debug-disabled'):
            previous_debug = admin.debug
            admin.debug = False
            try:
                proc, shot = run_case(profile, 'debug-disabled', '1280,800', '1', shots, 'debug-disabled')
                ok, text = check(proc)
                print(('PASS ' if ok else 'FAIL ') + 'debug-disabled -> ' + str(shot) + ('' if ok else ' :: ' + (text or proc.stderr[-400:])), flush=True)
                if not ok:
                    failures.append('debug-disabled')
            finally:
                admin.debug = previous_debug
        detail_cases = [('detail-functional', '1280,800', 'light'), ('create-functional', '1280,800', 'light')]
        detail_cases += [(f'create-{size.replace(',', 'x')}-{mode}-{scheme}',size,scheme)
                         for size in ('390,844','1024,768','1440,900','1920,1080')
                         for mode in ('edit','preview','confirm') for scheme in ('light','dark')]
        detail_cases += [(f'detail-{size.replace(",", "x")}-{mode}-{scheme}', size, scheme)
                         for size in ('1024,768', '1280,800', '1440,900', '390,844')
                         for mode in ('view', 'edit', 'preview', 'confirm')
                         for scheme in ('light', 'dark')]
        for label, size, scheme in detail_cases:
            if not selected(label):
                continue
            proc, shot = run_case(profile, label, size, '1', shots, label, theme=scheme)
            ok, text = check(proc)
            print(('PASS ' if ok else 'FAIL ') + label + ' -> ' + str(shot) + ('' if ok else ' :: ' + (text or proc.stderr[-400:])), flush=True)
            if not ok:
                failures.append(label)
        if failures:
            print('FAIL: scenarios failed: ' + ', '.join(failures))
            sys.exit(1)
        print('Screenshots (not committed): ' + shots +
              (' (retained for review)' if keep_dir else ' (temporary; removed on exit)'))
finally:
    server.should_exit = True; t.join(timeout=5); sock.close()
    if browser_runtime: browser_runtime.close()
    else: service.storage.close()
    audit.close()
    work.cleanup()
