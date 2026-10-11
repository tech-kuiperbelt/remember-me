async function runConfigDebugRegression() {
  await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'initial list');
  const phase = sessionStorage.getItem('config-debug-phase') || 'initial';
  const ui = await import('/admin/console/static/js/ui.js');
  if (phase === 'initial') {
    assert(ui.debugEnabled() && !$('nav-audit').hidden, 'startup flag enables UI');
    $('nav-ops').click(); await waitFor(() => !$('btn-model-save').disabled, 'config loaded');
    assert($('config-debug').checked, 'checkbox reflects effective startup flag');
    const config = await S.realFetch('/api/config').then(r => r.json());
    assert(config.debug === false && config.debug_effective === true, 'persisted and effective are distinct');
    $('config-debug').checked = false; $('btn-model-save').click();
    await waitFor(() => !$('config-debug-refresh').hidden && !$('btn-model-save').disabled, 'disable saved');
    assert(ui.debugEnabled() && !$('nav-audit').hidden, 'existing page retained until reload');
    sessionStorage.setItem('config-debug-phase', 'disabled'); location.reload(); return;
  }
  if (phase === 'disabled') {
    await checkDebugDiagnostics(false);
    assert(!ui.debugEnabled() && $('nav-audit').hidden && $('btn-debug').hidden, 'reload disables Debug and Audit');
    const router = await import('/admin/console/static/js/router.js');
    assert(router.switchPage('audit') === false, 'Audit route absent');
    document.querySelector('.memory-row-detail-arrow').click(); await waitFor(() => $('detail-edit'), 'disabled detail');
    assert(!$('detail-debug'), 'drawer Debug absent'); $('btn-detail-close').click();
    $('nav-ops').click(); await waitFor(() => !$('btn-model-save').disabled, 'disabled config loaded');
    assert(!$('config-debug').checked && $('config-debug-refresh').hidden, 'saved off matches current page');
    $('config-debug').checked = true; $('btn-model-save').click();
    await waitFor(() => !$('config-debug-refresh').hidden && !$('btn-model-save').disabled, 'enable saved');
    sessionStorage.setItem('config-debug-phase', 'enabled'); location.reload(); return;
  }
  assert(ui.debugEnabled() && !$('nav-audit').hidden && !$('btn-debug').hidden, 'reload enables Debug and Audit');
  await checkDebugDiagnostics(true);
  assert(!document.body.classList.contains('debug-ids'), 'inspection still opt-in');
  $('btn-debug').click(); assert(document.body.classList.contains('debug-ids'), 'inspector works'); $('btn-debug').click();
  document.querySelector('.memory-row-detail-arrow').click(); await waitFor(() => $('detail-debug'), 'enabled detail');
  $('btn-detail-close').click(); $('nav-ops').click(); await waitFor(() => !$('btn-model-save').disabled, 'enabled config loaded');
  assert($('config-debug').checked && $('config-debug-refresh').hidden, 'on setting persists');
  sessionStorage.removeItem('config-debug-phase');
  result.textContent = 'PASS: config Debug persisted, startup overridden, real reload disables/enables navigation and drawer buttons';
}

async function checkDebugDiagnostics(enabled) {
  $('nav-stats').click(); $('stats-tab-retrieval').click();
  await waitFor(() => $('stats-content').getAttribute('aria-busy') === 'false' && $('stats-retrieval-overview'), 'retrieval loaded');
  assert(Boolean($('stats-data-details')) === enabled, 'Stats diagnostics follow reloaded Debug state');
  $('nav-memories').click(); await waitFor(() => $('memories-list').getAttribute('aria-busy') === 'false', 'back to memories');
}
