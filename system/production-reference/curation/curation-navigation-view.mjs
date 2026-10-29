/** Read-only related navigation excerpts; original documents and hashes are separate. */
export function relatedNavigation(text,{path,relatedPaths=[],sourceText='',limit=5000}={}){
 const lower=sourceText.toLocaleLowerCase(),related=new Set(relatedPaths.map(p=>p.replace(/\.md$/,'')));
 const score=s=>[...s.matchAll(/\[\[([^\]|#]+)(?:[^\]]*)\]\]/g)].reduce((n,m)=>{
  const p=m[1].replace(/\.md$/,''),title=p.split('/').at(-1);
  return n+(related.has(p)?100: title.length>2&&lower.includes(title.toLocaleLowerCase())?10:0);
 },0);
 const blocks=path.endsWith('/知识索引.md')?text.split('\n'):text.split(/\n\s*\n/);
 const candidates=blocks.map((body,i)=>({body,i,score:score(body)})).filter(x=>x.score>0).sort((a,b)=>b.score-a.score||a.i-b.i);
 const selected=[];let chars=0;
 for(const b of candidates){if(chars+b.body.length+2>limit)continue;selected.push(b);chars+=b.body.length+2;}
 selected.sort((a,b)=>a.i-b.i);
 return {content:`# ${path}（相关导航摘录）\n\n仅列与当前资料或已召回页面相关的原文条目；不是全文。需要全景或其他范围时用 read_note 读取原页。common_versions 保留原页完整版本哈希。\n\n`+(selected.map(x=>x.body).join('\n\n')||'本段未匹配到相关导航条目；不表示知识库没有相关内容。'),total_chars:text.length,selected_blocks:selected.length,candidate_blocks:candidates.length,full_coverage:false};
}
