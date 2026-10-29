"""21:30 delivery independent of model runs and structure-check success."""
import os,datetime,importlib.util,json,pathlib,re,subprocess,sys
ROOT=pathlib.Path('/srv/wiki/controller');TZ=datetime.timezone(datetime.timedelta(hours=8));now=datetime.datetime.now(TZ);preview='--preview' in sys.argv;test='--test' in sys.argv or preview
if not test and (now.hour,now.minute)<(21,30):raise SystemExit('Not the report delivery time')
date=now.date().isoformat();cutoff=now if test else now.replace(hour=21,minute=25,second=0,microsecond=0);start=now.replace(hour=0,minute=0,second=0,microsecond=0)
spec=importlib.util.spec_from_file_location('vc',ROOT/'vault-client.py');vc=importlib.util.module_from_spec(spec);spec.loader.exec_module(vc)
cfg=json.loads((ROOT/'config.json').read_text());c=vc.MCP(cfg['mcp_url'],cfg['mcp_token'],timeout=45)
# Tenant-scoped helper is evaluated read-only; preview does not copy files into API.
def db(x):
 p=subprocess.run(['sudo','docker','exec','-i','rakazo-api-1','/app/node_modules/.bin/tsx','--eval',(ROOT/'kb-daily-delivery.ts').read_text()],input=json.dumps(x),text=True,capture_output=True,timeout=60)
 if p.returncode:raise RuntimeError(p.stderr[-1200:])
 return json.loads(p.stdout)
snapshot_path=('00-Test/v19-daily/' if test else '90-Logs/首次全量整理/日报/数据/')+date+'.md'
try:raw=c.tool('read_note',path=snapshot_path)
except Exception:raw=''
match=re.search(r'```json\n(.*?)\n```',raw,re.S);data=json.loads(match[1]) if match else None
if data and (data.get('date')!=date or bool(data.get('test'))!=test):data=None
ins=db({'action':'inspect','from':start.isoformat(),'until':now.isoformat()})
monthly_status=None
try:
 ms=importlib.util.spec_from_file_location('monthly_archive',ROOT/'kb-monthly-validation-archive.py');ma=importlib.util.module_from_spec(ms);ms.loader.exec_module(ma)
 monthly_status=ma.archive(c,ins.get('monthly'),preview=preview,prefix='00-Test/v19-daily/'if test else'')
except Exception as exc:monthly_status={'status':'FAILED','reason':str(exc)[:160]}
logs=subprocess.check_output(['sudo','journalctl','-u','personal-kb-curation-controller.service','--since',start.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),'--until',cutoff.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),'-o','json','--no-pager'],text=True,timeout=45)
events=[]
for line in logs.splitlines():
 try:
  entry=json.loads(json.loads(line)['MESSAGE'])
  if isinstance(entry,dict) and entry.get('at'):events.append(entry)
 except (ValueError,KeyError,TypeError):pass
full=[x for x in ins['runs'] if x['routineId']==os.environ['WIKI_CURATION_ROUTINE_ID'] and datetime.datetime.fromisoformat(x['createdAt'].replace('Z','+00:00'))<=cutoff]
retries=set()
for event in events:
 if event.get('status')!='recovery_backoff_scheduled':continue
 due=event.get('not_before');later=[x for x in full if due is not None and datetime.datetime.fromisoformat(x['createdAt'].replace('Z','+00:00')).timestamp()>=float(due)]
 if later:retries.add(later[0]['id'])
pauses={(x['at'],x['status']) for x in events if x.get('status') in ['failure_hold','retry_limit_hold','verification_hold','skill_version_hold','upstream_unavailable_hold']}
planned=0
for window in ROOT.glob('v19-oneoff-*/reservation.json'):
 w=json.loads(window.read_text());at=w.get('startedAt');planned+=int(at is not None and start.timestamp()<=at<=cutoff.timestamp())
window_log=ROOT/'maintenance-window-events.jsonl'
if window_log.exists():
 for line in window_log.read_text().splitlines():
  row=json.loads(line);at=datetime.datetime.fromisoformat(row['started_at']).timestamp();planned+=int(start.timestamp()<=at<=cutoff.timestamp())
