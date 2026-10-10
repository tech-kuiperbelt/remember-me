// Read-only Retrieval presentation; all correlation and deduplication are server-owned.
import { el, panel, number, percent, metricIcon } from './stats-view.js';
import { debugEnabled } from './ui.js';
import { mountTrend } from './stats-trend.js';
const modes = ['semantic', 'lexical', 'anchor', 'meta'];
const count = n => Number.isSafeInteger(n) && n >= 0;
const date = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && Number.isFinite(Date.parse(s+'T00:00:00Z'));
export function validateRetrieval(data, namespace, days, timezone) {
  const s = data?.search, f = data?.feedback, scope = data?.scope;
  if (!scope || scope.namespace !== namespace || scope.days !== days || scope.timezone !== timezone ||
      !date(scope.local_start_date) || !date(scope.local_end_date) || !Number.isFinite(Date.parse(scope.window_end_utc)) ||
      !s || !['total_searches', 'successful_searches', 'failed_searches', 'non_empty_searches'].every(k=>count(s[k])) ||
      s.successful_searches+s.failed_searches !== s.total_searches || s.non_empty_searches>s.successful_searches ||
      !Array.isArray(s.modes) || s.modes.length!==4 || new Set(s.modes.map(m=>m.mode)).size!==4 || s.modes.some(m=>!modes.includes(m.mode)||!count(m.count)) ||
      s.modes.reduce((n,m)=>n+m.count,0)!==s.total_searches || !Array.isArray(s.trend) || !s.trend.length ||
      (days!==90&&s.trend.length!==days) || s.trend.some(b=>!count(b.count)||(days===90? !date(b.start_date)||!date(b.end_date)||!Number.isFinite(Date.parse(b.start_utc))||!Number.isFinite(Date.parse(b.end_utc)):!date(b.date))) ||
      s.trend.reduce((n,b)=>n+b.count,0)!==s.total_searches || !f || !['eligible_searches', 'rated_searches', 'rating_count'].every(k=>count(f[k])) ||
      f.eligible_searches!==s.non_empty_searches || f.rated_searches>f.eligible_searches || f.rated_searches>f.rating_count ||
      (f.eligible_searches===0?f.coverage_percent!==null:!Number.isFinite(f.coverage_percent)||f.coverage_percent<0||f.coverage_percent>100) ||
      (f.rating_count===0?f.average_rating!==null:!Number.isFinite(f.average_rating)||f.average_rating< -10||f.average_rating>10) ||
      !f.distribution || !['positive','neutral','negative'].every(k=>count(f.distribution[k])) ||
      Object.values(f.distribution).reduce((n,v)=>n+v,0)!==f.rating_count)throw new Error('Invalid retrieval statistics response');
}
function metric(key, label, value) {
  const item = el('div', 'stats-metric'); item.dataset.metric = key; item.append(metricIcon(key), el('dt', '', label), el('dd', '', value)); return item;
}
function ratingStars(rating) {
  const stars = el('span', 'stats-rating-stars'); stars.id = 'stats-average-rating-stars';
  stars.setAttribute('aria-hidden', 'true'); stars.hidden = rating === null;
  const negative = rating < 0; stars.dataset.direction = negative ? 'negative' : 'positive';
  const value = Math.min(10, Math.abs(rating ?? 0));
  const icon = () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('focusable','false');
    const path = document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d','m12 2.5 2.94 5.96 6.58.96-4.76 4.64 1.12 6.56L12 17.52l-5.88 3.1 1.12-6.56-4.76-4.64 6.58-.96Z');
    svg.append(path); return svg;
  };
  for (let i = 0; i < 10; i++) {
    const star = el('span','stats-rating-star'), fill = el('span','stats-rating-star-fill');
    const fraction = Math.max(0, Math.min(1, value-(negative ? 9-i : i)));
    star.dataset.fill = String(fraction); fill.style.width = fraction*100+'%';
    fill.append(icon()); star.append(icon(),fill); stars.append(star);
  }
  return stars;
}
function proportionalRow(label, n, total, cls = '') {
  const row = el('div', 'stats-retrieval-row '+cls), bar = el('span', 'stats-retrieval-bar'), fill = el('span');
  fill.style.width = (total?n/total*100:0)+'%'; bar.append(fill); bar.setAttribute('aria-hidden','true');
  row.append(el('span', '', label), el('span', 'stats-count', number(n)), el('span', 'stats-percent', total?percent(n/total):'0%'), bar); return row;
}
function diagnostics(data) {
  const details = el('details', 'stats-data-details'); details.id = 'stats-data-details';
  const q = data.data_quality || {}, warnings = Array.isArray(q.warnings)?q.warnings:[];
  const alert = warnings.some(w=>w!=='no_audit_history') || ['skipped_malformed_lines','unmatched_reports','conflicting_search_ids'].some(k=>q[k]>0);
  details.append(el('summary', '', 'Data notes'+(alert?' · Attention':'')));
  const body = el('div', 'stats-data-body'); body.append(el('p', '', 'Global retained-audit scan diagnostics; not limited to the selected namespace or period.'));
  body.append(el('p', '', 'History completeness: '+(data.scope.coverage_complete===true?'Complete':data.scope.coverage_complete===false?'Incomplete':'Unknown')));
  if (data.scope.available_from)body.append(el('p', '', 'Earliest usable retained search: '+data.scope.available_from));
  const names = {skipped_malformed_lines:'Malformed lines skipped',skipped_unrecognized_source:'Unrecognized sources skipped',excluded_test_reports:'Declared test reports excluded',unmatched_reports:'Unmatched reports',conflicting_search_ids:'Conflicting search IDs'};
  for (const [key, label] of Object.entries(names))if (count(q[key]))body.append(el('p', '', label+': '+number(q[key])));
  const messages = {no_audit_history:'No retained audit history is available.',audit_changed_during_scan:'Audit files changed during reading; these statistics may be temporarily incomplete.',incomplete_tail:'An incomplete trailing record was skipped.'};
  for (const warning of warnings)body.append(el('p', '', messages[warning] || String(warning)));
  details.append(body); return details;
}
export function renderRetrieval(root, data) {
  const overview = panel('Overview', 'stats-retrieval-overview'), grid = el('dl', 'stats-overview-grid stats-retrieval-metrics');
  grid.append(metric('searches', 'Searches', number(data.search.total_searches)), metric('rated', 'Rated Searches', number(data.feedback.rated_searches)),
    metric('coverage', 'Feedback Coverage', data.feedback.coverage_percent===null?'—':percent(data.feedback.coverage_percent/100)));
  overview.append(grid);
  const context = el('div', 'stats-retrieval-context');
  const source = el('p', 'stats-retrieval-source', 'MCP search activity and reported feedback'); source.id = 'stats-retrieval-source'; context.append(source);
  context.append(el('p', '', data.scope.local_start_date+' – '+data.scope.local_end_date+' · '+data.scope.timezone));
  const cutoff = new Intl.DateTimeFormat(undefined, {hour:'2-digit',minute:'2-digit',timeZone:data.scope.timezone}).format(new Date(data.scope.window_end_utc));
  context.append(el('p', '', 'Today is partial · as of '+cutoff));
  if (debugEnabled())context.append(diagnostics(data));
  const activity = panel('Search Activity', 'stats-search-activity'), plot = el('div', 'stats-trend'); activity.append(plot);
  const modePanel = panel('Search Modes', 'stats-search-modes');
  const ordered = [...data.search.modes].sort((a,b)=>b.count-a.count||modes.indexOf(a.mode)-modes.indexOf(b.mode));
  for (const mode of ordered) { const row = proportionalRow(mode.mode[0].toUpperCase()+mode.mode.slice(1),mode.count,data.search.total_searches); row.dataset.mode=mode.mode; modePanel.append(row); }
  const feedback = panel('Feedback', 'stats-feedback'), summary = el('div', 'stats-feedback-summary');
  const average = el('div');
  const ratingValue = el('div','stats-rating-value'), numeric = el('strong','',data.feedback.average_rating===null?'—':number(data.feedback.average_rating));
  numeric.id = 'stats-average-rating-value'; ratingValue.append(numeric,ratingStars(data.feedback.average_rating));
  average.append(el('span','stats-label','Average Rating'),ratingValue,el('span','stats-label','Scale −10 to +10'));
  summary.append(average); feedback.append(summary);
  if (!data.feedback.rating_count)feedback.append(el('p','stats-empty','No feedback available for this period.'));
  else for (const [key,label] of [['positive','Positive (>0)'],['neutral','Neutral (=0)'],['negative','Negative (<0)']])feedback.append(proportionalRow(label,data.feedback.distribution[key],data.feedback.rating_count,'stats-feedback-'+key));
  const note = el('p','stats-feedback-note'); note.id = 'stats-feedback-note';
  const info = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); info.id = 'stats-feedback-note-icon';
  for (const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':1.5,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'}))info.setAttribute(key,value);
  const infoPath = document.createElementNS(info.namespaceURI,'path');
  infoPath.setAttribute('d','M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z M12 11v6 M12 7h.01'); info.append(infoPath);
  note.append(info,el('span','','Based on reported ratings only, not all search results. Valid ratings: '+number(data.feedback.rating_count)+'.')); feedback.append(note);
  const lower = el('div','stats-retrieval-panels'); lower.append(modePanel,feedback);
  root.replaceChildren(context,overview,activity,lower);
  return mountTrend(plot,data.search.trend,data.search.total_searches);
}
export function renderRetrievalLoading(root) {
  const status = el('p','stats-sr-only','Loading retrieval statistics…'); status.setAttribute('role','status');
  const overview = el('div','stats-panel stats-retrieval-skeleton stats-skeleton-overview');
  const chart = el('div','stats-panel stats-retrieval-skeleton stats-skeleton-chart');
  const lower = el('div','stats-retrieval-panels'); lower.append(el('div','stats-panel stats-retrieval-skeleton stats-skeleton-detail'),el('div','stats-panel stats-retrieval-skeleton stats-skeleton-detail'));
  root.replaceChildren(status,overview,chart,lower);
}
