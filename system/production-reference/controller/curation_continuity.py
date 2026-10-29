"""Watchdog and observation bookkeeping inside the existing controller lock."""
import datetime,hashlib,json,time
from pathlib import Path

def iso(value):return datetime.datetime.fromtimestamp(value,datetime.timezone.utc).isoformat()
def append(root,name,item):
 with(Path(root)/name).open('a')as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')

def watchdog_decision(snapshot,state,now):
 routine=snapshot['routine'];latest=snapshot.get('latest')or{}
 if not routine.get('continuous')or snapshot.get('archived'):return 'not_managed'
 if snapshot.get('active'):return 'busy'
 if snapshot.get('skill_sync',{}).get('mismatches'):return 'skill_hold'
 if state.get('blocked_reason')or state.get('safety_hold'):return 'redline_hold'
 if snapshot.get('owner_pause'):return 'owner_hold'
 if latest and latest.get('status')not in('completed','failed','cancelled'):return 'nonterminal'
 if latest.get('status')=='failed':return 'failure_requires_controller'
 if routine['active'] and latest.get('id')!=state.get('handled_run'):return 'terminal_requires_controller'
 if now<state.get('not_before',0)or now<state.get('pending_until',0):return 'backoff'
 return 'check_and_restore'if not routine['active']else 'check_and_wake'

def verify_previous(mcp,index):
 if index<=0:return {'status':'EMPTY_START'}
 report=json.loads(mcp.tool('curation_verify',index=index-1,verify_saved=True,require_completion=True))
 if report.get('status')=='PASS'and report.get('require_completion'):return report
 # A suspended source has a separate, verified disposition, never a successful commit.
 if report.get('status')=='SUSPENDED'and report.get('disposition_verified'):return report
 raise ValueError('Last formal disposition not verified: '+str(index-1))

def watchdog_once(root,snapshot,state,now,atomic_json,client,checkpoint,database,config):
 path=Path(root)/'continuity.json';record=json.loads(path.read_text())if path.exists()else{}
 if now<record.get('next_check_at',0):return None
 record['next_check_at']=now+900
 action=watchdog_decision(snapshot,state,now)
 record.update(last_check_at=iso(now),last_decision=action);atomic_json(path,record)
 if action not in('check_and_restore','check_and_wake'):return None
 # Do not rewrite an already-managed immediate continuation or a future backoff.
 if snapshot['routine']['active']:
  due=snapshot['routine'].get('nextRunAt')
  if due and datetime.datetime.fromisoformat(due.replace('Z','+00:00')).timestamp()<=now+120:return None
 mcp=client(config);status=json.loads(mcp.tool('curation_status'))
 if status.get('writer'):return None
 position=checkpoint(mcp,config['queue_version'])
 if position[0]>=config['total']:return None
 verify_previous(mcp,position[0])
 # A manual run can legitimately advance the cursor. Verify each intervening receipt.
 old=state.get('position',position)
 if tuple(position)<tuple(old):raise ValueError('Watchdog checkpoint regressed')
 for i in range(old[0],position[0]):verify_previous(mcp,i+1)
 if checkpoint(mcp,config['queue_version'])!=position:return {'status':'watchdog_checkpoint_changed'}
 outcome=database({'action':'watchdog','expectedLatest':(snapshot.get('latest')or{}).get('id'),
   'expectedRoutineUpdatedAt':snapshot['routine']['updatedAt'],'preserveModel':True,
   'expectedOwnerPauseEvent':snapshot.get('owner_pause_event'),
   'due':iso(now)})
 if outcome.get('status')in('recovered','scheduled'):
  state.update(position=position,activity_counter=status['activity_counter'],pending_until=now+120)
  # The routine terminal is still observed by the ordinary controller on its next tick.
  atomic_json(Path(root)/'state.json',state)
  event={'at':iso(now),'kind':'watchdog_recovery','position':position,'outcome':outcome,'previous_routine_active':snapshot['routine']['active']}
  append(root,'continuity-events.jsonl',event);record['last_recovery']=event;atomic_json(path,record)
  return {'status':'watchdog_recovered','outcome':outcome,'position':position}
 return {'status':'watchdog_restore_not_applied','outcome':outcome}