structure=[x for x in ins['runs'] if x['routineId']==os.environ['WIKI_STRUCTURE_ROUTINE_ID']];latest=structure[-1] if structure else None
if latest and latest['status']=='completed':inspection='巡检任务已结束；是否通过以其巡检报告为准。'
elif latest:inspection='巡检未完成：'+latest['status']+'；'+str(latest.get('error')or'截至发送时尚未完成')[:160]
else:inspection='巡检未完成：当天尚未找到运行记录'+('。' if ins['routineActive'] else '，任务当前关闭。')
last_full=full[-1] if full else None
unpassed=[(last_full.get('error')or'全量运行失败')[:100]] if last_full and last_full['status']=='failed' else []
if latest and latest['status']=='failed':unpassed.append('结构巡检：'+str(latest.get('error')or'运行失败')[:100])
if data:unpassed+=['提交尚未完成：'+x['status'] for x in data.get('unfinished_commits',[]) if x.get('at','')[:10]==cutoff.astimezone(datetime.timezone.utc).date().isoformat()]
else:unpassed.append('21:25统计快照未生成或无法验证')
header=('【连续两天无进展】\n' if data and data['no_progress_48h'] else '')+('【配置验收快照，不是21:25日报】\n' if test else '')+date+' 知识库进度'
if data:counts=f"队列 {data['queue_start']} → {data['queue_end']}/{data['total']}；知识成功 +{data['knowledge']}（累计{data['success_total']}）；无知识 {data['no_knowledge']}；修复 +{data['repairs']}。"
else:counts='当天队列、知识成功、无知识和修复数：未知，不能按0计算。'
text=header+'\n'+counts+f'\n已启动重试 {len(retries)}；暂停 {len(pauses)+planned}（其中计划维护{planned}）。\n'+inspection+'\n新增未通过：'+('；'.join(dict.fromkeys(unpassed))[:350] if unpassed else '本次统计未发现。')+'\n'+('需要主人处理：暂不需要操作，未通过项先由维护者排查。' if unpassed or data and data['no_progress_48h'] else '需要主人处理：暂无。')
from curation_controller_policy import upstream_unavailable
affected=[x for x in full if x['status']=='failed' and upstream_unavailable(x.get('error'))]
if affected:text+='\n上游故障历史运行（不是资料失败；全量后补核）：'+('、'.join(x['id']for x in affected)or'无')+'；'+str(len(affected))+'条。来源需按对应提交回执核实，不补造编号。'
probe_file=ROOT/'relay-probes.jsonl'
probes=[]
if probe_file.exists():
 for line in probe_file.read_text().splitlines():
  row=json.loads(line);at=datetime.datetime.fromisoformat(row['at'].replace('Z','+00:00'))
  if start<=at<=cutoff:probes.append(row)
if probes:
 last=probes[-1];text+='\n中转站探测 '+str(len(probes))+' 次，最近 '+last['at']+'：'+('可用' if last['available'] else '不可用')+'；队列停滞单列。'
continuity_file=ROOT/'continuity-events.jsonl'
if continuity_file.exists():
 events2=[json.loads(line)for line in continuity_file.read_text().splitlines()]
 today_events=[e for e in events2 if start<=datetime.datetime.fromisoformat(e['at'].replace('Z','+00:00'))<=cutoff]
 restored=[e for e in today_events if e.get('kind')=='watchdog_recovery']
 held=[e for e in today_events if e.get('kind')=='source_suspended']
 text+='\n自动恢复 '+str(len(restored))+' 次；资料挂起 '+str(len(held))+' 项（不计成功）。'
 for e in restored:text+='\n恢复 '+e['at']+'，位置 '+str(e.get('position'))+'。'
def execution_metrics(runs,start,until):
 def ts(value):return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
 low,high=start.timestamp(),until.timestamp();span=max(0,high-low);known=[];uncertain=[]
 for run in runs:
  if not run.get('startedAt'):
   if run.get('status')not in ('queued','cancelled','failed'):uncertain.append((ts(run['createdAt']),high))
   continue
  began=ts(run['startedAt']);ended=run.get('completedAt')
  if ended:
   finish=ts(ended)
   if finish<began:uncertain.append((began,high))
   else:known.append((began,finish))
  elif run.get('status')in('running','leased'):known.append((began,high))
  else:uncertain.append((began,high))
 def union(intervals):
  merged=[]
  for a,b in sorted((max(low,a),min(high,b))for a,b in intervals if b>=a and b>low and a<high):
   if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
   else:merged.append((a,b))
  return sum(b-a for a,b in merged)
 active=union(known);unknown=max(0,union(known+uncertain)-active);stopped=max(0,span-active-unknown)
 return {'span_seconds':span,'executing_seconds':active,'stopped_seconds':stopped,'unknown_seconds':unknown,'stopped_percent':round(stopped/span*100,1)if span else None}
