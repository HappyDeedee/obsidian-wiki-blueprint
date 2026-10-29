/** Deterministic metadata only. Does not infer a business date or semantic fact state. */
export function sourceKindFromPaths(paths=[]){
  if(paths.length&&paths.every(p=>p.startsWith('10-Sources/用户自述/')||p.startsWith('00-Inbox/日记/')) )return '用户自述';
  const folders=[['调研','调研'],['需求会','需求会'],['对外口径','对外口径'],['历史二手','历史二手']];
  for(const [folder,value]of folders)if(paths.length&&paths.every(p=>p.split('/').slice(0,-1).includes(folder)))return value;
  return '待定';
}
export function applyV2Metadata(text,{newPage=false,path='',sourcePaths=[]}={}){
  const fm=text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if(!fm)throw Error('V2 metadata requires frontmatter: '+path);
  let head=fm[1];
  if(!/^rule_version:\s*\S/m.test(head))head+='\nrule_version: '+(newPage?'v2':'v2之前');
  if(path.startsWith('20-Wiki/来源摘要/')&&!/^source_kind:\s*\S/m.test(head))head+='\nsource_kind: '+sourceKindFromPaths(sourcePaths);
  return text.replace(fm[0],'---\n'+head+'\n---');
}
