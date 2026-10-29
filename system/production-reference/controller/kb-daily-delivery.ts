import {createDb} from '/app/packages/db/src/client.ts';
import {createThreadMessageInTransaction,appendEventInTransaction,createThreadEvents} from '@rakazo/db';
import {readFileSync} from 'node:fs';
import {runAsTenant} from '/app/packages/db/src/row-security.ts';
function own(name:string){const v=process.env[name];if(!v)throw Error('Missing own setting: '+name);return v;}
const {prisma,pool}=createDb(process.env.DATABASE_URL!);let realtime:any=null;
async function main(){try{
 const input=JSON.parse(readFileSync(0,'utf8'));
 const routine=await prisma.routine.findUniqueOrThrow({where:{id:own('WIKI_REPORT_ROUTINE_ID')}});
 if(!routine.threadId)throw Error('Existing owner thread missing');
 const thread=await prisma.thread.findFirstOrThrow({where:{id:routine.threadId,spaceId:routine.spaceId,botId:routine.botId}});
 const from=new Date(input.from),until=new Date(input.until);
 const runs=await prisma.run.findMany({where:{routineId:{in:[own('WIKI_CURATION_ROUTINE_ID'),own('WIKI_DAILY_ROUTINE_ID'),own('WIKI_STRUCTURE_ROUTINE_ID'),routine.id]},createdAt:{lte:until},OR:[{createdAt:{gte:from}},{completedAt:{gte:from}},{status:{in:['running','leased','waiting_input','waiting_takeover']}}]},select:{id:true,routineId:true,status:true,error:true,createdAt:true,startedAt:true,completedAt:true},orderBy:{createdAt:'asc'}});
 if(input.action==='inspect'){
  const monthly=await prisma.routine.findFirst({where:{spaceId:routine.spaceId,name:'Obsidian 每月固定题验证',active:true}});
  const latest=monthly?await prisma.run.findFirst({where:{routineId:monthly.id,status:'completed'},orderBy:{createdAt:'desc'},select:{id:true,createdAt:true,startedAt:true,completedAt:true}}):null;
  const messages=latest?await prisma.message.findMany({where:{runId:latest.id,role:{in:['bot','assistant']}},orderBy:{createdAt:'asc'},select:{blocks:true}}):[];
  const dailyEffects=await prisma.externalEffect.findMany({where:{runId:{in:runs.filter(r=>r.routineId===own('WIKI_DAILY_ROUTINE_ID')).map(r=>r.id)}},orderBy:{createdAt:'asc'},select:{id:true,runId:true,kind:true,status:true,request:true,result:true,createdAt:true}});
  console.log(JSON.stringify({runs,dailyEffects,threadId:thread.id,recipient:'existing owner routine thread',routineActive:routine.active,monthly:latest?{run:latest,messages}:null}));
 }
 else if(input.action==='deliver'){
  const{PostgresRealtimeFanout}=await import('@rakazo/adapters');realtime=new PostgresRealtimeFanout({connectionString:process.env.REALTIME_DATABASE_URL??process.env.DATABASE_URL!,publisher:pool});
  if(typeof input.text!=='string'||input.text.length>4500||! /^(?:kb-daily-(?:test-)?\d{4}-\d{2}-\d{2}|kb-outage-[a-f0-9]{16}|kb-continuity-[a-f0-9]{20}|kb-confirmation-\d{4}-W\d{2})$/.test(input.nonce))throw Error('Invalid bounded report');
  const blocks=[{kind:'text' as const,text:input.text}];
  const result=await prisma.$transaction(async tx=>{
   await tx.$queryRaw`SELECT id FROM threads WHERE id = ${thread.id} FOR UPDATE`;
   const old=await tx.message.findUnique({where:{threadId_clientNonce:{threadId:thread.id,clientNonce:input.nonce}}});
   if(old)return {messageId:old.id,reused:true,eventSeq:null};
   const msg=await createThreadMessageInTransaction(tx,{threadId:thread.id,role:'system',blocks,botId:routine.botId,markUnread:true,clientNonce:input.nonce});
   const event=await appendEventInTransaction(tx,{spaceId:routine.spaceId,threadId:thread.id,botId:routine.botId,type:'thread.message.created',payload:{messageId:msg.id,role:'system',blocks}});
   return {messageId:msg.id,reused:false,eventSeq:event.seq};
  });
  if(result.eventSeq!==null)await createThreadEvents(prisma,realtime).notify(thread.id,result.eventSeq);
  const saved=await prisma.message.findUniqueOrThrow({where:{id:result.messageId}});if(JSON.stringify(saved.blocks)!==JSON.stringify(blocks))throw Error('Readback differs');
  const savedText=Array.isArray(saved.blocks)?saved.blocks.filter((b:any)=>b.kind==='text').map((b:any)=>b.text??'').join('\n'):'';
  const throughput=/每小时吞吐 (\d+(?:\.\d+)?) 条\/小时/.exec(savedText);
  const stopped=/停顿占比 (\d+(?:\.\d+)?)%/.exec(savedText);
  const metricsReadback={checkedAt:new Date().toISOString(),throughputPerHour:throughput?Number(throughput[1]):null,stoppedPercent:stopped?Number(stopped[1]):null,passed:!!throughput&&!!stopped};
  // Delivery remains independent of metric availability or structure-check success.
  // A missing numeric value is recorded as unverified, never silently changed to zero.
  console.log(JSON.stringify({...result,readback:true,recipient:'existing owner routine thread',metricsReadback}));
 }else throw Error('Unknown action');
}finally{await realtime?.close();await prisma.$disconnect();await pool.end();}
}
runAsTenant({spaceId:own('WIKI_SPACE_ID'),userId:own('WIKI_USER_ID')},main).catch(e=>{console.error(String(e));process.exitCode=1;});
