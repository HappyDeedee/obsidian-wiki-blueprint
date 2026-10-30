import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import Ajv from 'ajv';
import {createPipeline} from '../system/production-reference/curation/curation-pipeline.mjs';
import {createCurationVerifier} from '../system/production-reference/curation/curation-verifier.mjs';
import {makePolicy,READ_TOOLS,CURATOR_TOOLS} from '../system/production-reference/curation/mcp-role-policy.mjs';
import {jsonStore} from './store.mjs';
import {checkedPath,readAttachment,listAttachments} from './adapter.mjs';

const digest=text=>createHash('sha256').update(text).digest('hex');
const CP='90-Logs/首次全量整理/当前批次.md';
const additions=[
  {name:'read_attachment',description:'Public adapter extension: bounded, version-checked binary read; never executes attachments.',inputSchema:{type:'object',properties:{path:{type:'string'}},required:['path'],additionalProperties:false}},
  {name:'read_image',description:'Public adapter extension: bounded image bytes; content is not automatically understood.',inputSchema:{type:'object',properties:{path:{type:'string'}},required:['path'],additionalProperties:false}}
];
export async function createRuntime(config,vault,{importMeeting}={}) {
  const production=JSON.parse(await readFile(new URL('./tools.production.json',import.meta.url),'utf8'));
  const tools=[...production.tools,...additions],store=jsonStore(join(config.paths.state,'pipeline'));
  const receipts=jsonStore(join(config.paths.state,'receipts'));
  const queue={schema:1,records:[],catalog_sha256:digest('[]')};
  // An empty historical queue is intentional. Incoming sources are discovered by curation_pending.
  if(await vault.readNote(CP)===null) {
    const value=`---\nqueue_version: ${queue.catalog_sha256}\nnext_index: 0\n---\n# 当前批次\n\n历史队列为空；新资料使用每日增量入口。\n`;
    if(!await vault.writeNote(CP,value)||await vault.readNote(CP)!==value)throw Error('Checkpoint initialization failed');
  }
  const verify=createCurationVerifier({vault,queue,readOperation:k=>store.get('commit-'+k),readReceipt:k=>receipts.get(String(k)),writeReceipt:(k,v)=>receipts.set(String(k),v)});
  const pipeline=createPipeline({vault,queue,store,verify,listPaths:()=>vault.listNotes(),
    isIndexReady:()=>true,requireWriter:true,
    canWrite:p=>!p.startsWith('99-System/')&&!p.startsWith('40-输出/')});
  let proposalTail=Promise.resolve();
  const atomicCreate=async(path,content)=>{
    const next=proposalTail.then(async()=>{
      if(await vault.readNote(path)!==null)throw Error('Proposal exists; preserved');
      if(!await vault.writeNote(path,content)||await vault.readNote(path)!==content)throw Error('Proposal readback failed');
    });proposalTail=next.catch(()=>{});return next;
  };
  const policy=makePolicy({registryPath:config.paths.registry,atomicCreate});
  const validators=new Map(),ajv=new Ajv({strict:false,allErrors:false});
  for(const t of tools)validators.set(t.name,ajv.compile(t.inputSchema));
  const methods={curation_prepare:'prepare',curation_context_read:'contextRead',curation_stage:'stage',curation_commit:'commit',
    curation_writer_acquire:'writerAcquire',curation_writer_release:'writerRelease',curation_pending:'pending',curation_status:'status',curation_lint_prepare:'lintPrepare',curation_lint_save:'lintSave'};
  const attachmentTools=new Set(additions.map(x=>x.name));
  async function execute(name,args){
    if(methods[name])return pipeline[methods[name]](args);
    if(name==='curation_verify')return verify(args);
    if(name==='curation_queue')return {...queue,instruction:'Empty historical queue; use curation_pending for received sources.'};
    if(name==='import_meeting') {
      if(typeof importMeeting!=='function')throw Error('Meeting importer is not configured; not a successful import');
      return importMeeting(args.job_id);
    }
    if(name==='read_attachment'||name==='read_image') {
      const data=await readAttachment(vault,args.path,name==='read_image'?10*1024*1024:25*1024*1024);
      if(name==='read_attachment')return data;
      const bytes=Buffer.from(data.data,'base64');
      const mime=bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))?'image/png':
        bytes[0]===255&&bytes[1]===216&&bytes[2]===255?'image/jpeg':null;
      if(!mime)throw Error('Image must be a verified PNG or JPEG');
      return {content:[{type:'image',data:data.data,mimeType:mime},{type:'text',text:JSON.stringify({path:data.path,sha256:data.sha256,size:data.size})}]};
    }
    if(name==='read_note') {
      checkedPath(args.path);const text=await vault.readNote(args.path);if(text===null)throw Error('Note not found');
      const chars=Array.from(text),offset=args.offset??0,end=Math.min(chars.length,offset+(args.limit??4000));
      if(offset>chars.length)throw Error('Offset exceeds note length');
      return `[Segment offset=${offset} next_offset=${end===chars.length?'END':end} total_chars=${chars.length}]\n`+chars.slice(offset,end).join('');
    }
    if(name==='list_notes')return {paths:await vault.listNotes(args.folder)};
    if(name==='list_folders')return [...new Set((await vault.listNotes()).flatMap(p=>p.split('/').slice(0,-1).map((_,i)=>p.split('/').slice(0,i+1).join('/'))))];
    if(name==='get_note_metadata'){checkedPath(args.path);return vault.getMetadata(args.path);}
    if(name==='search_notes'){
      const hits=[];for(const p of await vault.listNotes()){
        const text=await vault.readNote(p);const terms=args.terms??[];
        if(terms.length&&terms.every(t=>(p+'\n'+text).toLocaleLowerCase().includes(t.toLocaleLowerCase())))hits.push({path:p,sha256:digest(text),snippet:text.slice(0,600)});
        if(hits.length>=50)break;
      }return {hits,scope:'bounded literal search; original sources remain authoritative'};
    }
    if(name==='list_tags'){
      const tags=new Set();for(const p of await vault.listNotes())for(const m of (await vault.readNote(p)).matchAll(/(?:^|\s)#([\p{L}\p{N}_/-]+)/gu))tags.add(m[1]);return [...tags];
    }
    // Public remote clients cannot directly mutate originals or bypass the commit journal.
    if(['write_note','edit_note','delete_note','move_note'].includes(name))throw Error('Direct mutation denied; use a proposal or journaled curation');
    throw Error('Unknown tool');
  }
  const visible=who=>tools.filter(t=>attachmentTools.has(t.name)?['read-only','curator'].includes(who.role):
    who.role==='curator'?CURATOR_TOOLS.has(t.name):who.role==='read-only'?READ_TOOLS.has(t.name):
    who.role==='proposal'?READ_TOOLS.has(t.name)||t.name==='write_note':who.role==='meeting'?(who.tools??[]).includes(t.name):false);
  let callTail=Promise.resolve();
  async function call(session,name,args){
      const who=await policy.current(session);
      if(!visible(who).some(t=>t.name===name))throw Error('Tool denied');
      if(!validators.get(name)?.(args))throw Error('Invalid tool arguments');
      if(attachmentTools.has(name))return execute(name,args);
      return policy.execute(name,args,{session:who},()=>execute(name,args));
  }
  return {
    async identity(header){return policy.identity(header);},
    async list(session){return visible(await policy.current(session));},
    async call(session,name,args){
      // Serialize registry decisions and journal mutations within the single host.
      const next=callTail.then(()=>call(session,name,args));callTail=next.catch(()=>{});return next;
    },pipeline,verify,store
  };
}
