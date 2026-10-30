import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash,randomBytes} from 'node:crypto';
import {once} from 'node:events';
import {createRuntime} from '../runtime.mjs';
import {serve} from '../server.mjs';
import {jsonStore} from '../store.mjs';

const hash=t=>createHash('sha256').update(t).digest('hex');
class MemoryVault {
  notes=new Map([
    ['99-System/知识库规则.md','# Public fictional rules\n'],
    ['20-Wiki/知识索引.md','# 知识索引\n'],['20-Wiki/总览.md','# 总览\n'],
    ['90-Logs/入库台账.md','# 入库台账\n']
  ]);
  async readNote(p){return this.notes.get(p)??null;}
  async writeNote(p,t){this.notes.set(p,t);return true;}
  async getMetadata(p){return this.notes.has(p)?{path:p,mtime:1577836800000}:null;}
  async listNotes(prefix){return [...this.notes.keys()].filter(p=>!prefix||p.startsWith(prefix)).sort();}
}
async function fixture(t){
  const root=await mkdtemp(join(tmpdir(),'wiki-host-'));t.after(()=>rm(root,{recursive:true,force:true}));
  const config={paths:{state:root,registry:join(root,'registry.json')},http:{bind:'127.0.0.1',port:0}};
  const tokens={},rows=[];
  for(const role of ['read-only','proposal','curator','meeting']){
    tokens[role]=randomBytes(32).toString('hex');rows.push({id:role,role,enabled:true,sha256:hash('Bearer '+tokens[role]),tools:role==='meeting'?['import_meeting']:[]});
  }
  await writeFile(config.paths.registry,JSON.stringify(rows));
  const vault=new MemoryVault(),runtime=await createRuntime(config,vault,{importMeeting:async id=>({saved:true,job_id:id,test_double:true})});
  vault.notes.set('20-Wiki/综合/已确认词典.md',await readFile(new URL('../../system/config/confirmed-dictionary.empty.md',import.meta.url),'utf8'));
  const identity=role=>runtime.identity('Bearer '+tokens[role]);
  return {root,config,tokens,rows,vault,runtime,identity};
}
test('all twelve live curation schemas are registered; binary tools are separately identified additions',async t=>{
  const f=await fixture(t),who=await f.identity('curator'),tools=await f.runtime.list(who);
  assert.equal(tools.filter(x=>x.name.startsWith('curation_')).length,12);
  assert(tools.some(x=>x.name==='read_attachment'));assert(tools.some(x=>x.name==='read_image'));
});
test('reader cannot acquire or write; curator cannot bypass pipeline to alter raw originals',async t=>{
  const f=await fixture(t),reader=await f.identity('read-only'),curator=await f.identity('curator');
  await assert.rejects(f.runtime.call(reader,'curation_writer_acquire',{owner:'test'}),/denied/i);
  await assert.rejects(f.runtime.call(curator,'write_note',{path:'00-Inbox/随手放/source.md',content:'overwrite'}),/denied/i);
  await assert.rejects(f.runtime.call(curator,'delete_note',{path:'00-Inbox/随手放/source.md'}),/denied/i);
});
test('proposal is create-only and cannot escape its permitted Inbox roots',async t=>{
  const f=await fixture(t),who=await f.identity('proposal');
  const args={path:'00-Inbox/问答投稿/fictional.md',content:'# Fictional proposal'};
  await f.runtime.call(who,'write_note',args);
  await assert.rejects(f.runtime.call(who,'write_note',{...args,content:'replacement'}),/preserved/i);
  await assert.rejects(f.runtime.call(who,'write_note',{path:'99-System/rules.md',content:'bad'}),/denied/i);
  assert.equal(await f.vault.readNote(args.path),args.content);
});
test('registry revocation applies to an already initialized identity',async t=>{
  const f=await fixture(t),who=await f.identity('read-only');f.rows[0].enabled=false;
  await writeFile(f.config.paths.registry,JSON.stringify(f.rows));
  await assert.rejects(f.runtime.call(who,'list_notes',{}),/revoked/i);
});
test('meeting identity can invoke only the exact job manifest contract',async t=>{
  const f=await fixture(t),who=await f.identity('meeting'),id=hash('fictional audio');
  assert.equal((await f.runtime.call(who,'import_meeting',{job_id:id})).job_id,id);
  await assert.rejects(f.runtime.call(who,'import_meeting',{job_id:'not-a-hash'}),/arguments/i);
  await assert.rejects(f.runtime.call(who,'write_note',{path:'20-Wiki/人物/no.md',content:'bad'}),/denied/i);
});
test('writer lease and journal state survive runtime recreation',async t=>{
  const f=await fixture(t),who=await f.identity('curator');
  const lease=await f.runtime.call(who,'curation_writer_acquire',{owner:'fixture'});
  assert.equal(lease.status,'ACQUIRED');
  const second=await createRuntime(f.config,f.vault);
  assert.equal((await second.call(who,'curation_writer_acquire',{owner:'other'})).status,'BUSY');
  await second.call(who,'curation_writer_release',{token:lease.token});
});
test('real production pipeline commits a fictional source and reuses the identical receipt',async t=>{
  const f=await fixture(t),who=await f.identity('curator');
  const path='00-Inbox/随手放/fictional.md',original='---\nkind: inbox\nprocessed: false\n---\n# Fictional Aurora\n\nThe workshop budget is 120 units.\n';
  await f.vault.writeNote(path,original);
  const lease=await f.runtime.call(who,'curation_writer_acquire',{owner:'fixture'});
  const prepared=await f.runtime.call(who,'curation_prepare',{paths:[path],writer_token:lease.token});
  const source=prepared.sources[0];assert.equal(source.full_coverage,true);
  const target='20-Wiki/来源摘要/Fictional-Aurora.md';
  const args={token:source.token,disposition:'knowledge',reason:'Fictional source-backed benchmark',
    concepts:{action:'none',paths:[],reason:'Single fictional project fact, no reusable concept'},
    entities:{action:'none',paths:[],reason:'Synthetic fixture does not establish a real entity'},concept_candidates:[],entity_candidates:[],
    overview_decision:{action:'unchanged',reason:'Does not change global navigation'},contradictions:'No existing claim in this empty fixture.',
    quotes:[{path,text:'The workshop budget is 120 units.'}],
    updates:[{path:target,mode:'create',content:`---\ntitle: Fictional Aurora\ndomain: 工作\ntype: 来源摘要\nstatus: 稳定\ncreated: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n# Fictional Aurora\n\nThe workshop budget is 120 units.\n\n[[${path}]]\n`}]};
  const first=await f.runtime.call(who,'curation_commit',args);assert.equal(first.status,'PASS');
  const again=await f.runtime.call(who,'curation_commit',args);assert.equal(again.reused,true);
  assert.equal((await f.runtime.call(who,'curation_status',{})).successes,1);
  assert.equal(await f.vault.readNote(path),original);
  const verified=await f.runtime.call(who,'curation_verify',{index:null,source_id:first.source_id,operation:'ingest',commit_key:first.commit_key,verify_saved:true});
  assert.equal(verified.status,'PASS');
});
test('state storage rejects traversal and survives reconstruction',async t=>{
  const f=await fixture(t),store=jsonStore(join(f.root,'separate'));
  await store.set('fixture',{value:120});assert.deepEqual(await jsonStore(join(f.root,'separate')).get('fixture'),{value:120});
  await assert.rejects(store.set('../escape',{}),/Invalid/);
});
test('HTTP MCP actually authenticates, validates schemas, rejects cross-origin and returns tools',async t=>{
  const f=await fixture(t),server=serve(f.runtime,f.config);server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url='http://127.0.0.1:'+server.address().port+'/mcp';
  const post=(body,extra={})=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+f.tokens.curator,...extra},body:JSON.stringify(body)});
  assert.equal((await post({jsonrpc:'2.0',id:1,method:'tools/list'},{Authorization:'Bearer invalid'})).status,401);
  assert.equal((await post({jsonrpc:'2.0',id:1,method:'tools/list'},{Origin:'https://other.example.invalid'})).status,403);
  const result=await(await post({jsonrpc:'2.0',id:2,method:'tools/list'})).json();
  assert(result.result.tools.some(x=>x.name==='curation_stage'));
  const bad=await(await post({jsonrpc:'2.0',id:3,method:'tools/call',params:{name:'curation_writer_acquire',arguments:{}}})).json();
  assert.equal(bad.result.isError,true);
});
