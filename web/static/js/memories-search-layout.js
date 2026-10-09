// Measure the complete filter group independently of its current wrapped layout.
export function createMemoriesSearchLayout({bar, onLayout}) {
  const root = bar.querySelector('.memories-filters');
  let frame = null;
  function layout() {
    frame = null;
    const width = bar.getBoundingClientRect().width;
    if (!width) return;
    if (width < 1200) {
      bar.classList.remove('filters-wrapped');
      onLayout();
      return;
    }
    const measure = document.createElement('div');
    measure.className = 'memories-search-bar memories-search-measure';
    measure.inert = true;
    measure.setAttribute('aria-hidden', 'true');
    measure.style.cssText = 'position:fixed;left:-100000px;top:0;display:block;width:max-content;visibility:hidden;pointer-events:none';
    const copy = root.cloneNode(true);
    for (const node of [copy, ...copy.querySelectorAll('*')]) {
      for (const name of ['id', 'for', 'aria-controls', 'aria-labelledby', 'aria-describedby']) node.removeAttribute(name);
    }
    measure.append(copy);
    bar.parentElement.append(measure);
    let filtersWidth;
    try { filtersWidth = Math.ceil(copy.getBoundingClientRect().width); }
    finally { measure.remove(); }
    const buttonsWidth = ['memories-search-submit', 'memories-search-clear', 'memories-advanced-toggle']
      .reduce((total, id) => total + document.getElementById(id).getBoundingClientRect().width, 0);
    // Five 8px grid gaps and an 8px spacer give Clear -> filters a 24px gap.
    const remaining = width - buttonsWidth - filtersWidth - 48;
    bar.style.setProperty('--search-query-width', Math.min(480, Math.max(320, remaining)) + 'px');
    bar.style.setProperty('--search-filters-width', filtersWidth + 'px');
    bar.classList.toggle('filters-wrapped', remaining < 320);
    onLayout();
  }
  function schedule() {
    if (frame === null) frame = requestAnimationFrame(layout);
  }
  const resize = new ResizeObserver(schedule);
  resize.observe(bar);
  const mutation = new MutationObserver(schedule);
  mutation.observe(root, {subtree: true, childList: true, characterData: true, attributes: true});
  document.fonts?.ready.then(schedule);
  document.fonts?.addEventListener('loadingdone', schedule);
  schedule();
  return {destroy() {
    resize.disconnect(); mutation.disconnect();
    document.fonts?.removeEventListener('loadingdone', schedule);
    if (frame !== null) cancelAnimationFrame(frame);
  }};
}
