"""Read-only daily-task audit. Manual commits and task completion are not proof.

Input: existing routine run/tool-effect records plus curation_pending's durable
audit. No database, note writes, task dispatch or model call occurs here.
"""
import datetime,json,os
DAILY=os.environ.get('WIKI_DAILY_ROUTINE_ID','CONFIGURE_YOUR_DAILY_ROUTINE_ID')
TZ=datetime.timezone(datetime.timedelta(hours=8))

def stamp(value):
    return datetime.datetime.fromisoformat(value.replace('Z','+00:00'))

def objects(value):
    if isinstance(value,str):
        try:yield from objects(json.loads(value))
        except (ValueError,TypeError):pass
    elif isinstance(value,list):
        for item in value:yield from objects(item)
    elif isinstance(value,dict):
        yield value
        for child in value.values():yield from objects(child)

def inspect(ins,pending,now,require_durable=True):
    today=now.astimezone(TZ).date().isoformat()
    runs={r['id']:r for r in ins.get('runs',[])if r.get('routineId')==DAILY and stamp(r['createdAt']).astimezone(TZ).date().isoformat()==today}
    audit=pending.get('daily_ingestion',{})
    receipts=[r for r in audit.get('receipts',[])if stamp(r['at']).astimezone(TZ).date().isoformat()==today]
    matched=set();seen=set();accepted=[];unverified=[];errors=[]
    for e in ins.get('dailyEffects',[]):
        if e.get('runId')not in runs:continue
        req=e.get('request',{})
        # Do not recognize a tool merely because the model mentioned it.
        routed='curation_commit'in str(e.get('kind','')) or any(o.get('toolName')=='curation_commit'for o in objects(req))
        if not routed:continue
        args=next((o['args']for o in objects(req)if isinstance(o.get('args'),dict)and'token'in o['args']),{})
        result=next((o for o in objects(e.get('result'))if o.get('status')=='PASS'and o.get('operation')in('ingest','topic_repair')and o.get('disposition')=='knowledge'),None)
        if not result:
            errors.append({'effect':e.get('id'),'status':e.get('status'),'reason':str(e.get('result'))[:220]});continue
        if result.get('reused'):continue
        signature=args.get('token')or(result.get('source_id'),e.get('id'))
        if signature in seen:continue
        seen.add(signature)
        choices=[r for r in receipts if r.get('key')not in matched and r.get('source_id')==result.get('source_id')and r.get('operation')==result['operation']and abs((stamp(e['createdAt'])-stamp(r['at'])).total_seconds())<=600]
        if len(choices)==1:
            matched.add(choices[0]['key']);key=choices[0]['key']
        elif require_durable:
            unverified.append({'effect':e.get('id'),'source_id':result.get('source_id'),'reason':'没有唯一匹配的持久提交回执','matches':len(choices)});continue
        else:key=None
        accepted.append({'run':e['runId'],'effect':e.get('id'),'source_id':result.get('source_id'),'key':key,'operation':result['operation'],'paths':result.get('knowledge_paths',[])})
    before_run=now.astimezone(TZ).hour<21 and not runs
    overdue=pending.get('confirmation_priority',{}).get('overdue',audit.get('overdue_answers',[]))
    count=len(accepted);waiting=len(pending.get('pending',[]))
    state='NOT_DUE'if before_run else'UNVERIFIED'if unverified else'FAULT_ZERO_INGEST'if waiting and count==0 else'PASS'
    return{'day':today,'status':state,'actual_daily_commits':count,'daily_run_ids':list(runs),'verified_commits':accepted,'other_finalized_commits':len([r for r in receipts if r['key']not in matched]),'pending_count':waiting,'overdue_answers':overdue,'unverified_effects':unverified,'failed_commit_effects':errors,'basis':'daily routine run -> successful curation_commit tool effect -> unique durable receipt; manual commits excluded'}

def format_report(a):
    state={'NOT_DUE':'今晚21:00任务尚未到点，不能提前验收','FAULT_ZERO_INGEST':'【每日入库故障】未整理非空但任务实际入库为0','UNVERIFIED':'【每日入库待核】工具回执与持久提交尚未唯一对应','PASS':'已按真实提交回执核对'}[a['status']]
    text='\n每日整理实际入库 '+str(a['actual_daily_commits'])+' 项；未归属本任务的提交 '+str(a['other_finalized_commits'])+' 项（不计每日任务）；未整理 '+str(a['pending_count'])+' 项。'+state+'。'
    overdue=a['overdue_answers']
    if not overdue:return text+'\n超过24小时未消化答复：0。'
    text+='\n【超24小时答复】共 '+str(len(overdue))+' 项：'
    for x in overdue:
        reason=x.get('blocked_reason')or x.get('reason')or x.get('status')or'原因待查'
        if '预算'in str(reason):reason='不能以预算作为答复阻塞理由；需查实际处理失败点'
        text+='\n'+str(x.get('question_id')or x.get('path')or'编号待核')+'；收到 '+str(x.get('received_at')or x.get('mtime')or'时间待核')+'；'+str(reason)[:120]
    return text
