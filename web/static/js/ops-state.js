// One presentation policy for the existing maintenance snapshot.
import { t } from './i18n.js';
import { dockerMode } from './core.js';

export function deriveStatus(snapshot) {
  const s = snapshot || {}, valid = ['normal','maintenance','entering','switching'].includes(s.state);
  const idle = !s.action, hasError = !!s.error, modelPending = !!s.required_reason;
  const maintenance = s.state === 'maintenance';
  const kind = !valid ? 'unavailable' : hasError ? 'error' : modelPending ? 'model_not_ready' : s.state === 'normal' ? 'normal' : 'maintenance';
  return {kind, snapshot:s, idle, hasError, modelPending,
    enter: valid && s.state === 'normal' && idle && !hasError && !modelPending,
    exit: maintenance && idle && !hasError && !modelPending,
    retryModel: maintenance && idle && s.required_reason === 'model_not_ready' && !['downloading','validating','cancelling'].includes(s.model?.status),
    restartShutdown: maintenance && idle,
    sync: maintenance && idle && !modelPending,
    bulkActions: maintenance && idle && !hasError && !modelPending};
}
export const statusText = {
  normal: t('status.normal'),
  maintenance: t('status.maintenance'),
  model_not_ready: t('status.model_not_ready'),
  error: t('status.error'),
  unavailable: t('status.unavailable'),
};
export function statusDetail(status) {
  const s=status.snapshot, action=s.action ? t('status.detail.action', {action: s.action}) : '';
  if(status.kind==='unavailable')return t('status.detail.unavailable');
  if(status.hasError)return t('status.detail.error', {error: s.error, action});
  if(status.modelPending)return t('status.detail.model', {reason: dockerMode ? t('docker.model.pending') : s.model?.error || s.model?.status || t('status.detail.model.fallback'), action});
  if(s.state==='entering')return t('status.detail.entering');
  if(s.state==='switching')return t('status.detail.switching');
  return (s.state==='normal' ? t('status.detail.normal') : t('status.detail.maintenance')) + action;
}
