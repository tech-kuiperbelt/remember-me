// Pure presentation of the bounded audit event. Never infer missing business data.
import { t } from './i18n.js';
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const text = value => typeof value === 'string' && value.trim() ? value.trim() : '';
const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const scalar = value => text(value) || (typeof value === 'boolean' || number(value) ? String(value) : '');
const join = values => values.filter(Boolean).join(' · ');
function list(value, total, truncated) {
  if (!Array.isArray(value)) return '';
  const items = value.map(scalar).filter(Boolean);
  const count = number(total) && total >= value.length ? total : items.length;
  const rest = count - Math.min(3, items.length);
  return join([items.slice(0, 3).join(', '), rest > 0 ? '+' + rest : '', truncated === true && !number(total) ? t('audit.truncated') : '']);
}
function fields(data, names) {
  return join(names.map(name => {
    const value = Array.isArray(data[name]) ? list(data[name], data[name + '_total'], data[name + '_truncated']) : scalar(data[name]);
    return value ? name + ': ' + value : '';
  }));
}
function dimensions(data) { return fields(data, ['keywords', 'categories', 'anchors']); }
function filters(data) { return join([dimensions(data), fields(data, ['updated_after', 'updated_before'])]); }
function ids(refs) { return Array.isArray(refs) ? refs.map(ref => text(object(ref).id)).filter(Boolean) : []; }
function namespace(event, params) {
  const direct = text(event.namespace) || text(params.namespace);
  if (direct) return direct;
  const names = [...new Set((Array.isArray(event.memory_refs) ? event.memory_refs : []).map(ref => text(object(ref).namespace)).filter(Boolean))];
  return names.length > 1 ? t('audit.multiNamespace', {list: list(names)}) : names[0] || '—';
}
function searchOutcome(result) {
  if (Array.isArray(result.items)) return join([t('audit.outcome.returned', {count: result.items.length}), number(result.total) && result.total !== result.items.length ? t('audit.outcome.total', {total: result.total}) : '']);
  return number(result.total) ? t('audit.outcome.returned', {count: result.total}) : '';
}
function booleanOutcome(value, yes, no) { return value === true ? yes : value === false ? no : ''; }
export function presentAudit(value) {
  const event = object(value), p = object(event.params), r = object(event.result);
  let summary = '', outcome = '';
  switch (event.tool) {
    case 'search_semantic': case 'search_lexical': case 'search_anchor':
      summary = join([text(p.query), filters(p)]); outcome = searchOutcome(r); break;
    case 'search_meta':
      summary = filters(p) || t('audit.browse'); outcome = searchOutcome(r); break;
    case 'add':
      summary = join([text(r.id) || list(ids(event.memory_refs)), dimensions(object(p.metadata))]);
      outcome = booleanOutcome(r.created, t('audit.created'), t('audit.exists')); break;
    case 'update': {
      const migrations = (Array.isArray(event.memory_refs) ? event.memory_refs : []).map(object)
        .filter(ref => text(ref.namespace_before) && text(ref.namespace) && ref.namespace_before !== ref.namespace)
        .map(ref => ref.namespace_before + ' → ' + ref.namespace);
      summary = join([text(p.memory_id), fields(p, ['namespace']), dimensions(object(p.metadata)), list(migrations)]);
      outcome = booleanOutcome(r.success, t('audit.updated'), t('audit.notUpdated')); break;
    }
    case 'delete':
      summary = text(p.memory_id); outcome = booleanOutcome(r.success, t('audit.deleted'), t('audit.notDeleted')); break;
    case 'get_details':
      summary = Array.isArray(p.memory_ids) ? list(p.memory_ids) : list([...ids(r.found), ...(Array.isArray(r.missing) ? r.missing : [])]);
      outcome = [Array.isArray(r.found) ? t('audit.found', {count: r.found.length}) : '', Array.isArray(r.missing) ? t('audit.missing', {count: r.missing.length}) : ''].filter(Boolean).join(' / '); break;
    case 'report': {
      const ratings = Array.isArray(p.ratings) ? p.ratings.filter(item => text(object(item).m_id) && Number.isInteger(object(item).rating)).map(item => item.m_id + ': ' + item.rating) : [];
      summary = join([text(event.search_id) || text(p.search_id), list(ratings, p.ratings_total, p.ratings_truncated)]);
      outcome = booleanOutcome(r.recorded, number(r.count) ? t('audit.reported.count', {count: r.count}) : t('audit.reported'), t('audit.notReported')); break;
    }
    case 'list_keywords': {
      summary = fields(p, ['mode', 'queries', 'categories']);
      const groups = Array.isArray(r.results) ? r.results.map(object) : [];
      const results = groups.filter(group => number(group.total)).map(group => (text(group.query) || t('audit.vocab')) + ': ' + group.total);
      outcome = list(results, undefined, r.results_truncated); break;
    }
    case 'stats': summary = t('audit.statsRead'); outcome = number(r.total) ? t('audit.memoriesCount', {count: r.total}) : ''; break;
    case 'memory_import': case 'memory_export': case 'online_recover_start': case 'online_recover':
      summary = join([fields(p, ['filename', 'type', 'namespace_prefix', 'job_id', 'import_id']), fields(r, ['job_id', 'import_id'])]);
      outcome = fields(r, ['status', 'phase', 'records', 'imported', 'count']); break;
    case 'maintenance_enter': summary = t('audit.maintenanceEnter'); outcome = text(r.state); break;
    case 'maintenance_exit': summary = t('audit.maintenanceExit'); outcome = text(r.state); break;
    case 'ndjson_health_check': summary = fields(p, ['trigger']); outcome = booleanOutcome(r.in_sync, t('audit.consistent'), t('audit.inconsistent')); break;
    case 'ndjson_compact': summary = fields(p, ['trigger']); outcome = fields(r, ['outcome', 'file_bytes', 'after_bytes', 'reclaimed_bytes']); break;
    case 'model_config': summary = join([t('audit.saveConfig'), fields(r, ['port', 'language', 'auto_download_model', 'local_only']).split(' · ').slice(0, 3).join(' · ')]); break;
    case 'model_prepare': case 'model_retry': summary = event.tool === 'model_retry' ? t('audit.retryModel') : t('audit.prepareModel'); outcome = fields(r, ['status', 'phase', 'ready']); break;
    case 'embedding_reindex': case 'index_value_reindex':
      summary = join([fields(object(p.profile), ['provider', 'model']), fields(p, ['batch_size'])]);
      outcome = fields(r, ['status', 'completed', 'total', 'count']); break;
    default:
      summary = fields(p, ['query', 'memory_id', 'memory_ids', 'filename', 'type', 'trigger']);
      outcome = fields(r, ['status', 'phase', 'count', 'records']);
  }
  const error = object(event.error);
  const errorText = join([text(error.type), text(error.message)]);
  const code = text(event.response_code) || text(r.response_code);
  return {namespace: namespace(event, p), summary: summary || t('audit.viewJson'),
    outcome: join([errorText || outcome, code]), status: text(event.status) || '—',
    duration: number(event.duration_ms) ? event.duration_ms + ' ms' : '—'};
}
