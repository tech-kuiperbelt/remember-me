"""Opt-in wire fixture: HTTPS GitHub responses behind a local CONNECT proxy.

Never forwards traffic externally. Test trust is supplied separately; TLS verification
and the application's production HTTP client remain enabled and unchanged.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class LocalServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


class Fixture:
    def __init__(self):
        self.mode = 'ok'
        self.seen = []
        self.connects = []
        self.delay = threading.Event()

    @staticmethod
    def release(version):
        return {'version': version, 'new': ['Integration feature ' + version],
                'improvements': [], 'fixes': [], 'upgrade': ['Back up before ' + version],
                'breaking': ['Compatibility change in ' + version]}

    @staticmethod
    def meta(version):
        return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
                'published_at': '2026-10-23T00:00:00Z'}

    def response(self, host, path):
        self.seen.append({'host': host, 'path': path, 'mode': self.mode})
        headers = {}
        if self.mode == 'slow' and host == 'api.github.com' and path.endswith('/latest'):
            self.delay.wait(2)
        if host == 'api.github.com':
            if self.mode == 'forbidden':
                return 403, {}, headers
            if self.mode == 'rate':
                return 429, {}, {'Retry-After': '2'}
            if path.endswith('/latest'):
                if self.mode == 'no_release':
                    return 404, {}, headers
                target = {'equal':'1.0.0','older':'0.9.0'}.get(self.mode,'1.3.0')
                return 200, self.meta(target), headers
            if path.startswith('/repos/tech-kuiperbelt/remember-me/releases'):
                return 200, ([] if self.mode == 'no_release' else [self.meta(v) for v in ('1.3.0','1.2.0','1.1.0')]), headers
        if host == 'raw.githubusercontent.com' and path.startswith('/tech-kuiperbelt/remember-me/'):
            version = path.split('/')[3].removeprefix('v')
            if self.mode == 'raw_missing' or (self.mode == 'gap' and version == '1.2.0'):
                return 404, {}, headers
            if self.mode == 'invalid':
                return 200, b'{invalid JSONC', headers
            if self.mode == 'mismatch' and version == '1.3.0':
                version = '1.9.0'
            body = self.release(version)
            # Real comments exercise the shared JSONC parser rather than a JSON stub.
            return 200, ('// controlled release fixture\n' + json.dumps(body)).encode(), headers
        return 404, {}, headers


def certificates(work):
    import certifi
    work = Path(work)
    for name, domain in [('good', 'api.github.com'), ('bad', 'invalid.example')]:
        command = ['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                   '-subj','/CN='+domain,'-addext','subjectAltName=DNS:'+domain + (',DNS:raw.githubusercontent.com' if name=='good' else ''),
                   '-keyout',str(work/(name+'.key')),'-out',str(work/(name+'.crt'))]
        subprocess.run(command, check=True, capture_output=True)
    bundle = work/'ca-bundle.pem'
    bundle.write_bytes(Path(certifi.where()).read_bytes() + b'\n' + (work/'good.crt').read_bytes())
    return bundle


def start_tls_proxy(fixture, host, work, bad=False):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    prefix = 'bad' if bad else 'good'
    context.load_cert_chain(Path(work)/(prefix+'.crt'), Path(work)/(prefix+'.key'))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            if self.path not in ('api.github.com:443','raw.githubusercontent.com:443'):
                self.send_error(403)
                return
            self.tunnel_host = self.path.split(':')[0]
            fixture.connects.append({'host': self.tunnel_host, 'bad_certificate': bad})
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            try:
                self.connection = context.wrap_socket(self.connection, server_side=True)
                self.connection.settimeout(15)
                self.rfile = self.connection.makefile('rb')
                self.wfile = self.connection.makefile('wb')
                self.close_connection = False
            except (ssl.SSLError, OSError):
                self.close_connection = True

        def do_GET(self):
            if not hasattr(self, 'tunnel_host'):
                self.send_error(405)
                return
            code, data, headers = fixture.response(self.tunnel_host, self.path)
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    server = LocalServer((host,0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, 'http://'+host+':'+str(server.server_port)


def start_control_proxy(fixture, upstream, *, advance=None):
    """Only /__phase_e/* is test control; every product route hits the real app."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def dispatch(self):
            body = self.rfile.read(int(self.headers.get('Content-Length','0')))
            if self.path.startswith('/__phase_e/'):
                if self.path == '/__phase_e/case':
                    fixture.mode = json.loads(body)['mode']
                elif self.path == '/__phase_e/advance' and advance:
                    advance(json.loads(body)['seconds'])
                data = json.dumps({'mode':fixture.mode, 'requests':fixture.seen, 'connects':fixture.connects}).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
            else:
                headers = {key:self.headers[key] for key in ('Content-Type','Accept','Origin','Sec-Fetch-Mode') if self.headers.get(key)}
                request = urllib.request.Request(upstream+self.path, data=body if self.command=='POST' else None,
                                                 headers=headers, method=self.command)
                try:
                    response = opener.open(request,timeout=60)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    data = response.read()
                    self.send_response(response.status)
                    for key in ('Content-Type','Cache-Control','Retry-After','Allow'):
                        if response.headers.get(key): self.send_header(key,response.headers[key])
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

    parsed = urlsplit(upstream)
    assert parsed.hostname == '127.0.0.1'
    server = LocalServer(('127.0.0.1',0), Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server, 'http://127.0.0.1:'+str(server.server_port)
