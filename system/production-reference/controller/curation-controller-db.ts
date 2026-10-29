import {createDb} from '/app/packages/db/src/client.ts';
import {runAsTenant} from '/app/packages/db/src/row-security.ts';
import {GraphileJobPublisher} from '/app/packages/adapters/src/wakeup.ts';
import {routineWakeupJob,routineJobKey} from '@rakazo/adapter-kit';
import {commentTicket} from '/app/packages/adapters/src/ticket-tools.ts';
import {readFileSync} from 'node:fs';
const {prisma,pool}=createDb(process.env.DATABASE_URL!);
const jobs=new GraphileJobPublisher(pool);
function required(name:string){const value=process.env[name];if(!value||value.startsWith('CONFIGURE_'))throw Error('Missing own deployment setting: '+name);return value;}
const routineId=required('WIKI_ROUTINE_ID');
const actor={spaceId:required('WIKI_SPACE_ID'),userId:required('WIKI_USER_ID')};
const statuses=['queued','leased','running','waiting_input','waiting_takeover'];
const marker='[personal-kb-continuous-curation-v4]';
// A previous owner's approval must never authorize a new installation.
const watchdogApproval=new Date(process.env.WIKI_WATCHDOG_APPROVED_AT||'1970-01-01T00:00:00Z');
if(!Number.isFinite(watchdogApproval.getTime()))throw Error('Invalid WIKI_WATCHDOG_APPROVED_AT');
function correlatedRelayProof(proof:any,probe:any,recovery:any,latest:any,model:string,now:number){
 return latest?.error==='Upstream request failed' && proof?.runId===latest.id &&
 !!process.env.WIKI_RELAY_PROOF_REQUEST_ID && proof?.requestId===process.env.WIKI_RELAY_PROOF_REQUEST_ID && proof?.model===model &&
 proof?.status===502 && proof?.upstreamStatus===502 &&
 Math.abs(Date.parse(proof?.requestAt)-new Date(latest.completedAt).getTime())<=5000 &&
 recovery?.enabled===true && recovery?.latest===latest.id &&
 probe?.fingerprint===recovery?.fingerprint && probe?.model===model &&
 probe?.available===true && probe?.status===200 &&
 Number.isFinite(Date.parse(probe?.at)) && now-Date.parse(probe.at)>=0 && now-Date.parse(probe.at)<=3600000;
}
function hasCorrelatedRelayProof(latest:any,model:string){
 try {
  const root='/app/packages/adapters/';
  const proof=JSON.parse(readFileSync(root+'relay-failure-proof.json','utf8'));
  const recovery=JSON.parse(readFileSync(root+'relay-recovery-proof.json','utf8'));
  const probe=JSON.parse(readFileSync(root+'relay-probe-proof.json','utf8'));
  return correlatedRelayProof(proof,probe,recovery,latest,model,Date.now());
 }catch{return false;}
}
async function main(){
try {
 const input=JSON.parse(readFileSync(0,'utf8')||'{"action":"snapshot"}');
 const getSnapshot=async()=>{
  const routine=await prisma.routine.findFirstOrThrow({where:{...actor,id:routineId},select:{id:true,botId:true,active:true,nextRunAt:true,updatedAt:true,prompt:true,crons:true}});
  const latest=await prisma.run.findFirst({where:{routineId},orderBy:{createdAt:'desc'},select:{id:true,status:true,createdAt:true,startedAt:true,completedAt:true,error:true}});
  // Strict evidence: exactly one external effect, an acknowledged writer BUSY.
  // A failed run, tool error, ACQUIRED response or any other effect is not exempt.
  let writerContention=false;
  if(latest?.status==='completed'){
   const effects=await prisma.externalEffect.findMany({where:{runId:latest.id},take:2,select:{kind:true,status:true,result:true}});
   if(effects.length===1&&effects[0].kind==='mcp__wiki-mcp__curation_writer_acquire'&&effects[0].status==='completed'){
    const value=effects[0].result as any;
    try{const v=JSON.parse((value?.content??[]).filter((b:any)=>b.type==='text').map((b:any)=>b.text).join(''));
     writerContention=!value?.isError&&v.status==='BUSY'&&typeof v.owner==='string'&&Number.isFinite(Date.parse(v.expires));
    }catch{}
   }
  }
  const active=await prisma.run.findMany({where:{spaceId:actor.spaceId,botId:routine.botId,status:{in:statuses}},select:{id:true,status:true,routineId:true}});
  const ownerEvent=await prisma.event.findFirst({where:{botId:routine.botId,type:'routine.updated',createdAt:{gte:watchdogApproval},payload:{path:['routineId'],equals:routineId}},orderBy:{createdAt:'desc'},select:{id:true,payload:true,createdAt:true}});
  const bot=await prisma.bot.findUnique({where:{id:routine.botId},select:{archivedAt:true}});
  const expected=input.expectedSkills??{};
  const crypto=await import('node:crypto');
  const skills=Object.keys(expected).length?await prisma.agentSkill.findMany({where:{...actor,name:{in:Object.keys(expected)}},select:{name:true,content:true}}):[];
  const schedules=await prisma.routine.findMany({where:{...actor,botId:routine.botId,name:{startsWith:'Obsidian '}},select:{name:true,active:true,crons:true,timezone:true,nextRunAt:true,modelId:true}});
  const mismatches=Object.entries(expected).filter(([name,sha])=>skills.filter(s=>s.name===name&&crypto.createHash('sha256').update(s.content).digest('hex')===sha).length!==1).map(([name])=>name);
  return {owner_pause:ownerEvent?.payload?.active===false,owner_pause_event:ownerEvent?.id??null,archived:!!bot?.archivedAt,routine:{...routine,prompt:undefined,continuous:routine.prompt.includes(marker),historical_only:routine.prompt.includes('[personal-kb-history-only-v1]')},latest:latest?{...latest,writer_contention:writerContention}:null,active,schedules,skill_sync:{checked:Object.keys(expected).length,mismatches}};
 };
 if(input.action==='snapshot')console.log(JSON.stringify(await getSnapshot()));
 else if(input.action==='schedule'||input.action==='pause'||input.action==='recover'||input.action==='watchdog'){
  if(['recover','watchdog'].includes(input.action)&&(!process.env.WIKI_WATCHDOG_APPROVED_AT||process.env.WIKI_ENABLE_AUTO_RECOVERY!=='true'))throw Error('Own explicit auto-recovery approval required');
  const due=new Date(input.due??Date.now());
  if(!Number.isFinite(due.getTime())||due.getTime()>Date.now()+26*3600_000)throw Error('Invalid due date');
  const outcome=await prisma.$transaction(async tx=>{
   const routine=await tx.routine.findFirstOrThrow({where:{...actor,id:routineId}});
   if(!routine.prompt.includes(marker)||(!['recover','watchdog'].includes(input.action)&&!routine.active))return {status:'not_active_or_not_managed'};
   if(input.action==='watchdog'){
    if(!input.expectedRoutineUpdatedAt||routine.updatedAt.toISOString()!==input.expectedRoutineUpdatedAt)throw Error('CURATION_BUSY_OR_NEW_RUN');
    const owner=await tx.event.findFirst({where:{botId:routine.botId,type:'routine.updated',createdAt:{gte:watchdogApproval},payload:{path:['routineId'],equals:routineId}},orderBy:{createdAt:'desc'},select:{id:true,payload:true}});
    if((owner?.id??null)!==(input.expectedOwnerPauseEvent??null)||owner?.payload?.active===false)throw Error('OWNER_PAUSE_WINS');
    if((await tx.bot.findUnique({where:{id:routine.botId},select:{archivedAt:true}}))?.archivedAt)throw Error('OWNER_PAUSE_WINS');
   }
   if(input.action==='recover'){
    if(routine.active)return {status:'already_active',botId:routine.botId,due:routine.nextRunAt};
    if(!input.expectedRoutineUpdatedAt||routine.updatedAt.toISOString()!==input.expectedRoutineUpdatedAt)throw Error('CURATION_BUSY_OR_NEW_RUN');
    const latest=await tx.run.findFirst({where:{routineId},orderBy:{createdAt:'desc'},select:{id:true,status:true,error:true,completedAt:true}});
    if((latest?.id??null)!==(input.expectedLatest??null)||latest?.status!=='failed')throw Error('CURATION_BUSY_OR_NEW_RUN');
    if(!/(?:\b(?:429|500|502|503|504|520|521|524)\b|temporarily unavailable|upstream[_ ]error|timeout|timed out|connection reset|stream .*interrupt|HTTP\/2 stream failed|stream ended without finish_reason|upstream_stream_truncated|^Request could not be completed\.?$|service unavailable|rate limit|No healthy Grok OAuth account is currently available|no (?:healthy|available).*oauth account|account pool (?:exhausted|empty)|(?:upstream|oauth|provider account).*\b(?:401|403)\b|\b(?:401|403)\b.*(?:upstream|oauth|provider account))/i.test(String(latest.error??''))&&!hasCorrelatedRelayProof(latest,routine.modelId??''))throw Error('RECOVERY_NOT_ELIGIBLE');
   }
   let modelFields={};
   if((input.action==='schedule'||input.action==='recover')&&!input.preserveModel&&routine.prompt.includes('[personal-kb-follow-connected-model-v1]')){
    const connected=await tx.spaceModelPreference.findFirst({where:{...actor,isDefault:true},include:{credential:true}});
    if(!connected?.modelId)throw Error('Connected default model unavailable; do not dispatch stale fixed model');
    modelFields={modelProvider:connected.credential.provider,modelId:connected.modelId,thinkingLevel:connected.thinkingLevel};
   }
   const data=input.action==='pause'?{active:false,nextRunAt:null}:['recover','watchdog'].includes(input.action)?{active:true,nextRunAt:due,...modelFields}:{nextRunAt:due,...modelFields};
   const changed=await tx.routine.updateMany({where:{id:routineId,active:routine.active,updatedAt:routine.updatedAt},data});
   if(changed.count!==1)throw Error('Controller state changed concurrently');
   // Updating the routine row uses the same lock as wakeRoutine. Roll back on a race.
   const active=await tx.run.count({where:{spaceId:actor.spaceId,botId:routine.botId,status:{in:statuses}}});
   const latest=await tx.run.findFirst({where:{routineId},orderBy:{createdAt:'desc'},select:{id:true}});
   if(active||(latest?.id??null)!==(input.expectedLatest??null))throw Error('CURATION_BUSY_OR_NEW_RUN');
   return {status:input.action==='pause'?'paused':input.action==='recover'||(input.action==='watchdog'&&!routine.active)?'recovered':'scheduled',botId:routine.botId,due:input.action==='pause'?null:due};
  });
  if(outcome.status==='scheduled'||outcome.status==='recovered')await jobs.enqueue(routineWakeupJob(routineId,due));
  if(outcome.status==='paused'){
   await jobs.cancel(routineJobKey(routineId));
   await commentTicket({prisma,ticketBoardEnabled:true},{...actor,botId:outcome.botId!,id:required('WIKI_BOARD_TICKET_ID'),body:'连续整理程序暂停新批次：'+String(input.reason??'需最终验收').slice(0,1800)+'。保留原文、已有成果和断点；这不表示全量整理已验收完成。'});
  }
  console.log(JSON.stringify(outcome));
 }else throw Error('Unknown controller action');
}catch(e){
 if(e instanceof Error&&e.message==='CURATION_BUSY_OR_NEW_RUN')console.log(JSON.stringify({status:'busy_or_changed'}));
 else throw e;
}finally{await jobs.close();await prisma.$disconnect();await pool.end();}
}
runAsTenant(actor,main).catch(e=>{console.error(String(e));process.exitCode=1;});
