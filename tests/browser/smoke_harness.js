// Test-only fetch harness, injected into the real /admin/console response before
// the application modules run. It lets the regression observe requests and, when
// needed, control responses at the network boundary instead of reaching into
// production module state.
(() => {
  const realFetch = window.fetch.bind(window);
  const calls = [];
  let override = null;
  let maintenanceFrozen = false;

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
    calls.push({url, method});
    if (override) {
      const handled = override(url, init, realFetch);
      if (handled !== undefined) return handled;
    }
    return realFetch(input, init);
  };
})();
