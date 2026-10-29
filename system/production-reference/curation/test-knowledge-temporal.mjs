import assert from 'node:assert/strict';
import {parseTemporal,validDay,validateTemporalChange,temporalTriggers,temporalPage} from './knowledge-temporal.mjs';
import {createPipeline,hash} from './curation-pipeline.mjs';
let checks=0;
const check=(f)=>{f();checks++;};
const front=s=>'---\n'+s+'\n---\n事实。';
check(()=>assert(validDay('2024-02-29')));
for(const d of ['2026-02-29','2026-02-30','2026-13-01','2026-10','10-16','0000-01-01','2026-10-17T00:00:00Z'])check(()=>assert(!validDay(d)));
check(()=>assert.equal(parseTemporal('\uFEFF---\r\nevent_date: "2026-10-15" # 日期\r\n---\r\n').values.event_date,'2026-10-15'));
check(()=>assert.equal(parseTemporal(front("event_date: '2026-10-15' # a")).values.event_date,'2026-10-15'));
check(()=>assert.equal(parseTemporal(front('event_date: 2026-10-15\nevent_date: 2026-10-16')).issues[0].code,'duplicate'));
check(()=>assert.deepEqual(parseTemporal(front('event_date: null\nsource_date: # 未知')).issues,[]));
check(()=>assert.equal(parseTemporal(front('event_date: 2026-10-18'),'2026-10-17').issues[0].code,'future_event'));
check(()=>assert.deepEqual(parseTemporal(front('valid_from: 2026-10-18'),'2026-10-17').issues,[]));
check(()=>assert.throws(()=>validateTemporalChange('',front('valid_from: 2026-10-20\nvalid_until: 2026-10-19'),'2026-10-17'),/reversed_validity/));
const invalid=front('event_date: 2026-02-30');
check(()=>assert.doesNotThrow(()=>validateTemporalChange(invalid,invalid+'正文修改','2026-10-17')));
check(()=>assert.throws(()=>validateTemporalChange(invalid,front('event_date: 2026-02-31'),'2026-10-17'),/TEMPORAL_INVALID/));
check(()=>assert.doesNotThrow(()=>validateTemporalChange(invalid,front('event_date: 2026-02-28'),'2026-10-17')));
check(()=>assert.equal(temporalTriggers(front('valid_until: 2026-10-17'),'2026-10-17').triggers.length,0));
check(()=>assert.equal(temporalTriggers(front('valid_until: 2026-10-17'),'2026-10-18').triggers.length,1));
check(()=>assert.equal(temporalTriggers(front('review_after: 2026-10-17'),'2026-10-17').triggers.length,1));
check(()=>assert.equal(temporalTriggers(front('valid_from: 2026-11-01\nvalid_until: 2026-10-01'),'2026-10-17').triggers.length,0));
const bodies={a:front('created: 2026-10-17\nevent_date: 2026-09-01'),b:front('created: 2026-10-17\nsource_date: 2026-10-17'),c:front('event_date: 2026-02-30'),d:'# 混合页\n2026年9月，旧事件。2026年10月16日，新事件。'};
const rows=Object.keys(bodies).map(path=>({path,mtime:123})),get=p=>bodies[p];
check(()=>assert.equal(temporalPage(rows,get,{event_from:'2026-10-17',event_to:'2026-10-17'},{state:'ready'}).matched_total,0));
const pg=temporalPage(rows,get,{event_date_state:'unknown',limit:2,offset:0},{state:'ready'});
check(()=>assert.equal(pg.unknown_event_pages,3));
check(()=>assert.equal(pg.next_offset,2));
check(()=>assert.deepEqual(temporalPage(rows,get,{event_date_state:'unknown',limit:2,offset:2,index_revision:pg.index_revision},{state:'ready'}).notes.map(n=>n.path),['d']));
check(()=>assert.equal(temporalPage(rows,get,{offset:0},{state:'building'}).complete,false));
check(()=>assert.equal(temporalPage(rows,p=>p==='b'?null:get(p),{offset:0},{state:'ready'}).complete,false));
check(()=>assert.throws(()=>temporalPage(rows,get,{event_date_state:'unknown',event_from:'2026-01-01'}),/Unknown/));
bodies.a+='变更';check(()=>assert.equal(temporalPage(rows,get,{index_revision:pg.index_revision},{state:'ready'}).status,'INDEX_CHANGED'));

