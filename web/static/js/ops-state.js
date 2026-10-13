// One presentation policy for the existing maintenance snapshot.
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
export const statusText = {normal:'运行中',maintenance:'维护中',model_not_ready:'模型未就绪',error:'维护异常',unavailable:'状态不可用'};
export function statusDetail(status) {
  const s=status.snapshot, action=s.action ? ` · 当前动作：${s.action}` : '';
  if(status.kind==='unavailable')return '服务状态暂不可用，执行控件已禁用。';
  if(status.hasError)return `维护异常：${s.error}${action}`;
  if(status.modelPending)return `模型未就绪：${s.model?.error || s.model?.status || '等待准备'}${action}`;
  if(s.state==='entering')return '正在等待活动写入结束…';
  if(s.state==='switching')return '正在切换数据库…';
  return (s.state==='normal' ? '服务正常运行。' : '写入暂停，操作结束后请手动退出维护。') + action;
}
