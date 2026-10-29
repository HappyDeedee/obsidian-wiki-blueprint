import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createHash,randomBytes} from 'node:crypto';
import {makePolicy} from '../system/production-reference/curation/mcp-role-policy.mjs';
import {createProposal} from '../system/production-reference/curation/mcp-atomic-proposal.mjs';

test('live role registry: reader denied write, proposal bounded, revocation immediate',async()=>{
 const root=await mkdtemp(join(tmpdir(),'wiki-roles-'));
 try{
  const registryPath=join(root,'roles.json'),header='Bearer '+randomBytes(32).toString('hex');
  let row={id:'test-client',role:'read-only',enabled:true,sha256:createHash('sha256').update(header).digest('hex')};
  await writeFile(registryPath,JSON.stringify([row]));
  const policy=makePolicy({registryPath,atomicCreate:async()=>{}}),session=await policy.identity(header);
  assert.equal(policy.allowed(session,'read_note',{path:'20-Wiki/知识.md'}),'read');
  assert.throws(()=>policy.allowed(session,'write_note',{path:'20-Wiki/知识.md',content:'x'}));
  row={...row,role:'proposal'};await writeFile(registryPath,JSON.stringify([row]));
  const who=await policy.current(session);
  assert.equal(policy.allowed(who,'write_note',{path:'00-Inbox/问答投稿/test.md',content:'x'}),'create');
  for(const path of ['20-Wiki/知识.md','00-Inbox/问答投稿/../other.md','00-Inbox/问答投稿/%2e%2e.md']){
   assert.throws(()=>policy.allowed(who,'write_note',{path,content:'x'}));
  }
  await writeFile(registryPath,JSON.stringify([{...row,enabled:false}]));
  await assert.rejects(policy.current(session),/revoked/);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('proposal create preserves an existing file',async()=>{
 const root=await mkdtemp(join(tmpdir(),'wiki-create-'));
 try{
  const vault={safePath:async(path)=>{const p=resolve(root,path);if(!p.startsWith(root+'/')&&!p.startsWith(root+'\\'))throw Error('Path denied');return p;}};
  const path='00-Inbox/问答投稿/a.md';
  await createProposal(vault,path,'first');
  await assert.rejects(createProposal(vault,path,'second'));
  assert.equal(await readFile(join(root,path),'utf8'),'first');
 }finally{await rm(root,{recursive:true,force:true});}
});

test('meeting role has only fixed job import scope',()=>{
 const policy=makePolicy({registryPath:'unused',atomicCreate:async()=>{}});
 const role={role:'meeting',tools:['import_meeting'],pathPrefixes:['00-Inbox/会议/']};
 assert.equal(policy.allowed(role,'import_meeting',{job_id:'a'.repeat(64)}),'meeting');
 assert.throws(()=>policy.allowed(role,'import_meeting',{job_id:'a'.repeat(64),path:'20-Wiki/x.md'}));
 assert.throws(()=>policy.allowed(role,'write_note',{path:'20-Wiki/x.md',content:'x'}));
});