runtime=execution_metrics(full,start,cutoff)
if data and isinstance(data.get('queue_start'),int)and isinstance(data.get('queue_end'),int)and data['queue_end']>=data['queue_start']and runtime['span_seconds']:
 rate=str(round((data['queue_end']-data['queue_start'])/(runtime['span_seconds']/3600),2))+' 条/小时'
else:rate='未知（当天队列基线快照尚未生成或无法验证）'
text+='\n每小时吞吐 '+rate+'；停顿占比 '+str(runtime['stopped_percent'])+'%；执行时间未知 '+str(round(runtime['unknown_seconds']/60,1))+' 分钟。停顿按真实批次起止计算，含无批次运行的租约等待、重试和批次间隔；不是旧72小时有效观察口径，也不是CPU空闲率。'
try:
 handoff_file=ROOT/'large-source-handoff-20261020.json'
 if handoff_file.exists():
  handoff=json.loads(handoff_file.read_text());progress=json.loads(c.tool('curation_status'));done=held=waiting=unknown=0
  for item in handoff['rows']:
   if progress['next_index']<=item['index']:waiting+=1;continue
   proof=json.loads(c.tool('curation_verify',index=item['index'],verify_saved=True,require_completion=True))
   if proof.get('status')=='PASS'and proof.get('require_completion'):done+=1
   elif proof.get('status')=='SUSPENDED'and proof.get('disposition_verified'):held+=1
   else:unknown+=1
  text+='\n移交资料：共 '+str(len(handoff['rows']))+' 份，正式提交并核验 '+str(done)+'，已挂起待维护处理 '+str(held)+'，已登记待主队列到达 '+str(waiting)+'，状态待核 '+str(unknown)+'。移交和阅读不计成功；全部完成前不报全量完成。'
  if handoff.get('report_path'):text+='\n本轮复核：[['+handoff['report_path']+']]。'
except Exception as exc:text+='\n移交资料进度：暂未核实，'+str(exc)[:100]+'。'
# The Worker writes numeric-only observations using its current local model settings.
# Read it only at report time; no new timer, database access or dependency on Codex.
try:
 b=subprocess.run(['sudo','docker','exec','rakazo-worker-1','cat','/data/curation-read-budget-last.json'],capture_output=True,text=True,timeout=15)
 if b.returncode==0:
  measured=json.loads(b.stdout)
  if isinstance(measured.get('chars'),int)and measured['chars']>0 and measured.get('at'):
   (ROOT/'adaptive-budget-last.json').write_text(json.dumps(measured,ensure_ascii=False))
except (ValueError,OSError,subprocess.TimeoutExpired):pass
budget_file=ROOT/'adaptive-budget-last.json'
if budget_file.exists():
 budget=json.loads(budget_file.read_text());text+='\n实际读取预算：'+str(budget.get('chars',budget.get('max_chars','未知')))+' 字符；记录于 '+str(budget.get('at','未知'))+'。'
 if isinstance(budget.get('visible_chars'),int):text+=' 本段可见文字 '+str(budget['visible_chars'])+' 字符（略过的图片内容未读）。'
 plan=budget.get('plan',{})
 if plan.get('mode')=='adaptive':
  text+=' 预算依据：（有效窗口 '+str(plan.get('window'))+' − 固定开销 '+str(plan.get('fixed_tokens'))+' − 输出预留 '+str(plan.get('configured_output'))+' − 推理预留 '+str(plan.get('reasoning_reserve'))+'）× '+str(plan.get('safety_factor'))+' = '+str(plan.get('source_byte_budget'))+' 的保守字节预算；再按UTF-8正文和工具回包150000字节上限缩小。窗口开销是估算，字节不等于token或汉字。'
 if budget.get('plan',{}).get('mode')=='legacy_fallback':text+=' 回退原因：'+str(budget['plan'].get('reason','设置不可读或该资料已沿旧断点开始'))+'。'
