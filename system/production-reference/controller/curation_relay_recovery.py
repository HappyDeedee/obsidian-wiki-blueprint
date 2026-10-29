"""Approved single-request provider probes within the existing controller lock."""
import json,subprocess,time
from pathlib import Path
def arm_after_exhaustion(root,snapshot,position,now,atomic_json,database,kind):
 """Arm only the pause just made by the controller, never an owner's pause."""
 path=Path(root)/'relay-recovery.json'
 if kind not in ('transient','upstream_unavailable') or not path.exists():return {'status':'not_eligible'}
 prior=json.loads(path.read_text())
 if prior.get('enabled') or not prior.get('fingerprint'):return {'status':'already_armed_or_unconfigured'}
 fresh=database({'action':'snapshot'});latest=fresh.get('latest')or{}
 if (fresh['routine']['active'] or not fresh['routine']['continuous'] or fresh['active']
     or latest.get('id')!=(snapshot.get('latest')or{}).get('id') or latest.get('status')!='failed'):
  return {'status':'changed_hold'}
 with (Path(root)/'relay-outage-history.jsonl').open('a')as f:f.write(json.dumps(prior,ensure_ascii=False)+'\n')
 recovery={'fingerprint':prior['fingerprint']}
 recovery.update(enabled=True,latest=latest['id'],routine_updated_at=fresh['routine']['updatedAt'],
                 next_index=position[0],next_probe_at=max(now+900,prior.get('next_probe_at',0)),
                 outage_started_at=now,previous_excluded_outage_seconds=prior.get('excluded_outage_seconds',0),
                 armed_by='controller_'+kind,armed_at=now,
                 budget_before_pause={k:json.loads((Path(root)/'state.json').read_text()).get(k,0)for k in ['failures','no_progress','recovery_attempts']})
 atomic_json(path,recovery)
 return {'status':'armed','next_probe_at':recovery['next_probe_at'],'next_index':position[0]}

