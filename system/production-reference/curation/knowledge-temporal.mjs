/** Shared, deliberately flat date contract for curation, navigation and health. */
import {createHash} from 'node:crypto';
export const TEMPORAL_VERSION='2026-10-17.1';
export const DATE_FIELDS=['event_date','source_date','valid_from','valid_until','review_after'];
const digest=s=>createHash('sha256').update(s).digest('hex');
export const shanghaiDay=(instant=new Date())=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(instant));
export function validDay(value){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value)||value<'0001-01-01')return false;
  const d=new Date(value+'T00:00:00Z');
  return Number.isFinite(+d)&&d.toISOString().slice(0,10)===value;
}
export function parseTemporal(text,today=shanghaiDay()){
  const front=String(text??'').replace(/^\uFEFF/,'').replace(/\r\n/g,'\n').match(/^---[ \t]*\n([\s\S]*?)\n---[ \t]*(?:\n|$)/)?.[1]??'';
  const values={},raw={},issues=[];
  for(const field of DATE_FIELDS){
    const lines=[...front.matchAll(new RegExp('^'+field+':[ \\t]*(.*)$','gm'))].map(m=>m[1]);
    raw[field]=lines;
    if(lines.length>1){issues.push({code:'duplicate',fields:[field]});continue;}
    if(!lines.length)continue;
    const v=lines[0].match(/^(?:"([^"\n]*)"|'([^'\n]*)'|([^#"'\n]*?))[ \t]*(?:#.*)?$/);
    const value=v?(v[1]??v[2]??v[3]).trim():null;
    if(value===''||value==='null'||value==='~')continue;
    if(!validDay(value)){issues.push({code:'invalid_date',fields:[field]});continue;}
    values[field]=value;
  }
  if(values.event_date&&values.event_date>today)issues.push({code:'future_event',fields:['event_date']});
  if(values.valid_from&&values.valid_until&&values.valid_from>values.valid_until)issues.push({code:'reversed_validity',fields:['valid_from','valid_until']});
  return {values,raw,issues};
}
export function validateTemporalChange(before,after,today=shanghaiDay()){
  const old=parseTemporal(before,today),next=parseTemporal(after,today);
  const introduced=next.issues.filter(i=>i.fields.some(f=>JSON.stringify(old.raw[f])!==JSON.stringify(next.raw[f])));
  if(introduced.length)throw Error('TEMPORAL_INVALID: '+JSON.stringify(introduced));
  return next;
}
export function temporalTriggers(text,today=shanghaiDay()){
  const p=parseTemporal(text,today),bad=new Set(p.issues.flatMap(i=>i.fields));
  const triggers=[];
  // Last valid date is inclusive. Review date is due starting that day.
  if(p.values.valid_until&&!bad.has('valid_until')&&p.values.valid_until<today)triggers.push({field:'valid_until',date:p.values.valid_until,reason:'explicit_validity_ended'});
  if(p.values.review_after&&!bad.has('review_after')&&p.values.review_after<=today)triggers.push({field:'review_after',date:p.values.review_after,reason:'review_requested'});
  return {parsed:p,triggers,key:triggers.length?digest(TEMPORAL_VERSION+'\n'+digest(text)+'\n'+JSON.stringify(triggers)):null};
}
export function temporalPage(notes,getContent,args={},index={}){
  const {event_from,event_to,event_date_state,offset=0,limit=100,index_revision}=args;
  for(const d of [event_from,event_to])if(d!==undefined&&!validDay(d))throw Error('Use a real YYYY-MM-DD event date');
  if(event_from&&event_to&&event_from>event_to)throw Error('event_from must not follow event_to');
  if(event_date_state&&!['known','unknown'].includes(event_date_state))throw Error('Invalid event_date_state');
  if(event_date_state==='unknown'&&(event_from||event_to))throw Error('Unknown dates cannot be filtered to an event interval; query unknown separately');
  if(!Number.isInteger(offset)||offset<0||!Number.isInteger(limit)||limit<1||limit>20000)throw Error('Invalid pagination');
  // Snapshot synchronous cached values before any awaits. Missing content stays visible as unknown.
  const rows=notes.map(n=>{const text=getContent(n.path);const p=parseTemporal(text);const event=p.issues.some(i=>i.fields.includes('event_date'))?null:p.values.event_date??null;return {...n,event_date:event,event_date_state:event?'known':'unknown',date_issues:p.issues,content_available:typeof text==='string',content_sha256:typeof text==='string'?digest(text):null};});
  const revision=digest(JSON.stringify(rows.map(n=>[n.path,n.mtime,n.content_sha256]))+JSON.stringify(index));
  if(index_revision&&index_revision!==revision)return {status:'INDEX_CHANGED',index_revision:revision,instruction:'Restart pagination at offset=0 and deduplicate paths. No completeness claim across revisions.'};
  const known=rows.filter(n=>n.event_date),unknown=rows.length-known.length;
  const selected=rows.filter(n=>{
    if(event_date_state==='unknown')return !n.event_date;
    if((event_date_state==='known'||event_from||event_to)&&!n.event_date)return false;
    return (!event_from||n.event_date>=event_from)&&(!event_to||n.event_date<=event_to);
  });
  const page=selected.slice(offset,offset+limit),next=offset+page.length;
  return {status:'OK',scope_total:rows.length,known_event_pages:known.length,unknown_event_pages:unknown,matched_total:selected.length,offset,next_offset:next<selected.length?next:null,index_revision:revision,index,complete:next>=selected.length&&index.state==='ready'&&rows.every(n=>n.content_available),notes:page,
    scope:'Page field candidates only; known means marked, not source-verified. Missing, mixed, partial and invalid dates require body/source reading. Never replace event time with file time. A dated page may contain other events; field filtering is not a complete fact timeline.'};
}
