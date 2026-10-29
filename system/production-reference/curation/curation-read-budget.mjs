/** Read-size policy. Values are token/byte upper bounds, never claims of exact tokenization. */
const positive=x=>Number.isFinite(x)&&x>0;
export const FALLBACK_READ_CHARS=14000;
export const TOOL_RETURN_BYTE_LIMIT=150000;
export function selectReadPlan(settings){
 if(!settings||!positive(settings.context_window)||!positive(settings.max_output)||!Number.isFinite(settings.fixed_tokens)||settings.fixed_tokens<0)
  return {schema:1,mode:'legacy_fallback',chars:FALLBACK_READ_CHARS,reason:'missing_or_unreadable_server_model_settings'};
 const cap=positive(settings.verified_context_cap)?settings.verified_context_cap:settings.context_window;
 const runtime=positive(settings.runtime_context_window)?settings.runtime_context_window:settings.context_window;
 const window=Math.floor(Math.min(settings.context_window,cap,runtime));
 const reasoning=Number.isFinite(settings.reasoning_reserve)?Math.max(0,settings.reasoning_reserve):settings.max_output;
 const factor=settings.safety_factor===0.125?0.125:settings.safety_factor===0.25?0.25:0.5;
 const available=Math.floor((window-settings.fixed_tokens-settings.max_output-reasoning)*factor);
 if(available<1024)return {schema:1,mode:'legacy_fallback',chars:FALLBACK_READ_CHARS,reason:'insufficient_measured_window_headroom',settings};
 return {schema:1,mode:'adaptive',model:settings.model,window,configured_window:settings.context_window,runtime_window:runtime,configured_output:settings.max_output,
  fixed_tokens:settings.fixed_tokens,reasoning_reserve:reasoning,safety_factor:factor,source_byte_budget:available,
  cap_source:settings.cap_source??'resolved current model settings; relay-specific hard limit unverified',
  measured:settings.measured??null,byte_limit:TOOL_RETURN_BYTE_LIMIT};
}
/** Match the existing runtime navigation projection when measuring the actual tool body. */
export function projectedReadPayload(batch){
 const path='20-Wiki/知识索引.md',body=batch.common?.[path];
 if(batch.version!==6||typeof body!=='string'||Buffer.byteLength(body,'utf8')<=8192)return batch;
 const {[path]:_index,...common}=batch.common;
 return {...batch,common,common_navigation:{...batch.common_navigation,[path]:{
  path,sha256:batch.common_versions?.[path],total_chars:Array.from(body).length,preview:Array.from(body).slice(0,1600).join(''),full_coverage:false,
  instruction:'Global index navigation preview only, not a complete read. Source evidence and related page bodies are unchanged. Use read_note(path,offset=0,limit=2500) and follow next_offset if the full index is needed. Do not re-read the index merely to complete this source.'
 }}};
}
export function projectedReturnBytes(batch){const body=JSON.stringify(projectedReadPayload(batch));return Buffer.byteLength(JSON.stringify({content:[{type:'text',text:body}]}),'utf8');}
/** Read a smaller prefix only. Already staged offsets never move backwards. */
export function fitSourceSegment({content,offset,plan,media,read,makeBatch,ceiling}){
 let maximum=plan.mode==='adaptive'?Math.min(150000,Math.max(1,plan.source_byte_budget)):FALLBACK_READ_CHARS;
 if(Number.isInteger(ceiling)&&ceiling>0)maximum=Math.min(maximum,ceiling);
 const empty=read(content,offset,0,media),fixed=projectedReturnBytes(makeBatch(empty,0));
 if(fixed>=TOOL_RETURN_BYTE_LIMIT)throw Error('SOURCE_CAPACITY_BLOCKED: fixed preparation overhead exceeds bounded tool response; no source text silently clipped');
 let size=maximum;
 if(plan.mode==='adaptive'){
  const sampleSize=Math.min(maximum,FALLBACK_READ_CHARS),sample=read(content,offset,sampleSize,media);
  const density=Math.max(1,(projectedReturnBytes(makeBatch(sample,sampleSize))-fixed)/Math.max(1,sample.visible_chars));
  size=Math.max(1,Math.min(maximum,Math.floor((TOOL_RETURN_BYTE_LIMIT-fixed-2048)/(density*1.15))));
 }
 let low=1,high=size,best;
 while(low<=high){
  size=Math.floor((low+high)/2);
  const reading=read(content,offset,size,media);
  const sourceBytes=Buffer.byteLength(reading.segment,'utf8');
  const fits=(plan.mode!=='adaptive'||sourceBytes<=plan.source_byte_budget)&&projectedReturnBytes(makeBatch(reading,size))<=TOOL_RETURN_BYTE_LIMIT;
  if(fits){best={...reading,read_chars:size};low=size+1;}
  else high=size-1;
 }
 if(best)return best;
 throw Error('SOURCE_CAPACITY_BLOCKED: fixed preparation overhead exceeds bounded tool response; no source text silently clipped');
}
/** Cache only verified image intervals/statistics, keyed by exact raw source versions. */
export function createSourceParsingCache(){
 let current;
 return {get(key,compute){if(current?.key===key)return {...current.value,reused:true};const value=compute();current={key,value};return {...value,reused:false};},clear(){current=undefined;}};
}
