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
    """Inject the test-only harness into the real console HTML response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not (scope.get("type") == "http" and scope.get("path") == "/admin/console"
                and scope.get("method") == "GET"):
            await self.app(scope, receive, send)
            return
        messages = []

        async def capture(message):
            messages.append(message)

        await self.app(scope, receive, capture)
        start = next(m for m in messages if m["type"] == "http.response.start")
        raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        text = (raw.decode("utf-8")
                .replace("</head>", HEAD_SCRIPT + "</head>")
                .replace("</body>", BODY_SCRIPT + "</body>"))
        payload = text.encode("utf-8")
        headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(payload)).encode()))
        await send({"type": "http.response.start", "status": start["status"], "headers": headers})
        await send({"type": "http.response.body", "body": payload})


app = InjectHarness(Starlette(routes=admin.routes))
sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
t = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True); t.start()
try:
    with tempfile.TemporaryDirectory(prefix='keyword-chrome-') as profile:
        proc = subprocess.run(['google-chrome','--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',
            '--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--dump-dom',
            '--virtual-time-budget=40000',f'http://127.0.0.1:{port}/admin/console'], capture_output=True, text=True, timeout=90)
        import re
        import html
        found = re.search(r'<pre id="smoke-result">(.*?)</pre>', proc.stdout, re.S)
        print(html.unescape(found.group(1)) if found else 'FAIL: browser did not finish: ' + proc.stderr[-1200:])
        if not found or not found.group(1).startswith('PASS:'):
            sys.exit(1)
finally:
    server.should_exit = True; t.join(timeout=5); sock.close(); service.storage.close()
    audit.close()
    work.cleanup()
