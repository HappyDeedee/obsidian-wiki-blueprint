/** Queue dispositions are durable, separate from successful knowledge commits. */
import {createHash} from 'node:crypto';
const digest=t=>createHash('sha256').update(t).digest('hex');
export const SUSPENSION_VIEW='90-Logs/首次全量整理/挂起清单.md';
const key=i=>'suspension-index-'+i;
export async function suspensionSummary(store,queue){
  const rows=[];
  for(const k of (store.keys?await store.keys():[]))if(k.startsWith('suspension-index-')){
    const r=await store.get(k);
    if(r?.queue_version===queue.catalog_sha256)rows.push(r);
  }
  return rows.sort((a,b)=>a.index-b.index);
}
export async function verifySuspension({store,queue,vault,index}){
  const r=await store.get(key(index));
  if(!r||r.queue_version!==queue.catalog_sha256||r.source_id!==queue.records[index]?.source_id)return null;
  if(r.status==='RESOLVED')return null;
  const cp=await vault.readNote('90-Logs/首次全量整理/当前批次.md');
  const view=await vault.readNote(SUSPENSION_VIEW);
  const advanced=cp?.includes(queue.catalog_sha256)&&Number(cp.match(/^next_index:\s*(\d+)/m)?.[1])>index;
  return {status:r.status==='SUSPENDED'&&advanced&&view?.includes(r.id)?'SUSPENDED':'FAIL',
    disposition:'suspended',index,source_id:r.source_id,require_completion:false,counts_as_knowledge:false,
    disposition_verified:!!(r.status==='SUSPENDED'&&advanced&&view?.includes(r.id)),
    issues:advanced?[]:['SUSPENSION_CHECKPOINT_NOT_ADVANCED'],receipt:r.id,final_audit_required:true};
}
export function createSuspensions({queue,store,vault,put,position,checkpoint,writerCheck,now}){
  async function render(){
    const rows=await suspensionSummary(store,queue);
    await put(SUSPENSION_VIEW,'# 首次全量挂起清单\n\n由程序回执生成；挂起不计成功，原件保留。主队列遍历结束不等于全量完成。\n\n'+rows.map(r=>
      `## ${r.id}\n\n- 来源：${r.source_id}\n- 队列位置：${r.index}\n- 状态：${r.status}\n- 原因：${r.reason}\n- 输入版本：${JSON.stringify(r.versions)}\n- 证据：${r.evidence_id}\n- 恢复条件：${r.recovery_condition}\n- 首次挂起：${r.at}\n${r.category==='owner_handoff'?'- 移交登记：'+r.registered_at+'\n- 已有断点：'+JSON.stringify(r.handoff_checkpoint)+'\n':''}${r.paths.map(p=>'[['+p+']]').join('\n')}\n`).join('\n'));
  }
  async function suspend(input){
    await writerCheck(input.writer_token);
    if(!/^[a-zA-Z0-9_-]{8,100}$/.test(input.evidence_id??''))throw Error('Validated evidence ID required');
    const e=await store.get('suspension-evidence-'+input.evidence_id);
    // Evidence is provisioned by the maintenance diagnostic outside note/MCP writes.
    // A model cannot authorize suspension by asserting that its own failure is a bad source.
    if(!e||e.issuer!=='maintenance-v19'||!e.verified||e.queue_version!==queue.catalog_sha256||
       !['source_corrupt','source_missing','capacity_unsupported','model_capacity','response_format','owner_handoff'].includes(e.category))throw Error('No validated source-block evidence');
    if(e.category==='owner_handoff'&&(!e.owner_approved||!(e.effective_chars>1000000)||!e.registered_at))throw Error('Owner-approved large-source handoff evidence required');
    const record=queue.records[e.index];
    if(!record||record.source_id!==e.source_id||JSON.stringify(record.paths)!==JSON.stringify(e.paths)||!e.reason?.trim()||!e.recovery_condition?.trim())throw Error('Evidence scope mismatch');
    let r=await store.get(key(e.index));
    if(r&&r.evidence_id!==input.evidence_id)throw Error('Existing suspension: retain original receipt');
    if(r?.status==='RESOLVED')return {status:'RESOLVED',reused:true,id:r.id};
    if(!r){
      const pos=await position();if(pos.index!==e.index)throw Error('Suspension must match current queue index');
      for(const k of await store.keys())if(k.startsWith('commit-')){
        const j=await store.get(k);
        if(j?.prepared?.initial&&j.prepared.index===e.index)throw Error('Commit journal exists: recover/verify it before suspension');
      }
      if(e.versions?.length!==record.paths.length)throw Error('Every source part needs a version or explicit missing marker');
      for(const path of record.paths){
        const v=e.versions.find(v=>v.path===path),text=await vault.readNote(path);
        if(!v||(text===null?v.missing!==true:v.sha256!==digest(text)))throw Error('Evidence input version changed');
      }
      let handoff_checkpoint=e.handoff_checkpoint;
      if(e.category==='owner_handoff'&&e.segment_key){
        const stage=await store.get('segment-'+e.segment_key);
        handoff_checkpoint={segment_key:e.segment_key,offset:stage?.offset??0,chunks:stage?.chunks?.length??0,summary_sha256:digest(stage?.summary??'')};
      }
      r={id:'S-'+digest(queue.catalog_sha256+'\n'+e.source_id+'\n'+input.evidence_id).slice(0,20),index:e.index,
        source_id:e.source_id,queue_version:queue.catalog_sha256,paths:record.paths,versions:e.versions,
        evidence_id:input.evidence_id,category:e.category,registered_at:e.registered_at,handoff_checkpoint,reason:e.reason,recovery_condition:e.recovery_condition,at:now(),status:'APPLYING',
        before_checkpoint:pos.text,after_checkpoint:checkpoint(e.index+1,0,0,'资料已挂起，不计成功；见 [['+SUSPENSION_VIEW+']]。')};
      await store.set(key(e.index),r);
    }
    if(r.status==='SUSPENDED'){await render();return {status:'SUSPENDED',id:r.id,reused:true,counts_as_knowledge:false};}
    const current=await position();
    if(current.text!==r.after_checkpoint){
      if(current.text!==r.before_checkpoint)throw Error('Checkpoint changed: suspension recovery held');
      await put('90-Logs/首次全量整理/当前批次.md',r.after_checkpoint);
    }
    r.status='SUSPENDED';await store.set(key(e.index),r);await render();
    return {status:'SUSPENDED',id:r.id,index:r.index,next_index:r.index+1,counts_as_knowledge:false};
  }
  async function resolve(index,commitKey){
    const r=await store.get(key(index));if(!r)return;if(r.status==='RESOLVED'){await render();return;}
    const j=await store.get('commit-'+commitKey);
    if(j?.status!=='PASS'||!j.maintenance_finalized||j.prepared.record.source_id!==r.source_id)throw Error('Verified backfill commit required');
    r.status='RESOLVED';r.resolved_at=now();r.resolution_commit=commitKey;
    r.resolution_versions=j.prepared.snapshots.map(({text,...v})=>v);
    await store.set(key(index),r);await render();
  }
  return {suspend,resolve,render,summary:()=>suspensionSummary(store,queue)};
}