else:text+='\n实际读取预算：当前常规读取14000字符；自适应发布后的实际值以每条断点记录为准。'
text+='\n统计截至 '+(data['cutoff'] if data else cutoff.isoformat())+'；模型内部重试及未留审计记录的手动暂停无法统计。'
try:
 spec_metrics=importlib.util.spec_from_file_location('deepseek_metrics',ROOT/'deepseek-observability.py');metrics=importlib.util.module_from_spec(spec_metrics);spec_metrics.loader.exec_module(metrics)
 captured=[]
 for container in ['rakazo-api-1','rakazo-worker-1']:
  p=subprocess.run(['sudo','docker','logs','--since',start.astimezone(datetime.timezone.utc).isoformat(),'--until',cutoff.astimezone(datetime.timezone.utc).isoformat(),container],capture_output=True,text=True,timeout=40)
  if p.returncode:raise RuntimeError('无法完整读取 '+container+' 日志')
  captured.append(p.stdout+'\n'+p.stderr)
 captured.append(metrics.archived_between(ROOT,start,cutoff))
 startup=ROOT/'deepseek-startup-events.jsonl'
 if startup.exists():
  for line in startup.read_text().splitlines():
   row=json.loads(line)
   if start<=datetime.datetime.fromisoformat(row['at'].replace('Z','+00:00'))<=cutoff:captured.append('DEEPSEEK_STARTUP_CHECK '+json.dumps(row))
 text+='\n'+metrics.format_metrics(metrics.summarize('\n'.join(captured)))
except Exception as exc:text+='\nDeepSeek 短重试、救回、最高输出和触顶统计：未知；'+str(exc)[:140]+'。日报照常发送。'
try:
 release_path=ROOT/'deepseek-idle-release-result.json'
 release=json.loads(release_path.read_text())if release_path.exists()else{'status':'WAIT_IDLE_BOUNDARY'}
 text+='\nDeepSeek 本轮发布：'+str(release.get('status','未知'))+'；压缩真实回放：'+str(release.get('compaction_replay',{}).get('status','待验收'))+'。维护补充：[[90-Logs/首次全量整理/日报/补充-2026-10-19]]。'
except Exception:text+='\nDeepSeek 发布回执：暂不可读，未按完成计算。'
from importlib.util import spec_from_file_location,module_from_spec
sp=spec_from_file_location('weekly_confirmation',ROOT/'kb-confirmation-summary.py');qm=module_from_spec(sp);sp.loader.exec_module(qm)
text+=qm.weekly(c,now)
# Daily-task tool effects must match durable receipts. Manual maintenance is separate.
try:
 from daily_ingestion_audit import inspect as inspect_ingestion,format_report
 pending=json.loads(c.tool('curation_pending'))
 ingestion=inspect_ingestion(ins,pending,now)
 text+=format_report(ingestion)
 if not preview:
  ad=ROOT/'daily-ingestion-audit';ad.mkdir(mode=0o700,exist_ok=True)
  ap=ad/(date+'.json');tmp=ap.with_suffix('.tmp')
  tmp.write_text(json.dumps(ingestion,ensure_ascii=False,indent=2));tmp.replace(ap)
except Exception as exc:text+='\n【每日入库审计失败】'+str(exc)[:180]+'；不能把任务completed或手工入库视为每日任务验收。'

if monthly_status and monthly_status.get('status')=='FAILED':text+='\n月度验证归档未完成：'+monthly_status['reason']+'；日报照常发送。'
if preview:print(json.dumps({'status':'PREVIEW_ONLY','text':text,'monthly_archive':monthly_status},ensure_ascii=False));raise SystemExit()
path=('00-Test/v19-daily/report-' if test else '90-Logs/首次全量整理/日报/')+date+'.md'
old=c.tool('read_note',path=path)
if not old.startswith('Note not found:'):
 saved=old.split('\n\n---\n\n',1)[1];text=saved.removeprefix('# 日报\n\n').rstrip()
else:
 body='# 日报\n\n'+text+'\n';c.tool('write_note',path=path,content=body);assert c.tool('read_note',path=path).split('\n\n---\n\n',1)[1]==body
delivery_text=text if len(text)<=4400 else text[:4200].rsplit('\n',1)[0]+'\n完整日报及全部超时答复编号/阻塞原因：[['+path+']]（消息仅摘录，日报未省略）。'
result=db({'action':'deliver','text':delivery_text,'nonce':'kb-daily-'+('test-' if test else '')+date,'from':start.isoformat(),'until':now.isoformat()})
evidence=ROOT/'daily-report-evidence';evidence.mkdir(mode=0o700,exist_ok=True);(evidence/(('test-' if test else '')+date+'.json')).write_text(json.dumps({'path':path,'delivery':result,'snapshot':snapshot_path,'reportedAt':now.isoformat()},ensure_ascii=False,indent=2))
print(json.dumps({'path':path,'delivery':result,'text':text},ensure_ascii=False))
