"""Run admin browser regression using an isolated in-memory service and headless Chrome.

Usage: .venv/bin/python tests/run_admin_browser.py
Requires google-chrome on PATH; no production database or model is used.
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
                   "MEMORY_AUDIT_PATH":str(Path(work.name) / "audit.jsonl"),
                   "EMBED_PROVIDER":"ollama", "EMBED_PRELOAD":"false",
                   "OLLAMA_BASE_URL":"http://127.0.0.1:1"})
from app.service import MemoryService
from app.admin import AdminAPI
from app.settings import Settings
from app import audit
from tests.conftest import FakeProvider
from app.embed import EmbeddingSpec
from app.storage_sqlite import SQLiteStorage
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import HTMLResponse
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
checks = '<script>' + (ROOT / 'tests/browser/admin_regression.js').read_text() + '</script>'
async def page(request):
    return HTMLResponse((ROOT / 'web/index.html').read_text().replace('</body>', checks+'</body>'))
app=Starlette(routes=[Route('/__smoke',page)]+admin.routes)
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(app, log_level='error'))
t=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);t.start()
try:
    with tempfile.TemporaryDirectory(prefix='keyword-chrome-') as profile:
        proc=subprocess.run(['google-chrome','--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',
            '--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--dump-dom',
            '--virtual-time-budget=15000',f'http://127.0.0.1:{port}/__smoke'],capture_output=True,text=True,timeout=40)
        import re
        import html
        found=re.search(r'<pre id="smoke-result">(.*?)</pre>',proc.stdout,re.S)
        print(html.unescape(found.group(1)) if found else 'FAIL: browser did not finish: '+proc.stderr[-1200:])
        if not found or not found.group(1).startswith('PASS:'):sys.exit(1)
finally:
    server.should_exit=True;t.join(timeout=5);sock.close();service.storage.close()

    audit.close()
    work.cleanup()
