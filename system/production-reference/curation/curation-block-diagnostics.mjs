/** Source-scoped evidence; network, permissions and unfinished commits never authorize suspension. */
import {createHash} from 'node:crypto';
const sha=t=>createHash('sha256').update(t).digest('hex');
export function sourceFailureCategory(message){
 if(/^SOURCE_RUNTIME_CAPACITY:/.test(message))return 'model_capacity';
 if(/^SOURCE_RESPONSE_FORMAT_INVALID:/.test(message))return 'response_format';
 if(/^Missing note: /.test(message))return 'source_missing';
 if(/^(Invalid string length|Cannot create a string longer than|SOURCE_CAPACITY_BLOCKED:)/.test(message))return 'capacity_unsupported';
 if(/^SOURCE_PARSE_BLOCKED:/.test(message))return 'source_corrupt';
 return null;
}
export function createBlockDiagnostics({queue,store,vault,position,writerCheck,suspensions,isIndexReady=()=>true,now}){
 const key=i=>'source-block-diagnostic-'+i;
 const identity=(vs,pos)=>sha(JSON.stringify([vs,pos.text.match(/^current_part:.*$/m)?.[0]??'',pos.text.match(/^next_offset:.*$/m)?.[0]??'']));
 async function versions(record){
  const all=[];
  for(const path of record.paths){const text=await vault.readNote(path);all.push(text===null?{path,missing:true}:{path,sha256:sha(text)});}
  return all;
 }
 async function record(input,error){
  const category=sourceFailureCategory(String(error.message));
  if(!category||input.paths?.length||!input.writer_token||!isIndexReady())return;
  const pos=await position(),r=queue.records[pos.index];if(!r)return;
  if(category==='source_missing'&&!r.paths.includes(String(error.message).slice('Missing note: '.length)))return;
  const vs=await versions(r),inputIdentity=identity(vs,pos),old=await store.get(key(pos.index));
  const item=old?.input_version===inputIdentity?old:{index:pos.index,source_id:r.source_id,queue_version:queue.catalog_sha256,input_version:inputIdentity,versions:vs,attempts:[]};
  const run=sha(input.writer_token);
  if(!item.attempts.some(a=>a.run===run))item.attempts.push({run,at:now(),category,error:String(error.message).slice(0,500)});
  item.attempts=item.attempts.slice(-8);await store.set(key(pos.index),item);
 }
 async function recover(input){
  await writerCheck(input.writer_token);
  const pending=(await suspensions.summary()).find(r=>r.status==='APPLYING');
  if(pending)return suspensions.suspend({writer_token:input.writer_token,evidence_id:pending.evidence_id});
  if(!isIndexReady())return {status:'DIAGNOSIS_NOT_PROVEN',reason:'index_not_ready'};
  const pos=await position(),r=queue.records[pos.index],d=await store.get(key(pos.index));
  if(!r||!d||d.queue_version!==queue.catalog_sha256||d.source_id!==r.source_id)return {status:'DIAGNOSIS_NOT_PROVEN',reason:'no_fixed_input_error_evidence'};
  const vs=await versions(r);
  if(identity(vs,pos)!==d.input_version)return {status:'DIAGNOSIS_NOT_PROVEN',reason:'input_version_changed'};
  const last=d.attempts.slice(-3),categories=new Set(last.map(a=>a.category));
  if(!last.length||categories.size!==1)return {status:'DIAGNOSIS_NOT_PROVEN',reason:'three_independent_leases_and_ten_minutes_required'};
  // Permission or sync-service failure cannot be established from missing source text alone.
  for(const p of ['99-System/知识库规则.md','20-Wiki/知识索引.md'])if(typeof await vault.readNote(p)!=='string')return {status:'DIAGNOSIS_NOT_PROVEN',reason:'global_vault_read_failed'};
  for(const k of await store.keys())if(k.startsWith('commit-')){const j=await store.get(k);if(j?.prepared?.initial&&j.prepared.index===pos.index)return {status:'DIAGNOSIS_NOT_PROVEN',reason:'commit_journal_must_be_recovered'};}
  if(last.length<3)return {status:'DIAGNOSIS_RETRY',reason:'fixed_source_error_requires_independent_retry',attempts:last.length,retry_after_seconds:300};
  if(Date.parse(last[2].at)-Date.parse(last[0].at)<600000)return {status:'DIAGNOSIS_NOT_PROVEN',reason:'three_independent_leases_and_ten_minutes_required'};
  const id='auto-'+sha(queue.catalog_sha256+'\n'+r.source_id+'\n'+d.input_version).slice(0,40);
  const e={issuer:'maintenance-v19',verified:true,queue_version:queue.catalog_sha256,index:pos.index,source_id:r.source_id,paths:r.paths,versions:vs,category:last[0].category,
   reason:'同一输入版本在三个独立写入租约中可复现来源处理容量或响应格式阻塞；未判定资料损坏。最后错误：'+last[2].error,
   recovery_condition:'主队列结束后按原来源身份核查并补处理；修复读取/转换能力或补齐原文后复核',diagnostic_attempts:last,checkpoint:pos.text};
  await store.set('suspension-evidence-'+id,e);
  return suspensions.suspend({writer_token:input.writer_token,evidence_id:id});
 }
 return {record,recover};
}