def probe_once(root,snapshot,state,now,atomic_json,client,checkpoint,database,config,expected):
 path=Path(root)/'relay-recovery.json'
 if not path.exists():return None
 recovery=json.loads(path.read_text())
 if not recovery.get('enabled'):return None
 routine=snapshot['routine'];latest=snapshot.get('latest')or{}
 if not routine.get('continuous')or routine.get('active')or snapshot.get('active'):return None
 if latest.get('id')!=recovery['latest']or routine['updatedAt']!=recovery['routine_updated_at']:
  recovery.update(enabled=False,hold='routine_or_run_changed');atomic_json(path,recovery);return {'status':'relay_probe_configuration_hold'}
 if expected.get('mismatches'):return {'status':'relay_probe_skill_hold'}
 alert=alert_if_due(root,recovery,now,atomic_json)
 if now<recovery.get('next_probe_at',0):return {'status':'relay_probe_wait','next_probe_at':recovery['next_probe_at']}
 # Persist the next slot BEFORE the request. A crash cannot hot-loop a probe.
 recovery['next_probe_at']=now+900;atomic_json(path,recovery)
 payload=json.dumps({'action':'probe','fingerprint':recovery['fingerprint']})
 subprocess.run(['sudo','docker','cp',str(Path(root)/'relay-probe.ts'),'rakazo-api-1:/app/packages/adapters/relay-probe.ts'],capture_output=True,text=True,timeout=15,check=True)
 p=subprocess.run(['sudo','docker','exec','-i','rakazo-api-1','/app/node_modules/.bin/tsx','/app/packages/adapters/relay-probe.ts'],input=payload,capture_output=True,text=True,timeout=50)
 if p.returncode:
  recovery.update(enabled=False,hold='probe_configuration_or_local_failure');atomic_json(path,recovery);return {'status':'relay_probe_configuration_hold'}
 result=json.loads(p.stdout);recovery['last_probe']=result
 if result.get('status') in [401,403] and not __import__('curation_controller_policy').upstream_unavailable(str(result.get('status'))+' '+str(result.get('error',''))):
  recovery.update(enabled=False,hold='unclassified_auth_or_configuration');atomic_json(path,recovery);return {'status':'relay_probe_configuration_hold','error':'401/403 origin requires review'}
 with (Path(root)/'relay-probes.jsonl').open('a')as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
 if not result['available']:
  recovery.setdefault('outage_started_at',now);atomic_json(path,recovery);return {'status':'relay_unavailable','probe':result,'next_probe_at':recovery['next_probe_at']}
 mcp=client(config);status=json.loads(mcp.tool('curation_status'))
 if status.get('writer'):atomic_json(path,recovery);return {'status':'relay_available_writer_busy'}
 position=checkpoint(mcp,config['queue_version'])
 if position[0]!=recovery['next_index']:
  recovery.update(enabled=False,hold='checkpoint_changed');atomic_json(path,recovery);return {'status':'relay_probe_checkpoint_hold'}
 if position[0]>0:
  receipt=json.loads(mcp.tool('curation_verify',index=position[0]-1,verify_saved=True,require_completion=True))
  if not ((receipt.get('status')=='PASS' and receipt.get('require_completion')) or (receipt.get('status')=='SUSPENDED' and receipt.get('disposition_verified'))):
   recovery.update(enabled=False,hold='last_receipt_not_verified');atomic_json(path,recovery);return {'status':'relay_probe_receipt_hold'}
 if checkpoint(mcp,config['queue_version'])!=position:return {'status':'relay_probe_checkpoint_changed'}
 outcome=database({'action':'recover','expectedLatest':recovery['latest'],'expectedRoutineUpdatedAt':recovery['routine_updated_at'],'preserveModel':True})
 if outcome.get('status')=='recovered':
  state.update(**recovery.get('budget_before_pause',{}));state['not_before']=now;state.pop('blocked_reason',None);atomic_json(Path(root)/'state.json',state)
  recovery.update(enabled=False,resumed_at=time.time(),resume=outcome,excluded_outage_seconds=recovery.get('previous_excluded_outage_seconds',0)+max(0,now-recovery.get('outage_started_at',now)))
 atomic_json(path,recovery);return {'status':'relay_probe_recovery','outcome':outcome,'probe':result}


def alert_if_due(root,recovery,now,atomic_json):
 """Use the existing owner thread, once per outage; never touch source counters."""
 elapsed=max(0,now-recovery.get('outage_started_at',now))
 total=recovery.get('previous_excluded_outage_seconds',0)+elapsed
 if total<21600 or recovery.get('redline_delivered'):return None
 if now<recovery.get('next_alert_attempt',0):return None
 recovery['next_alert_attempt']=now+900;atomic_json(Path(root)/'relay-recovery.json',recovery)
 from datetime import datetime,timezone
 import hashlib
 at=datetime.fromtimestamp(now,timezone.utc).isoformat()
 payload={'action':'deliver','from':datetime.fromtimestamp(recovery.get('outage_started_at',now),timezone.utc).isoformat(),'until':at,'nonce':'kb-outage-'+hashlib.sha256(str(recovery.get('armed_at')).encode()).hexdigest()[:16],
  'text':'【需主人知悉：上游暂停累计超过6小时】知识整理暂停，原模型、原中转站不变；每15分钟最多探测一次。队列与来源计数未跳过或重置。请核查原中转站账号可用性；恢复后程序先核最后回执再续跑。其他升级工作继续。'}
 subprocess.run(['sudo','docker','cp',str(Path(root)/'kb-daily-delivery.ts'),'rakazo-api-1:/app/packages/adapters/kb-daily-delivery.ts'],capture_output=True,timeout=15,check=True)
 p=subprocess.run(['sudo','docker','exec','-i','rakazo-api-1','/app/node_modules/.bin/tsx','/app/packages/adapters/kb-daily-delivery.ts'],input=json.dumps(payload),text=True,capture_output=True,timeout=60)
 if p.returncode:return {'status':'redline_delivery_failed'}
 result=json.loads(p.stdout);recovery['redline_delivered']=result;atomic_json(Path(root)/'relay-recovery.json',recovery);return result
