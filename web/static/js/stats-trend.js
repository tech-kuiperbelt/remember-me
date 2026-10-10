// Responsive SVG trend. Dates are API calendar dates, never browser-local instants.
import { el, number } from './stats-view.js';
const ns = 'http://www.w3.org/2000/svg';
function svgNode(tag, attrs) {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attrs))node.setAttribute(key, value);
  return node;
}
export function bucketLabel(bucket) {
  return bucket.date || bucket.start_date + ' – ' + bucket.end_date;
}
export function mountTrend(root, buckets, total) {
  if (!total) { root.append(el('p', 'stats-trend-empty', 'No searches for this period.')); return () => {}; }
  const svg = svgNode('svg', {class: 'stats-trend-svg', role: 'img', tabindex: 0,
    'aria-label': 'Search activity. Use Left and Right arrows to inspect counts, Home and End for first and last, Escape to dismiss.'});
  const tooltip = el('div', 'stats-trend-tooltip'); tooltip.hidden = true;
  tooltip.setAttribute('role', 'status'); tooltip.setAttribute('aria-live', 'polite');
  const table = el('table', 'stats-sr-only'); table.append(el('caption', '', 'Search activity by local date'));
  const head = el('tr'); head.append(el('th', '', 'Date'), el('th', '', 'Searches')); table.append(head);
  for (const bucket of buckets) { const row = el('tr'); row.append(el('td', '', bucketLabel(bucket)), el('td', '', number(bucket.count))); table.append(row); }
  root.append(svg, tooltip, table);
  let selected = null, width = 0, points = [], marker;
  const reveal = index => {
    selected = index; tooltip.hidden = false;
    tooltip.replaceChildren(el('strong', '', bucketLabel(buckets[index])), el('span', '', number(buckets[index].count) + ' searches'));
    if (marker) { marker.setAttribute('cx', points[index].x); marker.setAttribute('cy', points[index].y); marker.style.display = ''; }
    const tipWidth = Math.min(220, width-16);
    tooltip.style.width = tipWidth + 'px'; tooltip.style.left = Math.max(8, Math.min(points[index].x-tipWidth/2, width-tipWidth-8)) + 'px';
  };
  function dismiss() { selected = null; tooltip.hidden = true; if (marker)marker.style.display = 'none'; }
  function draw() {
    width = root.clientWidth; if (!width)return;
    const height = 240, left = 48, right = width-16, top = 20, bottom = 202;
    const max = Math.max(1, ...buckets.map(b => b.count)), rawStep = max/4;
    const magnitude = 10**Math.floor(Math.log10(rawStep));
    const step = [1, 2, 5, 10].map(n => n*magnitude).find(n => n >= rawStep);
    const ymax = Math.ceil(max/step)*step;
    svg.setAttribute('viewBox', '0 0 '+width+' '+height); svg.replaceChildren();
    for (let i = 0; i <= ymax/step; i++) {
      const y = bottom-i*step/ymax*(bottom-top);
      svg.append(svgNode('line', {x1: left, x2: right, y1: y, y2: y, class: 'stats-trend-grid'}));
      const label = svgNode('text', {x: left-8, y: y+4, 'text-anchor': 'end', class: 'stats-trend-label'});
      label.textContent = Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1}).format(i*step); svg.append(label);
    }
    points = buckets.map((bucket, i) => ({x: left+i/Math.max(1, buckets.length-1)*(right-left), y: bottom-bucket.count/ymax*(bottom-top)}));
    svg.append(svgNode('path', {d: points.map((p, i) => (i?'L':'M')+p.x+' '+p.y).join(' '), class: 'stats-trend-line'}));
    for (const p of points)svg.append(svgNode('circle', {cx: p.x, cy: p.y, r: 3, class: 'stats-trend-point'}));
    const ticks = width>=900?7:width>=500?5:3;
    const indices = [...new Set(Array.from({length: Math.min(ticks, buckets.length)}, (_, i) => Math.round(i*(buckets.length-1)/Math.max(1,Math.min(ticks,buckets.length)-1))))];
    for (const index of indices) {
      const date = buckets[index].date || buckets[index].start_date;
      const label = svgNode('text', {x: points[index].x, y: 228, 'text-anchor': index===0?'start':index===buckets.length-1?'end':'middle', class: 'stats-trend-label'});
      label.textContent = new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'}).format(new Date(date+'T00:00:00Z')); svg.append(label);
    }
    marker = svgNode('circle', {r: 5, class: 'stats-trend-marker'}); marker.style.display = 'none'; svg.append(marker);
    if (selected !== null)reveal(selected);
  }
  const pointer = event => {
    const rect = svg.getBoundingClientRect(), x = (event.clientX-rect.left)*width/rect.width;
    let nearest = 0; points.forEach((p, i) => { if (Math.abs(p.x-x)<Math.abs(points[nearest].x-x))nearest=i; });
    if (points.length)reveal(nearest);
  };
  svg.onpointermove = pointer; svg.onpointerdown = pointer;
  svg.onpointerleave = () => { if (document.activeElement !== svg)dismiss(); };
  svg.onfocus = () => reveal(selected ?? 0); svg.onblur = dismiss;
  svg.onkeydown = event => {
    if (event.key === 'Escape') { event.preventDefault(); dismiss(); return; }
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))return;
    event.preventDefault();
    const index = event.key === 'Home'?0:event.key === 'End'?buckets.length-1:Math.max(0, Math.min(buckets.length-1, (selected ?? 0)+(event.key === 'ArrowLeft'?-1:1)));
    reveal(index);
  };
  const observer = new ResizeObserver(draw); observer.observe(root); draw();
  return () => observer.disconnect();
}
