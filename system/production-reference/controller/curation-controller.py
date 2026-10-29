"""Server-side single-flight continuation of the owner's existing Rakazo routine."""
import argparse
import datetime
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import time
from curation_controller_policy import (
    checkpoint_position,
    decide,
    failure_class,
    retry_limit_decision,
    terminal_state,
    writer_retry_time,
)

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state.json'
CONFIG = ROOT / 'config.json'
SCRIPT = '/app/packages/adapters/curation-controller-db.ts'
container_id = None
last_status = None
last_published = 0


def publish_status(result):
    """Refresh two fixed user views; receipts remain the audit authority."""
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    mcp = client(config)
    manifest_path=ROOT/'knowledge-maintenance-manifest.json'
    expected=json.loads(manifest_path.read_text(encoding='utf-8')).get('skills',{}) if manifest_path.exists() else {}
    snapshot = database({'action': 'snapshot','expectedSkills':expected})
    status = json.loads(mcp.tool('curation_status'))
    clock = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    latest = snapshot.get('latest') or {}
    active = snapshot.get('active') or []
    enabled = snapshot['routine']['active']
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    retry_at = float(state.get('not_before', 0))
    backoff = enabled and not active and retry_at > time.time()
    state_text = '正在整理' if active else '等待自动重试' if backoff else '等待下一批' if enabled else '已暂停新批次'
    note = '# 知识库当前运行\n\n更新时间：' + clock.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）\n\n'
    note += '本页每次运行状态变化或约5分钟更新，超过10分钟未更新时按过期快照看待。\n\n'
    note += '**Chief：' + state_text + '**\n\n'
    note += '- 首次历史队列位置：' + str(status['next_index']) + ' / ' + str(status['total']) + '（位置不是成功数量）\n'
    note += '- 已保存的知识来源成功：' + str(status['successes']) + '；主题修复：' + str(status['repairs']) + '；连续阅读进度：' + str(status['read_steps']) + '。\n'
    note += '- 全库内容体检完成时间：' + str(status.get('last_completed_lint') or '尚无完整验收记录') + '。\n'
    note += '- 当前写入方：' + str((status.get('writer') or {}).get('owner') or '无') + '。\n'
    note += '- 最近批次状态：' + str(latest.get('status') or '未知') + '；编号：`' + str(latest.get('id') or '未知') + '`。\n'
    if latest.get('error'):note += '- 最近错误：' + str(latest['error']).replace('\n',' ')[:500] + '\n'
    if backoff:
        retry_clock = datetime.datetime.fromtimestamp(retry_at, clock.tzinfo)
        note += '- 下次检查：' + retry_clock.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）；' + ('等待其他写入方释放租约，最多30秒重查。' if status.get('writer') else '按本次运行结果的退避规则等待，不代表中转站仍故障。') + '\n'
    recovery_path=ROOT/'relay-recovery.json'
    if recovery_path.exists():
        recovery=json.loads(recovery_path.read_text());probe=recovery.get('last_probe')or{}
        note+='- 中转站探测：'+('恢复探测运行中，每15分钟最多一条请求' if recovery.get('enabled') else '当前未处于上游暂停，定时恢复探测未启用')+'；最近一次历史探测 '+str(probe.get('at','尚无记录'))+'；当次可用 '+str(probe.get('available','未知'))+'。\n'
    versions=snapshot.get('skill_sync',{})
    note += '- 三项知识库Skill版本：' + ('一致' if versions.get('checked')==3 and not versions.get('mismatches') else '未通过完整版本校验') + '。\n'
    note += '\n[[90-Logs/今日运行|今天整理了什么]] · [[90-Logs/首次全量整理/当前批次|精确续跑位置]] · [[90-Logs/入库台账|全部处理记录]] · [[90-Logs/巡检进度|体检范围]] · [[90-Logs/结构巡检报告|结构检查]]\n'
    note += '\n## 自动任务\n\n首次历史队列已遍历完，原续跑开关保持现状；每日资料整理每小时整点串行运行，每轮最多3份普通来源，确认答复优先且不占该额度。有其他写入方时让出本轮，无待整理时结束；72份/日只是排期上限，不是实际吞吐。每日结构巡检21:30、周日内容体检与知识简报21:00（北京时间）。\n\n'
    for task in snapshot.get('schedules',[]):
        note += '- '+task['name']+'：'+('启用' if task['active'] else '暂停')+'；模型 '+str(task.get('modelId')or'默认')+'。\n'
    note += '\n[[90-Logs/运维说明/任务与复盘现状|任务分工与恢复办法]] · [[90-Logs/知识周报/最新周报|最近知识库周报]]\n'
    mcp.tool('write_note', path='90-Logs/运维说明/当前运行.md', content=note)
    ledger = mcp.tool('read_note', path='90-Logs/入库台账.md', offset=0, limit=4000)
    import re
    match = re.search(r'total_chars=(\d+)', ledger)
    if match and int(match[1]) > 3800:
        ledger = mcp.tool('read_note', path='90-Logs/入库台账.md', offset=max(0, int(match[1])-3800), limit=4000)
    rows = [line for line in ledger.splitlines() if ' | operation:' in line]
    entries = []
    for line in rows[-12:]:
        fields=line.split(' | ')
        source=fields[0]
        log_paths=re.findall(r'\[\[(90-Logs/程序整理/[^\]|]+)',line)
        if not log_paths:continue
        header=mcp.tool('read_note',path=log_paths[-1],offset=0,limit=120)
        if '# '+clock.strftime('%Y-%m-%d')+' 整理' not in header:continue
        kind='主题修复' if 'operation: topic_repair' in line else '无知识处置' if 'status: no_knowledge' in line else '空白处置' if 'status: empty' in line else '知识整理'
        links=re.findall(r'\[\[[^\]]+\]\]',line)
        label=source.rsplit('/',1)[-1].removesuffix('.md') if '/' in source else source
        entries.append('- '+kind+'：'+label+' '+ ' '.join(links[:5]))
    today = '# 今日知识库维护\n\n日期：' + clock.strftime('%Y-%m-%d') + '（北京时间）；更新：' + clock.strftime('%H:%M:%S') + '\n\n'
    today += '这是最近台账片段的展示，最多12项，不是全天数量；日期按程序操作日志核对，不把原文日期当处理日期。全天记录和具体证据从入库台账查询。\n\n'
    today += '\n'.join(entries) if entries else '当前读取的台账末段没有本日记录；不据此认定今天没有运行。'
    today += '\n\n[[90-Logs/入库台账|查看完整台账与原始操作日志]] · [[90-Logs/运维说明/当前运行|当前运行]]\n\n程序整理是操作记录，知识修订是修改前备份，内容巡检是当次检查；旧日志保留历史，不能覆盖当前规则。\n'
    mcp.tool('write_note', path='90-Logs/今日运行.md', content=today)


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(path)


