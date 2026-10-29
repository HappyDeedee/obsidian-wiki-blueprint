// Candidate only. No production token defaults; unclassified identities fail closed.
import { createHash, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
export const READ_TOOLS = new Set(['read_note','list_notes','list_folders','list_tags','search_notes','get_note_metadata','curation_status']);
export const CURATOR_TOOLS = new Set([...READ_TOOLS,'curation_queue','curation_verify','write_note','edit_note','delete_note','move_note','curation_writer_acquire','curation_writer_release','curation_prepare','curation_context_read','curation_stage','curation_commit','curation_pending','curation_lint_prepare','curation_lint_save']);
const PROPOSAL_ROOTS = ['00-Inbox/问答投稿/','00-Inbox/待确认答复/'];
export function normalizedPath(path) {
  if(typeof path!=='string'||!path||path!==path.normalize('NFC')||path.startsWith('/')||/[\\%\x00-\x1f:]/u.test(path)||path.split('/').some(x=>!x||x==='.'||x==='..'))throw Error('Invalid vault path');
  return path;
}
function pathsIn(args) {
  const paths=[];
  const visit=(x,key='')=>{if(x===null||x===undefined)return;
    if(['path','from','to'].includes(key)&&typeof x==='string')paths.push(normalizedPath(x));
    else if(['paths','related_paths','prioritize_paths','evidence_paths'].includes(key)&&Array.isArray(x))x.forEach(p=>paths.push(normalizedPath(p)));
    else if(Array.isArray(x))x.forEach(y=>visit(y));
    else if(typeof x==='object')Object.entries(x).forEach(([k,v])=>visit(v,k));};visit(args);return paths;
}
export function makePolicy({registryPath,atomicCreate}) {
  async function identity(header) {
    if(typeof header!=='string'||!/^Bearer \S+$/.test(header))throw Error('Unauthorized');
    const digest=createHash('sha256').update(header).digest();
    const records=JSON.parse(await readFile(registryPath,'utf8'));
    if(!Array.isArray(records))throw Error('Invalid identity registry');
    const matches=records.filter(r=>r.enabled===true&&/^[a-f0-9]{64}$/.test(r.sha256||'')&&timingSafeEqual(digest,Buffer.from(r.sha256,'hex')));
    if(matches.length!==1)throw Error('Unauthorized');
    return {...matches[0],authenticated:true};
  }
  async function current(session) {
    // Re-read every call. A downgrade/revocation invalidates even cached session context.
    const rows=JSON.parse(await readFile(registryPath,'utf8'));
    const hits=rows.filter(r=>r.enabled===true&&r.id===session?.id&&r.sha256===session?.sha256);
    if(hits.length!==1)throw Error('Identity revoked');return hits[0];
  }
  function allowed(who,tool,args) {
    if(!['read-only','proposal','curator','meeting'].includes(who.role))throw Error('Unclassified identity');
    const paths=pathsIn(args);
    if(who.role==='curator'&&who.legacyMeetingPermit===true&&tool==='import_meeting') {
      if(!/^[a-f0-9]{64}$/.test(args.job_id||'')||Object.keys(args).some(k=>k!=='job_id'))throw Error('Invalid meeting job');
      return 'meeting-transition';
    }
    if(who.role==='meeting') {
      if(!(who.tools||[]).includes(tool))throw Error('Meeting tool denied');
      if(tool==='import_meeting') {
        // The fixed job manifest and the existing importer validate all destinations.
        if(!/^[a-f0-9]{64}$/.test(args.job_id||'')||Object.keys(args).some(k=>k!=='job_id'))throw Error('Invalid meeting job');
      } else if(!paths.length||paths.some(p=>!(who.pathPrefixes||[]).some(root=>p.startsWith(root))))throw Error('Meeting path denied');
      return 'meeting';
    }
    if(READ_TOOLS.has(tool))return 'read';
    if(who.role==='proposal'&&tool==='write_note') {
      const p=normalizedPath(args.path);
      if(!PROPOSAL_ROOTS.some(root=>p.startsWith(root))||!p.endsWith('.md'))throw Error('Proposal path denied');
      if(typeof args.content!=='string'||Buffer.byteLength(args.content)>262144)throw Error('Invalid proposal content');
      return 'create';
    }
    if(who.role==='curator'&&CURATOR_TOOLS.has(tool)) {
      // Existing pipeline lease, version and raw-file protections remain authoritative.
      if(!READ_TOOLS.has(tool))for(const p of paths) {
        if(p.startsWith('99-System/')||p.startsWith('40-输出/'))throw Error('Protected path');
      }
      return 'curate';
    }
    throw Error('Tool denied: '+tool);
  }
  async function execute(tool,args,ctx,original) {
    const who=await current(ctx.session);const mode=allowed(who,tool,args);
    if(mode==='create'){await atomicCreate(args.path,args.content);return 'Proposal created: '+args.path;}
    return original(args,ctx);
  }
  return {identity,current,allowed,execute};
}
