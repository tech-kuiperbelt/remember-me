// Test-only fetch harness, injected into the real /admin/console response before
// the application modules run. It lets the regression observe requests and, when
// needed, control responses at the network boundary instead of reaching into
// production module state.
(() => {
  const realFetch = window.fetch.bind(window);
  const calls = [];
  let override = null;
  let maintenanceFrozen = false;

  const scenario = new URLSearchParams(location.search).get('smoke');
  if (['help-deeplink', 'help-refresh', 'help-invalid', 'help-model'].includes(scenario)) {
    const initial = scenario === 'help-invalid' ? '#help/unknown' : '#help/rules';
    if (!sessionStorage.getItem('help-refresh-stage')) history.replaceState(null, '', location.pathname + location.search + initial);
  }
  if (scenario?.startsWith('help-')) override = url => url === '/api/maintenance' ? Promise.resolve(new Response(JSON.stringify({
    state:'normal', action:null, error:null, required_reason:null, model:{status:'ready',ready:true}
  }), {headers:{'content-type':'application/json'}})) : undefined;
  if (scenario === 'help-model') override = url => url === '/api/maintenance' ? Promise.resolve(new Response(JSON.stringify({
    state:'maintenance', action:null, error:null, required_reason:'model_not_ready', model:{status:'failed',ready:false,error:'missing model'}
  }), {headers:{'content-type':'application/json'}})) : undefined;

  window.__smoke = {
    realFetch,
    calls,
    setOverride(fn) { override = fn; },
    reset() { override = null; maintenanceFrozen = false; },
    resetCalls() { calls.length = 0; },
    freezeMaintenance() { maintenanceFrozen = true; },
    unfreezeMaintenance() { maintenanceFrozen = false; },
  };

  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || String(input);
    const method = (init && init.method) || (input && input.method) || 'GET';
    if (maintenanceFrozen && url === '/api/maintenance') return new Promise(() => {});
    calls.push({url, method, body: init?.body});
    if (override) {
      const handled = override(url, init, realFetch);
      if (handled !== undefined) return handled;
    }
    return realFetch(input, init);
  };
})();