def database(action):
    global container_id
    current = subprocess.check_output(['sudo', 'docker', 'inspect', '--format', '{{.Id}}', 'rakazo-api-1'], text=True, timeout=10).strip()
    if current != container_id:
        subprocess.run(['sudo', 'docker', 'cp', str(ROOT / 'curation-controller-db.ts'), 'rakazo-api-1:' + SCRIPT], check=True, capture_output=True, timeout=15)
        container_id = current
    result = subprocess.run(['sudo', 'docker', 'exec', '-i', 'rakazo-api-1', '/app/node_modules/.bin/tsx', SCRIPT], input=json.dumps(action), text=True, encoding='utf-8', capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Controller database command failed: ' + result.stderr[-600:])
    return json.loads(result.stdout)


def client(config):
    spec = importlib.util.spec_from_file_location('vault_client', ROOT / 'vault-client.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MCP(config['mcp_url'], config['mcp_token'], timeout=120)


def checkpoint(mcp, queue_version):
    text = mcp.tool('read_note', path='90-Logs/首次全量整理/当前批次.md')
    if queue_version not in text:
        raise ValueError('Checkpoint queue version changed; explicit reconciliation required')
    return checkpoint_position(text)


def tick():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    manifest_path=ROOT/'knowledge-maintenance-manifest.json'
    expected=json.loads(manifest_path.read_text(encoding='utf-8')).get('skills',{}) if manifest_path.exists() else {}
    snapshot = database({'action': 'snapshot','expectedSkills':expected})
    recovery=__import__('curation_relay_recovery').probe_once(ROOT,snapshot,state,time.time(),atomic_json,client,checkpoint,database,config,snapshot.get('skill_sync',{}))
    if recovery is not None:return recovery
    watch=__import__('curation_continuity').watchdog_once(ROOT,snapshot,state,time.time(),atomic_json,client,checkpoint,database,config)
    if watch is not None:return watch
    if not snapshot['routine']['continuous'] or not snapshot['routine']['active']:
        return {'status': 'paused_or_not_managed'}
    if snapshot['active']:
        return {'status': 'chief_busy', 'runs': [r['id'] for r in snapshot['active']]}
    if snapshot.get('skill_sync',{}).get('mismatches'):
        reason='Knowledge skill versions differ from the approved release: '+', '.join(snapshot['skill_sync']['mismatches'])
        outcome=database({'action':'pause','expectedLatest':(snapshot.get('latest')or{}).get('id'),'reason':reason})
        return {'status':'skill_version_hold','reason':reason,'outcome':outcome}
    now = time.time()
    mcp = None
    position = state.get('position')
    latest = snapshot.get('latest')
    activity = state.get('activity_counter')
    pipeline_status = None
    if position is None or (latest['id'] if latest else None) != state.get('handled_run'):
        mcp = client(config)
        position = checkpoint(mcp, config['queue_version'])
        pipeline_status = json.loads(mcp.tool('curation_status'))
        activity = pipeline_status['activity_counter']
    if not state:
        state = {'position': position, 'handled_run': latest['id'] if latest else None, 'failures': 0, 'not_before': 0, 'pending_until': 0, 'initialized_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        atomic_json(STATE, state)
    if pipeline_status and pipeline_status.get('pending_suspensions'):
        recovered=__import__('curation_continuity').recover_blocked(mcp or client(config))
        if recovered.get('status')=='SUSPENDED':return __import__('curation_continuity').adopt_suspension(ROOT,mcp or client(config),state,config,recovered,atomic_json)
    decision = decide(snapshot, state, position, config['total'], now)
    if decision['action'] == 'observe_terminal':
        # An interleaved Inbox source or long-source reading segment can make
        # durable progress without moving the initial historical queue cursor.
        if (not snapshot['routine'].get('historical_only') and activity is not None
                and state.get('activity_counter') is not None and activity > state['activity_counter']):
            decision['advanced'] = True
        mcp = mcp or client(config)
        if len(decision['verify_indices']) > 3:
            decision = {'action': 'hold', 'reason': 'unexpected_progress_jump'}
        else:
            for index in decision['verify_indices']:
                try:
                    report = json.loads(mcp.tool('curation_verify', index=index, verify_saved=True, require_completion=True))
                    if not ((report['status']=='PASS' and report.get('require_completion')) or (report['status']=='SUSPENDED' and report.get('disposition_verified'))):
                        raise ValueError('Program completion checks failed: ' + json.dumps(report.get('issues', []), ensure_ascii=False))
                except Exception as exc:
                    decision = {'action': 'hold', 'reason': 'completion_receipt_failed index=' + str(index) + ': ' + str(exc)[:600]}
                    break
            if decision['action'] != 'hold':
                previous_handled_run = state.get('handled_run')
                state = terminal_state(state, decision, position, now)
                state['not_before'] = max(state['not_before'], writer_retry_time((pipeline_status or {}).get('writer'), now))
                state['activity_counter'] = activity
                kind = failure_class(latest)
                if decision['completed'] and decision['advanced']:
                    state['recovery_attempts'] = 0
                    state.pop('blocked_reason', None)
                atomic_json(STATE, state)
                if decision.get('writer_contention'):
                    state['not_before']=max(now+10,writer_retry_time((pipeline_status or {}).get('writer'),now))
                    atomic_json(STATE,state)
                    outcome=database({'action':'schedule','preserveModel':True,'expectedLatest':decision['run'],'due':datetime.datetime.fromtimestamp(state['not_before'],datetime.timezone.utc).isoformat()})
                    return {'status':'verified_writer_contention','source_retry_charged':False,'not_before':state['not_before'],'outcome':outcome}
                if kind == 'upstream_unavailable':
                    reason='Upstream account/service unavailable; pause without charging source retry budget.'
                    outcome=database({'action':'pause','expectedLatest':decision['run'],'reason':reason})
                    probe_arm=__import__('curation_relay_recovery').arm_after_exhaustion(ROOT,snapshot,position,now,atomic_json,database,kind) if outcome.get('status')=='paused' else {'status':'not_paused'}
                    return {'status':'upstream_unavailable_hold','failure_class':kind,'outcome':outcome,'relay_probe':probe_arm}
                if ((not decision['advanced'] and kind in ('none','unknown','nonretryable','stream_json')) or kind=='source_capacity') and 'suspended_count' in (pipeline_status or {}):
                    held=__import__('curation_continuity').recover_blocked(mcp)
                    if held.get('status')=='DIAGNOSIS_RETRY':
                        state['not_before']=now+held['retry_after_seconds'];atomic_json(STATE,state)
                        outcome=database({'action':'schedule','preserveModel':True,'expectedLatest':decision['run'],'due':datetime.datetime.fromtimestamp(state['not_before'],datetime.timezone.utc).isoformat()})
                        return {'status':'source_diagnostic_backoff','diagnostic':held,'outcome':outcome}
                    if held.get('status')=='DIAGNOSIS_BUSY':
                        current=json.loads(mcp.tool('curation_status'))
                        state['not_before']=writer_retry_time(current.get('writer'),now)
                        state['handled_run']=previous_handled_run
                        atomic_json(STATE,state)
                        return {'status':'source_diagnosis_waiting_for_lease','not_before':state['not_before']}
                    if held.get('status')=='SUSPENDED':
                        fresh=checkpoint(mcp,config['queue_version'])
                        __import__('curation_continuity').verify_previous(mcp,fresh[0])
                        state.update(position=fresh,failures=0,no_progress=0,recovery_attempts=0,not_before=now+10,pending_until=0)
                        state.pop('blocked_reason',None);atomic_json(STATE,state)
                        __import__('curation_continuity').append(ROOT,'continuity-events.jsonl',{'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kind':'source_suspended','receipt':held,'position':fresh})
                        return {'status':'source_suspended','receipt':held,'position':fresh}
                if kind in ('nonretryable', 'unknown', 'provider_recovery', 'source_capacity'):
                    reason = 'nonretryable_or_unknown_failure: ' + kind
                    outcome = database({'action': 'pause', 'expectedLatest': decision['run'], 'reason': reason})
                    if outcome.get('status') == 'paused':
                        state['blocked_reason'] = reason
                        atomic_json(STATE, state)
                    return {'status': 'failure_hold', 'failure_class': kind, 'reason': reason, 'outcome': outcome}
                if kind in ('transient', 'stream_json') or state['no_progress'] >= 3 or state['failures'] >= 6:
                    reason = ('Known relay model-route failure; waiting for bounded automatic recovery.'
                              if kind == 'provider_recovery' else
                              'Repeated batches made no saved progress or repeatedly failed; inspect upstream and checkpoint before retrying.')
                    retry = retry_limit_decision(state, latest, now)
                    if retry['action'] == 'schedule':
                        state['recovery_attempts'] = retry['attempt']
                        state['not_before'] = retry['due']
                        state['blocked_reason'] = reason
                        atomic_json(STATE, state)
                        outcome = database({
                            'action': 'schedule', 'preserveModel': True,
                            'expectedLatest': decision['run'],
                            'due': datetime.datetime.fromtimestamp(retry['due'], datetime.timezone.utc).isoformat(),
                        })
                        return {'status': 'recovery_backoff_scheduled', 'attempt': retry['attempt'], 'failure_class': retry['failure_class'], 'not_before': retry['due'], 'outcome': outcome}
                    outcome = database({'action': 'pause', 'expectedLatest': decision['run'], 'reason': reason})
                    if outcome.get('status') == 'paused':
                        state['blocked_reason'] = reason
                        atomic_json(STATE, state)
                    probe_arm = (__import__('curation_relay_recovery').arm_after_exhaustion(
                        ROOT,snapshot,position,now,atomic_json,database,retry['failure_class'])
                        if outcome.get('status') == 'paused' else {'status':'not_paused'})
                    return {'status': 'retry_limit_hold', 'failure_class': retry['failure_class'], 'reason': reason, 'outcome': outcome,'relay_probe':probe_arm}
                # Push fallback beyond backoff so it cannot circumvent the retry delay.
                if state['failures']:
                    outcome = database({'action': 'schedule', 'preserveModel': True, 'expectedLatest': decision['run'], 'due': datetime.datetime.fromtimestamp(state['not_before'], datetime.timezone.utc).isoformat()})
                    return {'status': 'backoff_scheduled', 'failures': state['failures'], 'not_before': state['not_before'], 'outcome': outcome}
                return {'status': 'progress_verified', 'position': position, 'next_batch_after_seconds': 10}
    if decision['action'] == 'hold':
        outcome = database({'action': 'pause', 'expectedLatest': latest['id'] if latest else None, 'reason': decision['reason']})
        return {'status': 'verification_hold', 'reason': decision['reason'], 'outcome': outcome}
    if decision['action'] == 'schedule':
        # Recheck the cursor immediately before dispatch; do not trust stale local state.
        mcp = mcp or client(config)
        pipeline_status = json.loads(mcp.tool('curation_status'))
        if pipeline_status.get('pending_suspensions'):
            recovered=__import__('curation_continuity').recover_blocked(mcp)
            if recovered.get('status')=='SUSPENDED':return __import__('curation_continuity').adopt_suspension(ROOT,mcp,state,config,recovered,atomic_json)
            return {'status':'suspension_recovery_wait','receipt':recovered}
        retry_at = writer_retry_time(pipeline_status.get('writer'), now)
        if retry_at > now:
            state['not_before'] = retry_at
            atomic_json(STATE, state)
            outcome = database({'action': 'schedule', 'preserveModel': True, 'expectedLatest': decision['expectedLatest'], 'due': datetime.datetime.fromtimestamp(retry_at, datetime.timezone.utc).isoformat()})
            return {'status': 'writer_wait_scheduled', 'not_before': retry_at, 'outcome': outcome}
        fresh = checkpoint(mcp, config['queue_version'])
        if fresh != position:
            return {'status': 'checkpoint_changed_before_dispatch', 'saved': position, 'current': fresh}
        outcome = database({'action': 'schedule', 'preserveModel': True, 'expectedLatest': decision['expectedLatest'], 'due': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        if outcome['status'] == 'scheduled':
            state['pending_until'] = now + 120
            atomic_json(STATE, state)
        return {'status': 'continuation_requested', 'outcome': outcome, 'position': position}
    return {'status': decision['reason']}


def main():
    global last_status, last_published
    parser = argparse.ArgumentParser()
    parser.add_argument('--loop', action='store_true')
    args = parser.parse_args()
    with (ROOT / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                result = tick()
            except Exception as exc:
                result = {'status': 'controller_error', 'error': str(exc)[:800]}
            try:
                current_state=json.loads(STATE.read_text())
                current_snapshot=database({'action':'snapshot'})
                __import__('curation_continuity').observation_once(ROOT,current_snapshot,current_state,result,time.time(),atomic_json)
                alert=__import__('curation_continuity').idle_alert_due(ROOT,current_snapshot,current_state,result,time.time(),atomic_json)
                if alert:__import__('curation_continuity').deliver_alert(ROOT,alert)
            except Exception as exc:
                print(json.dumps({'status':'continuity_bookkeeping_error','error':str(exc)[:500]}),flush=True)
            signature = json.dumps(result, ensure_ascii=False, sort_keys=True)
            if signature != last_status or time.time()-last_published >= 300:
                try:
                    publish_status(result)
                    last_published = time.time()
                except Exception as exc:
                    print(json.dumps({'status':'status_view_error','error':str(exc)[:400]}),flush=True)
            if signature != last_status:
                print(json.dumps({'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), **result}, ensure_ascii=False), flush=True)
                last_status = signature
            if not args.loop:
                break
            time.sleep(20)


if __name__ == '__main__':
    main()