def observation_once(root,snapshot,state,result,now,atomic_json):
 path=Path(root)/'observation.json';record=json.loads(path.read_text())if path.exists()else{'schema':1,'started_at':iso(now),'active_seconds':0,'excluded_seconds':0,'unknown_seconds':0}
 if any(r.get('routineId')!=snapshot['routine'].get('id')for r in snapshot.get('active',[])):kind='manual_or_maintenance'
 elif snapshot.get('active'):kind='running'
 elif result.get('status','').startswith(('relay_','upstream_')):kind='upstream'
 elif state.get('safety_hold')or state.get('blocked_reason'):kind='redline_or_failure'
 elif not snapshot['routine']['active']:kind='disabled'
 elif result.get('status')in('writer_wait_scheduled','chief_busy'):kind='maintenance_or_lease'
 elif now<state.get('not_before',0)and state.get('failures',0):kind='retry_wait'
 else:kind='ready'
 prior=record.get('last_sample_at',now);elapsed=max(0,now-prior)
 # A dead process cannot retroactively certify the missing interval as running.
 if elapsed>90:record['unknown_seconds']+=elapsed
 elif record.get('last_kind')in('running','ready'):record['active_seconds']+=elapsed
 else:record['excluded_seconds']+=elapsed
 if kind!=record.get('last_kind'):
  append(root,'observation-events.jsonl',{'at':iso(now),'kind':kind,'position':state.get('position'),'status':result.get('status')})
 record.update(last_sample_at=now,last_kind=kind,last_position=state.get('position'),last_status=result.get('status'))
 hour=iso(now)[:13]+':00:00+00:00';hours=record.setdefault('hours',{})
 row=hours.setdefault(hour,{'active_seconds':0,'excluded_seconds':0,'unknown_seconds':0,'queue_start':(state.get('position')or[None])[0],'queue_end':None})
 metric='unknown_seconds'if elapsed>90 else 'active_seconds'if record.get('previous_kind')in('running','ready')else 'excluded_seconds'
 # Split elapsed time at the hour boundary rather than assigning a long gap to one hour.
 start=prior
 while start<now:
  until=min(now,(int(start)//3600+1)*3600);bucket=iso(start)[:13]+':00:00+00:00'
  h=hours.setdefault(bucket,{'active_seconds':0,'excluded_seconds':0,'unknown_seconds':0,'queue_start':None,'queue_end':None})
  h[metric]+=until-start;start=until
 row['queue_end']=(state.get('position')or[None])[0];record['previous_kind']=kind
 atomic_json(path,record)
 return record

def idle_alert_due(root,snapshot,state,result,now,atomic_json):
 path=Path(root)/'idle-alert.json';record=json.loads(path.read_text())if path.exists()else{}
 position=state.get('position');known=result.get('status','').startswith(('relay_','upstream_'))or snapshot.get('owner_pause')or state.get('safety_hold')
 if known or position!=record.get('position'):
  record={'position':position,'since':now};atomic_json(path,record);return None
 if now-record.get('since',now)<3600 or record.get('delivered'):return None
 if now<record.get('next_attempt',0):return None
 record['next_attempt']=now+900;atomic_json(path,record)
 return {'at':iso(now),'kind':'unknown_idle_over_one_hour','position':position,'status':result.get('status'),'message':'知识整理已超过1小时没有已保存的进展，原因尚未确认；请检查当前控制器与上游。未改模型、队列或计数。'}

def deliver_alert(root,event):
 import subprocess
 root=Path(root)
 script=root/'kb-daily-delivery.ts'
 subprocess.run(['sudo','docker','cp',str(script),'rakazo-api-1:/app/packages/adapters/kb-daily-delivery.ts'],check=True,capture_output=True,timeout=15)
 payload={'action':'deliver','from':event['at'],'until':event['at'],'nonce':'kb-continuity-'+hashlib.sha256(json.dumps(event['position']).encode()).hexdigest()[:20],'text':event['message']}
 p=subprocess.run(['sudo','docker','exec','-i','rakazo-api-1','/app/node_modules/.bin/tsx','/app/packages/adapters/kb-daily-delivery.ts'],input=json.dumps(payload),capture_output=True,text=True,timeout=60,check=True)
 data=json.loads((root/'idle-alert.json').read_text());data['delivered']=json.loads(p.stdout)
 tmp=root/'idle-alert.tmp';tmp.write_text(json.dumps(data));tmp.replace(root/'idle-alert.json')
 append(root,'continuity-events.jsonl',event)

def recover_blocked(mcp):
 lease=json.loads(mcp.tool('curation_writer_acquire',owner='首次全量阻塞核验'))
 if lease.get('status')!='ACQUIRED':return {'status':'DIAGNOSIS_BUSY'}
 try:return json.loads(mcp.tool('curation_prepare',writer_token=lease['token'],recover_blocked=True))
 finally:mcp.tool('curation_writer_release',token=lease['token'])

def adopt_suspension(root,mcp,state,config,receipt,atomic_json):
 fresh=__import__('curation_controller_policy').checkpoint_position(mcp.tool('read_note',path='90-Logs/首次全量整理/当前批次.md'))
 verify_previous(mcp,fresh[0])
 state.update(position=fresh,failures=0,no_progress=0,recovery_attempts=0,not_before=time.time()+10,pending_until=0)
 state.pop('blocked_reason',None);atomic_json(Path(root)/'state.json',state)
 append(root,'continuity-events.jsonl',{'at':iso(time.time()),'kind':'suspension_crash_recovered','receipt':receipt,'position':fresh})
 return {'status':'suspension_crash_recovered','receipt':receipt,'position':fresh}
