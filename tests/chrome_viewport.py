"""Run isolated UI checks at an exact CSS viewport through Chrome's CDP pipe."""
import base64
import json
import os
import select
import subprocess
import time


def run_viewport(url, profile, width, height, screenshot):
    incoming, writer = os.pipe()
    reader, outgoing = os.pipe()

    def connect_pipe():
        os.dup2(incoming, 3)
        os.dup2(outgoing, 4)

    proc = subprocess.Popen([
        'google-chrome', '--headless', '--no-sandbox', '--disable-gpu',
        '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
        '--remote-debugging-pipe', '--user-data-dir=' + profile, 'about:blank',
    ], preexec_fn=connect_pipe, close_fds=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(incoming)
    os.close(outgoing)
    sequence = 0
    buffer = b''

    def call(method, params=None, session=None):
        nonlocal sequence, buffer
        sequence += 1
        message = {'id': sequence, 'method': method, 'params': params or {}}
        if session:
            message['sessionId'] = session
        os.write(writer, json.dumps(message).encode() + b'\0')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            while b'\0' in buffer:
                raw, buffer = buffer.split(b'\0', 1)
                response = json.loads(raw)
                if response.get('id') == sequence:
                    if 'error' in response:
                        raise RuntimeError(response['error'])
                    return response.get('result', {})
            if select.select([reader], [], [], .2)[0]:
                chunk = os.read(reader, 65536)
                if not chunk:
                    raise RuntimeError('Chrome CDP pipe closed')
                buffer += chunk
        raise TimeoutError(method)

    try:
        target = call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height,
             'deviceScaleFactor': 1, 'mobile': False}, session)
        call('Page.navigate', {'url': url}, session)
        deadline = time.monotonic() + 40
        expression = "({text:document.getElementById('smoke-result')?.textContent || '',width:innerWidth,height:innerHeight,dpr:devicePixelRatio})"
        while time.monotonic() < deadline:
            result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, session)['result'].get('value', {})
            if result.get('text', '').startswith(('PASS:', 'FAIL:')):
                break
            time.sleep(.05)
        else:
            raise TimeoutError('UI result')
        if result['width'] != width or result['height'] != height:
            raise AssertionError('Unexpected CSS viewport: ' + str(result))
        screenshot.write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format': 'png'}, session)['data']))
        return result
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        os.close(writer)
        os.close(reader)
