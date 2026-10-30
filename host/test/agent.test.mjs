import {test} from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {fixture} from './fixtures.mjs';
import {jsonStore} from '../store.mjs';
import {runAgent} from '../../scheduler/agent.mjs';
import {modelTransport} from '../../scheduler/model.mjs';

const sourcePath='00-Inbox/随手放/fictional-agent.md';
const statement='The workshop budget is 120 units.';
const source='---\nkind: inbox\nprocessed: false\n---\n# Fictional source\n\n'+statement+'\n';
function config(f){return {...f.config,agent:{enabled:true,max_segments_per_run:100,max_context_chars:100000,read_budget:{context_window:64000,max_output:8000,fixed_tokens:16000}},model:{mode:'injected-mock-only'}};}
async function client(f){
  const who=await f.identity('curator');
  return {tool:(name,args)=>f.runtime.call(who,name,args),call:async method=>{
    assert.equal(method,'tools/list');return {tools:await f.runtime.list(who)};
  }};
}
function proposal(request){
  const s=request.prepared.sources[0],path=s.paths[0].path;
  if(request.phase==='curation_stage'){
    // Explicit deterministic fake model for transport/state-machine tests.
    const text=s.source_content;
    return {summary:'Fictional mock reading summary; source quotes retained. Not a real semantic evaluation.',quotes:[text.includes(statement)?statement:text.trim().slice(0,30)]};
  }
  return {disposition:'knowledge',reason:'Explicit fictional model double, not a live extraction quality claim',
    concepts:{action:'none',paths:[],reason:'One fictional fact'},entities:{action:'none',paths:[],reason:'No real person'},
    concept_candidates:[],entity_candidates:[],overview_decision:{action:'unchanged',reason:'No navigation conclusion changes'},contradictions:'No prior claim in this fixture',
    quotes:[{path,text:statement}],updates:[{path:'20-Wiki/来源摘要/Fictional-Agent.md',mode:'create',content:`---\ntitle: Fictional Agent\ndomain: 工作\ntype: 来源摘要\nstatus: 稳定\ncreated: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n# Fictional Agent\n\n${statement}\n\n[[${path}]]\n`}]};
}
test('generic loop disabled means no tools and no model call',async()=>{
  const result=await runAgent({agent:{enabled:false}},{},null,()=>{throw Error('Must not call');});
  assert.equal(result.status,'NEEDS_USER');assert.equal(result.committed,0);
});
test('generic loop drives real pipeline and verifies receipt; explicit model double is called only once',async t=>{
  const f=await fixture(t),cfg=config(f),store=jsonStore(join(f.root,'agent'));
  await f.vault.writeNote(sourcePath,source);let calls=0;
  const generate=async req=>{calls++;return proposal(req);};
  const c=await client(f),result=await runAgent(cfg,c,store,generate);
  assert.equal(result.status,'PASS');assert.equal(result.committed,1);assert.equal(calls,1);
  assert.equal(await f.vault.readNote(sourcePath),source);
  assert.equal((await runAgent(cfg,c,store,generate)).status,'IDLE');assert.equal(calls,1);
});
test('generic loop actually stages long-source segments, then commits once; stage is not an ingestion',async t=>{
  const f=await fixture(t),cfg=config(f),store=jsonStore(join(f.root,'agent'));
  cfg.agent.read_budget={context_window:16000,max_output:2000,fixed_tokens:4000};
  cfg.agent.max_segments_per_run=2;
  const long=source+('\n'+statement).repeat(1500);await f.vault.writeNote(sourcePath,long);
  const c=await client(f),phases=[];let result;
  for(let run=0;run<50;run++){
    result=await runAgent(cfg,c,store,async req=>{phases.push(req.phase);return proposal(req);});
    if(result.status==='PASS')break;assert.equal(result.status,'CONTINUE');assert.equal(result.committed,0);
  }
  assert.equal(result.status,'PASS');assert(phases.includes('curation_stage'));assert.equal(phases.filter(x=>x==='curation_commit').length,1);
  assert.equal(await f.vault.readNote(sourcePath),long);
});
test('uncertain API request is never automatically billed a second time',async t=>{
  const f=await fixture(t),store=jsonStore(join(f.root,'model'));
  const cfg={model:{mode:'api',allow_model_calls:true,base_url:'https://model.example.invalid/v1',api_key:'fictional-only',name:'fictional-model',max_input_chars:10000,max_output_tokens:100,max_requests_per_run:3,timeout_ms:1000,max_response_bytes:10000}};
  let calls=0;const transport=modelTransport(cfg,store,{fetchImpl:async()=>{calls++;throw Error('Synthetic unknown network outcome');}});
  await assert.rejects(transport({source:'fictional'}),/Synthetic/);
  await assert.rejects(transport({source:'fictional'}),/NEEDS_USER/);assert.equal(calls,1);
});
test('model disabled refuses before invoking a network client',async t=>{
  const f=await fixture(t),store=jsonStore(join(f.root,'model'));
  const cfg={model:{mode:'api',allow_model_calls:false,max_input_chars:1000,max_output_tokens:10,max_requests_per_run:1,timeout_ms:1000,max_response_bytes:1000}};
  let calls=0;const transport=modelTransport(cfg,store,{fetchImpl:async()=>{calls++;throw Error('Must not call');}});
  await assert.rejects(transport({source:'fictional'}),/disabled/);assert.equal(calls,0);
});
