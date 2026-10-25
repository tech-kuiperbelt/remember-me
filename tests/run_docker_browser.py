"""Real Chrome → Docker UI save/restart acceptance, without mocked HTTP APIs.

Use only an isolated deployment: python tests/run_docker_browser.py URL
Changes its language/debug settings and restarts its application process.
"""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.chrome_viewport import run_viewport

SCENARIO = r"""
addEventListener('DOMContentLoaded', async () => {
  const result=document.createElement('pre');result.id='smoke-result';document.body.append(result);
  const $=id=>document.getElementById(id), assert=(test,msg)=>{if(!test)throw Error(msg);};
  const wait=async(test,msg)=>{const end=Date.now()+25000;while(!test()){if(Date.now()>end)throw Error(msg);await new Promise(r=>setTimeout(r,100));}};
  try {
    assert(document.documentElement.dataset.deploymentMode==='docker','Docker marker');
    await wait(()=>$('btn-model-save')?.onclick,'application mounted');
    await wait(()=>$('memories-list').getAttribute('aria-busy')==='false'&&document.querySelector('.nav [aria-current=page]'),'navigation bootstrap');
    $('nav-ops').click();await wait(()=>!$('ops-config-fields').disabled,'config loaded');
    for(const id of ['config-port','config-local-only','model-auto-download'])
      assert($(id).getClientRects().length===0,'internal config visible: '+id);
    assert($('model-proxy-enabled').getClientRects().length>0,'shared proxy control is visible');
    const before=await (await fetch('/api/config')).json();
    const language=before.language==='en'?'zh-CN':'en',debug=!before.debug;
    $('config-language').value=language;$('config-debug').checked=debug;
    $('config-debug').dispatchEvent(new Event('change',{bubbles:true}));
    assert(!$('btn-model-save').disabled,'save available');$('btn-model-save').click();
    await wait(()=>!$('ops-config-fields').disabled&&$('btn-model-save').disabled,'save completed');
    const saved=await (await fetch('/api/config')).json();
    assert(saved.language===language&&saved.debug===debug,'language/debug persisted');
    for(const key of ['port','local_only','auto_download_model','proxy'])
      assert(JSON.stringify(saved[key])===JSON.stringify(before[key]),'hidden value changed: '+key);
    $('ops-tab-maintenance').click();
    assert($('btn-shutdown').getClientRects().length===0,'shutdown hidden');
    assert($('btn-model-retry').textContent.includes(document.documentElement.lang==='en'?'Reload':'重新加载'),'Docker Retry label');
    $('btn-maintenance-enter').click();await wait(()=>!$('btn-restart').disabled,'maintenance entered');
    window.confirm=()=>true;$('btn-restart').click();
    await wait(()=>$('restart-address').textContent.includes(location.origin),'published restart address');
    await wait(()=>!$('btn-maintenance-enter').disabled||!$('btn-maintenance-exit').disabled,'restart reconnected');
    const reloaded=await (await fetch('/api/config')).json();
    assert(reloaded.language===language&&reloaded.debug===debug,'config retained after restart');
    if(!$('btn-maintenance-exit').disabled)$('btn-maintenance-exit').click();
    result.textContent='PASS: real Docker UI hidden fields, save, restart, published address and persistence';
  } catch(e) { result.textContent='FAIL: '+e.stack; }
});
"""

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='rm-docker-chrome-') as profile:
        screenshot = Path('/tmp/rm-phase22-browser.png')
        result = run_viewport(sys.argv[1], profile, 1440, 1000, screenshot,
                              scenario_script=SCENARIO)
        print(result)
        if not result['text'].startswith('PASS:'):
            sys.exit(1)
