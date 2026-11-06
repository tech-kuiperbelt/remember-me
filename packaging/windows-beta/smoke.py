"""One isolated HTTP/MCP CRUD/restart smoke with the bundled model and interpreter."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from urllib.error import URLError, HTTPError
from urllib.request import Request, build_opener, ProxyHandler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', required=True, type=Path)
    work = p.parse_args().work.resolve()
    bundle, evidence = work / 'bundle', work / 'evidence'
    data = work / 'smoke-data'
    if data.exists():
        raise RuntimeError('Smoke must use a new data directory')
    opener = build_opener(ProxyHandler({}))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    def request(path, body=None):
        headers = {'Accept':'application/json, text/event-stream'}
        if body is not None: headers['Content-Type']='application/json'
        req = Request(base+path, data=None if body is None else json.dumps(body).encode('utf-8'), headers=headers)
        with opener.open(req, timeout=120) as response:
            payload = response.read()
            return json.loads(payload) if payload and 'json' in response.headers.get('Content-Type','') else payload
    def rpc(method, params, ident=1):
        value = request('/mcp', {'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        if 'error' in value: raise RuntimeError(value)
        return value['result']
    process = None
    log = (evidence/'business-server.log').open('wb')
    result = {'status':'failed', 'strict_s1a_defender_audit':'not a Beta build gate; original POC evidence retained'}
    started = time.perf_counter()
    def launch():
        nonlocal process
        env = dict(os.environ, REMEMBER_ME_BETA_DATA_DIR=str(data))
        child = subprocess.Popen([sys.executable,'-I',str(bundle/'beta_start.py'),'--port',str(port)],
                                 cwd=bundle, env=env, stdout=log, stderr=subprocess.STDOUT)
        process = child
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if child.poll() is not None:raise RuntimeError(f'Server exited during startup: {child.returncode}')
            try:
                request('/api/stats')
                return child
            except (URLError,HTTPError):time.sleep(0.5)
        child.terminate();child.wait(timeout=30)
        raise RuntimeError('Server startup timed out; inspect business-server.log')
    def stop(child):
        request('/api/maintenance/enter',{})
        request('/api/shutdown',{})
        assert child.wait(timeout=120)==0, 'Server did not exit cleanly'
    try:
        process = launch()
        # Verify both lifetime sidecars exclude independent Windows processes.
        for kind in ('primary', 'replica'):
            code = ("import sys;sys.path.insert(0,sys.argv[1]);"
                    "from app.storage.database_lock import DatabaseWriterLock;"
                    "from app.storage.ndjson import NDJSONReplica;"
                    "lock=DatabaseWriterLock(sys.argv[2]) if sys.argv[3]=='primary' else NDJSONReplica(sys.argv[2]);"
                    "lock.acquire() if sys.argv[3]=='primary' else lock.acquire_writer_lock()")
            path = data / ('memories.db' if kind == 'primary' else 'memories.jsonl')
            contender = subprocess.run([sys.executable,'-I','-c',code,str(bundle),str(path),kind],
                                       capture_output=True,timeout=30)
            assert contender.returncode != 0 and b'active' in contender.stderr, contender.stderr
        assert b'<html' in request('/admin/console').lower()
        assert request('/api/version-update')['current']['install_method'] == 'WINDOWS_SETUP'
        memory = request('/api/memories', {'text':'Windows Beta remembers 中文离线验证。','namespace':'default'})
        mid = memory['id']
        found = request('/api/memories/search',{'query':'中文离线验证','namespace':'default','k':3})
        assert mid in json.dumps(found)
        rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'windows-beta-smoke','version':'1'}})
        tools = rpc('tools/list',{})
        assert any(t['name']=='search_semantic' for t in tools['tools'])
        found = rpc('tools/call',{'name':'search_semantic','arguments':{'query':'中文离线验证','namespace':'default','k':3}})
        assert not found.get('isError') and mid in json.dumps(found)
        stop(process);process=None
        process = launch()
        assert request('/api/memories/'+mid)['id']==mid
        found = rpc('tools/call',{'name':'search_semantic','arguments':{'query':'Windows Beta','namespace':'default','k':3}})
        assert mid in json.dumps(found)
        stop(process);process=None
        result.update(status='passed',checks=['HTTP startup','Windows Setup installation identity','Web resources','HTTP add/search','MCP initialize/list/search',
                                             'primary and replica single-writer exclusion','graceful shutdown','restart preserves data','bundled offline model'],
                      seconds=time.perf_counter()-started, data_directory=str(data))
    except BaseException:
        result['error']=traceback.format_exc()
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate();process.wait(timeout=30)
        log.close()
        (evidence/'business-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
