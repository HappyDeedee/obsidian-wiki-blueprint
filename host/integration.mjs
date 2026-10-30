/** Disposable database only. Real upstream Vault + encrypted CouchDB + HTTP MCP.
 * Cloud transcription is explicitly replayed; no model/cloud request is made.
 */
import assert from 'node:assert/strict';
import {readFile,writeFile,copyFile,mkdir} from 'node:fs/promises';
import {openAsBlob} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {loadConfig} from './config.mjs';
import {openVault,readAttachment} from './adapter.mjs';
import {bootstrap} from './bootstrap.mjs';
import {createRuntime} from './runtime.mjs';
import {meetingImporter} from './meeting.mjs';
import {serve} from './server.mjs';

const config=await loadConfig();
if(config.profile!=='disposable-ci'||!/^wiki_ci_[a-f0-9]+$/.test(config.couchdb.database))throw Error('Disposable CI profile/database required');
const tokens=JSON.parse(await readFile(join(config.paths.state,'ci-tokens.json'),'utf8'));
const watchdog=setTimeout(()=>{process.stderr.write('Disposable integration watchdog expired; no PASS claimed\n');process.exit(1);},180000);
const vault=await openVault(config),checks=[];
const checkpoint=name=>{checks.push(name);process.stdout.write('CI checkpoint '+name+'\n');};
let server;
try{
  await bootstrap(vault);
  assert.equal((await bootstrap(vault)).created,0);checkpoint('starter_idempotence');
  const sourcePath='00-Inbox/随手放/fictional.md';
  const source='---\nkind: inbox\nprocessed: false\n---\n# Fictional Aurora\n\nThe workshop budget is 120 units.\n';
  await vault.writeNote(sourcePath,source);
  const runtime=await createRuntime(config,vault,{importMeeting:meetingImporter(config,vault)});
  server=serve(runtime,config);server.listen(config.http.port,config.http.bind);await once(server,'listening');
  const url=`http://${config.http.bind}:${config.http.port}/mcp`;
  async function call(role,method,params={}){
    const response=await fetch(url,{signal:AbortSignal.timeout(30000),method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tokens[role]},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
    assert.equal(response.status,200);const body=await response.json();assert(!body.error,JSON.stringify(body));return body.result;
  }
  async function tool(role,name,args){
    const result=await call(role,'tools/call',{name,arguments:args});
    if(result.isError)throw Error('Real tool failed: '+name);
    const text=result.content.find(c=>c.type==='text')?.text;
    try{return JSON.parse(text);}catch{return text;}
  }
  assert.equal((await call('reader','initialize',{protocolVersion:'2024-11-05'})).serverInfo.name,'wiki-blueprint-host');
  assert((await call('curator','tools/list')).tools.filter(t=>t.name.startsWith('curation_')).length===12);
  assert((await call('reader','tools/call',{name:'curation_writer_acquire',arguments:{owner:'forbidden'}})).isError);
  checkpoint('real_http_role_boundary');
  const lease=await tool('curator','curation_writer_acquire',{owner:'disposable-integration'});
  assert.equal(lease.status,'ACQUIRED');
  const prep=await tool('curator','curation_prepare',{paths:[sourcePath],writer_token:lease.token});
  const target='20-Wiki/来源摘要/Fictional-Aurora.md';
  const proposal={token:prep.sources[0].token,disposition:'knowledge',reason:'Fictional evidence replay, NOT model extraction.',
    concepts:{action:'none',paths:[],reason:'Single fictional fact'},entities:{action:'none',paths:[],reason:'Synthetic fact only'},
    concept_candidates:[],entity_candidates:[],overview_decision:{action:'unchanged',reason:'No global navigation change'},contradictions:'None in disposable database',
    quotes:[{path:sourcePath,text:'The workshop budget is 120 units.'}],
    updates:[{path:target,mode:'create',content:`---\ntitle: Fictional Aurora\ndomain: 工作\ntype: 来源摘要\nstatus: 稳定\ncreated: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n# Fictional Aurora\n\nThe workshop budget is 120 units.\n\n[[${sourcePath}]]\n`}]};
  const receipt=await tool('curator','curation_commit',proposal);assert.equal(receipt.status,'PASS');
  assert.equal((await tool('curator','curation_commit',proposal)).reused,true);
  assert.equal((await tool('curator','curation_verify',{index:null,source_id:receipt.source_id,operation:'ingest',commit_key:receipt.commit_key,verify_saved:true})).status,'PASS');
  assert.equal(await vault.readNote(sourcePath),source);checkpoint('encrypted_source_commit_verify_duplicate_original_preserved');
  const imageFile=new URL('../benchmark/fixtures/sample.png',import.meta.url),imagePath='00-Inbox/随手放/fictional.png';
  const imageBytes=await readFile(imageFile);
  assert(await vault.manipulator.put(imagePath,await openAsBlob(imageFile),{ctime:Date.now(),mtime:Date.now(),size:imageBytes.length}));
  assert.equal((await readAttachment(vault,imagePath)).sha256,createHash('sha256').update(imageBytes).digest('hex'));
  const image=await call('reader','tools/call',{name:'read_image',arguments:{path:imagePath}});
  assert(!image.isError);assert(image.content.some(c=>c.type==='image'&&c.mimeType==='image/png'));checkpoint('encrypted_binary_image_readback');
  await mkdir(config.paths.meeting_data,{recursive:true});
  const audioFile=new URL('../benchmark/fixtures/synthetic-tone.wav',import.meta.url),bytes=await readFile(audioFile),job=createHash('sha256').update(bytes).digest('hex');
  await copyFile(audioFile,join(config.paths.meeting_data,job+'.wav'));
  const manifest={job_id:job,task_id:'fictional-replay-task',extension:'wav',bytes:bytes.length,created:'2026-01-01T08:00:00Z',is_test:true};
  const result={job_id:job,task_id:manifest.task_id,paragraphs:[{start_ms:0,speaker_id:'1',text:'Fictional replay transcript, not speech recognition.'}],actions:[],summary:'Fictional replay',minutes:{title:'Fictional replay'}};
  await writeFile(join(config.paths.meeting_data,job+'.export.json'),JSON.stringify(manifest));
  await writeFile(join(config.paths.meeting_data,job+'.result.json'),JSON.stringify(result));
  const imported=await tool('meeting','import_meeting',{job_id:job});assert.equal(imported.saved,true);assert.equal(imported.audio_sha256,job);
  assert.equal((await tool('meeting','import_meeting',{job_id:job})).note_path,imported.note_path);
  assert.equal(createHash('sha256').update(await readFile(join(config.paths.meeting_data,job+'.wav'))).digest('hex'),job);
  checkpoint('real_meeting_import_replayed_transcript_source_preserved');
  await tool('curator','curation_writer_release',{token:lease.token});
  await new Promise(resolve=>server.close(resolve));server=null;
  const recreated=await createRuntime(config,vault);
  const who=await recreated.identity('Bearer '+tokens.curator);
  assert.equal((await recreated.call(who,'curation_status',{})).successes,1);
  assert.equal((await recreated.call(who,'curation_verify',{index:null,source_id:receipt.source_id,operation:'ingest',commit_key:receipt.commit_key,verify_saved:true})).status,'PASS');
  checkpoint('runtime_recreation_persistence');
  console.log(JSON.stringify({status:'PASS',scope:'Real CouchDB, upstream Vault and HTTP host; NOT full install or device acceptance',checks,model_calls:0,cloud_transcription:'synthetic replay, not live ASR',production_compared:false}));
}finally{
  if(server)await new Promise(resolve=>server.close(resolve));
  await Promise.race([vault.close(),new Promise(resolve=>setTimeout(resolve,5000))]);
  clearTimeout(watchdog);
}
// Upstream background timers can outlive close; all writes/readbacks above were awaited.
process.exit(0);