const raw='00-Inbox/原文.md',target='20-Wiki/概念/示例.md';
function fixture(content){
 const data=new Map(Object.entries({[raw]:'政策最后适用日为2026年10月16日。',[target]:content,'20-Wiki/总览.md':'# 总览','20-Wiki/知识索引.md':'# 索引','20-Wiki/综合/已确认词典.md':'# 已确认词典\n\n此测试没有人物或词条。','99-System/知识库规则.md':'规则','90-Logs/首次全量整理/当前批次.md':'q\nnext_index: 0\n'}));
 let today='2026-10-16T02:00:00Z',stamp=1,fail=null;
 const state=new Map([['maintenance',{successes:42,repairs:3,review_method:8,reviewed:{[target]:hash(content)},review_ranges:{[target]:{sha256:hash(content),end:content.length}},last_completed_lint:'old',reviewed_successes:42}]]);
 const dictionary='20-Wiki/综合/已确认词典.md';
 state.get('maintenance').reviewed[dictionary]=hash(data.get(dictionary));
 state.get('maintenance').review_ranges[dictionary]={sha256:hash(data.get(dictionary)),end:data.get(dictionary).length};
 const vault={readNote:async p=>data.get(p)??null,getMetadata:async p=>data.has(p)?{mtime:1}:null,writeNote:async(p,t)=>{if(fail===p){fail=null;throw Error('injected');}data.set(p,t);stamp++;return true;}};
 const pipe=createPipeline({vault,queue:{records:[],catalog_sha256:'q'},store:{get:async k=>structuredClone(state.get(k)),set:async(k,v)=>state.set(k,structuredClone(v)),keys:async()=>[...state.keys()]},verify:async()=>({status:'PASS'}),listPaths:async()=>[...data.keys()],now:()=>today});
 return {pipe,data,state,clock:s=>today=s,fail:p=>fail=p};
}
const declaration=n=>({path:n.path,sha256:n.sha256,read_range:n.read_range});
const f=fixture(front('valid_until: 2026-10-16')+'长'.repeat(5300));
assert.equal((await f.pipe.lintPrepare({mode:'temporal'})).notes.length,0);checks++;
const ordinary=structuredClone(f.state.get('maintenance'));
f.clock('2026-10-17T00:00:00Z');
let rounds=0;
while(true){const p=await f.pipe.lintPrepare({mode:'temporal'});if(!p.notes.length)break;
 assert.equal(p.notes[0].read_range[0],rounds*2500);
 await f.pipe.lintSave({token:p.token,findings:'到期，原文未证明续期。',evidence_paths:[raw],reviewed_pages:p.notes.map(declaration)});rounds++;assert(rounds<5);}
assert.equal(rounds,3);checks++;
const after=structuredClone(f.state.get('maintenance'));delete after.temporal_reviews;assert.deepEqual(after,ordinary);checks++;
assert.equal((await f.pipe.lintPrepare()).notes.length,0);checks++;
f.data.set(target,f.data.get(target)+'\n新依据待核');assert.equal((await f.pipe.lintPrepare({mode:'temporal'})).notes.length,1);checks++;
{
 const f=fixture(front('review_after: 2026-10-16')),p=await f.pipe.lintPrepare({mode:'temporal'});
 const empty=await f.pipe.lintSave({token:p.token,findings:'未读',evidence_paths:[],reviewed_pages:[]});
 assert.equal(empty.reviewed_ranges,0);assert.equal((await f.pipe.lintPrepare({mode:'temporal'})).notes.length,1);checks++;
 f.data.set(target,'changed');await assert.rejects(f.pipe.lintSave({token:p.token,findings:'旧版本',evidence_paths:[],reviewed_pages:p.notes.map(declaration)}),/changed/);checks++;
}
{
 const f=fixture(front('review_after: 2026-10-16')),p=await f.pipe.prepare({paths:[raw],purpose:'topic_repair',related_paths:[target]});
 const input={token:p.sources[0].token,reason:'核时间',overview_decision:{action:'unchanged',reason:'仅本主题'},concepts:{action:'update',paths:[target],reason:'核验'},entities:{action:'none',paths:[],reason:'无'},concept_candidates:[{name:'示例',action:'update',path:target,reason:'核验',evidence:{path:raw,text:'政策最后适用日为2026年10月16日。'}}],entity_candidates:[],quotes:[{path:raw,text:'政策最后适用日为2026年10月16日。'}],updates:[{path:target,mode:'replace',find:'review_after: 2026-10-16',content:'event_date: 2026-10-20'}]};
 input.contradictions='只核日期，未发现新旧事实矛盾。';
 await assert.rejects(f.pipe.commit(input),/future_event/);assert.equal(f.state.get('maintenance').repairs,3);checks++;
 input.updates[0].content='valid_until: 2026-10-16';f.fail(target);await assert.rejects(f.pipe.commit(input),/injected/);
 assert.equal((await f.pipe.commit(input)).status,'PASS');assert.equal((await f.pipe.commit(input)).reused,true);
 assert.equal(f.state.get('maintenance').successes,42);assert.equal(f.state.get('maintenance').repairs,4);checks++;
 assert([...f.data.keys()].some(p=>p.startsWith('90-Logs/知识修订/')));checks++;
}
console.log(JSON.stringify({status:'PASS',checks,scope:'Calendar, unknown/pagination, unchanged expiry, isolated lint coverage, transaction validation/retry/history.'}));
