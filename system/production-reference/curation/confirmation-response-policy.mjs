/** Deterministic confirmation plumbing. No model calls, no interpretation of 整理助手 drafts. */
export const QUESTION_REGISTER='90-Logs/待确认/问题登记.md';
export const isResponsePath=p=>/^00-Inbox\/待确认答复\/[^/]+\.md$/.test(p);
const fm=text=>text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1]??'';
export function parseResponse(text,path){
  if(!isResponsePath(path)||!/^kind:\s*["']?confirmation-response["']?\s*$/m.test(fm(text)))return null;
  const match=text.match(/```json\s*\n([\s\S]*?)\n```/);
  let x;try{x=JSON.parse(match?.[1]);}catch{throw Error('CONFIRMATION_INVALID_JSON');}
  if(x?.kind!=='confirmation-response'||!/^Q-[\w-]+$/.test(x.question_id??'')||!x.question_version||!x.question_page||typeof x.answer!=='string'||!x.answer.trim()||!x.answer_source)throw Error('CONFIRMATION_REQUIRED_FIELDS');
  // Retain only owner response fields. 整理助手 drafts are never evidence or executable instructions.
  return Object.fromEntries(['kind','question_id','question_version','question_page','question','answer','answer_source','answer_source_sha256','answered_at','owner_action','supersedes'].filter(k=>x[k]!==undefined).map(k=>[k,x[k]]));
}
export function parseRegister(text){
  const m=text.match(/```json\s*\n([\s\S]*?)\n```/);let data;
  try{data=JSON.parse(m?.[1]);}catch{throw Error('CONFIRMATION_REGISTER_INVALID');}
  if(!data.questions||Array.isArray(data.questions))throw Error('CONFIRMATION_REGISTER_INVALID');
  return{data,match:m};
}
export function checkQuestion(response,text){
  const {data}=parseRegister(text),q=data.questions[response.question_id];
  if(!q)throw Error('CONFIRMATION_QUESTION_NOT_REGISTERED: '+response.question_id);
  if(q.version!==response.question_version)throw Error('CONFIRMATION_VERSION_MISMATCH: '+response.question_id);
  if(q.page!==response.question_page)throw Error('CONFIRMATION_PAGE_MISMATCH: '+response.question_id);
  if(q.response_path&&response.supersedes!==q.response_path&&q.response_answer!==response.answer)throw Error('CONFIRMATION_SUPERSEDES_REQUIRED: '+response.question_id);
  return q;
}
export function closeQuestion(response,text,path,at){
  checkQuestion(response,text);const {data,match}=parseRegister(text),q=data.questions[response.question_id];
  Object.assign(q,{state:'closed',resolution:'主人答复已核对采纳；保留答复原文和来源',response_path:path,response_answer:response.answer,response_source:response.answer_source,response_version:response.question_version,processed_at:at});
  return text.replace(match[0],()=>'```json\n'+JSON.stringify(data,null,2)+'\n```');
}
export function responseTiming(response,mtime,at){
  // Timestamp is scheduling metadata only, never an event date for knowledge claims.
  const raw=response.answered_at?.replace(/ (\d\d:\d\d:\d\d) ([+-]\d\d:\d\d)$/,'T$1$2');
  const entered=Number.isFinite(Date.parse(raw))?Date.parse(raw):Date.parse(mtime);
  const shifted=new Date(entered+8*3600_000),today=new Date(Date.parse(at)+8*3600_000).toISOString().slice(0,10);
  const due=new Date(Date.UTC(shifted.getUTCFullYear(),shifted.getUTCMonth(),shifted.getUTCDate()+(shifted.getUTCHours()>=21?1:0))).toISOString().slice(0,10);
  return{received_at:new Date(entered).toISOString(),due_day:due,due:due<=today,over_24h:Date.parse(at)-entered>24*3600_000};
}
export async function prioritizePending(rows,{read,metadata,at}){
  let register;const answers=[];
  for(const row of rows){
    if(!row.paths?.some(isResponsePath))continue;
    let response,timing,reason=null;
    try{
      if(row.paths.length!==1)throw Error('CONFIRMATION_ONE_RESPONSE_REQUIRED');
      response=parseResponse(await read(row.paths[0]),row.paths[0]);if(!response)throw Error('CONFIRMATION_KIND_REQUIRED');
      timing=responseTiming(response,(await metadata(row.paths[0]))?.mtime,at);
      register??=await read(QUESTION_REGISTER);checkQuestion(response,register);
      if(!timing.due)reason='AFTER_21_CUTOFF_NEXT_DAY';
    }catch(e){reason=String(e.message??e);}
    row.priority_class='confirmation-response';row.question_id=response?.question_id??null;row.question_version=response?.question_version??null;
    row.response_status={...timing,blocked_reason:reason};
    answers.push({source_id:row.source_id,path:row.paths[0],question_id:row.question_id,...row.response_status,reason:reason??'待执行：尚无本版本正式提交回执（不是预算内未审）'});
  }
  const rank=r=>r.priority_class==='confirmation-response'?0:r.kind==='会议录音'?1:r.paths?.some(p=>/^00-Inbox\/日记\//.test(p))?2:3;
  rows.sort((a,b)=>rank(a)-rank(b)||(a.response_status?.received_at??a.paths?.[0]??'').localeCompare(b.response_status?.received_at??b.paths?.[0]??''));
  return{answers,overdue:answers.filter(x=>x.over_24h),due:answers.filter(x=>x.due&&!x.blocked_reason).length,ordering:['confirmation-response','会议','日记','其他'],confirmation_budget:'not_part_of_ordinary_source_budget'};
}
