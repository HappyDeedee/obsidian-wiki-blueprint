"""Generate views from live page properties; preserve original pages and queue counts."""
import os
import argparse,collections,datetime,hashlib,importlib.util,json,pathlib,re,urllib.parse
import yaml

DOMAINS=('工作','创业','个人')
def metadata(text):
    m=re.match(r'\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---(?:\r?\n|$)',text,re.S)
    data=yaml.safe_load(m[1])if m else {}
    if data is None:return {}
    if not isinstance(data,dict):raise ValueError('frontmatter must be a mapping')
    return data
def cell(s):return str(s).replace('|','&#124;').replace('\n',' ')
def link(p):return '[['+cell(p.removesuffix('.md'))+']]'
def generate(notes,kind):
    errors=[];rows=[]
    if kind=='aliases':
        aliases=collections.defaultdict(set)
        for p,text in notes.items():
            if p=='20-Wiki/别名表.md':continue
            try:
                value=metadata(text).get('aliases')
                if value is None:continue
                if not isinstance(value,list)or any(not isinstance(x,str)for x in value):raise ValueError('aliases must be a string list')
                for x in value:
                    if x.strip():aliases[x.strip()].add(p)
            except Exception as e:errors.append({'path':p,'error':str(e)[:200]})
        rows=['---','kind: guide','---','# 别名表','','由知识页的 aliases 自动生成。请在对应页面维护别名；同名候选不自动合并。','','| 别名 | 对应页面 | 状态 |','| --- | --- | --- |']
        for alias,paths in sorted(aliases.items()):rows.append('| '+cell(alias)+' | '+'、'.join(map(link,sorted(paths)))+' | '+('多页同名，需核对'if len(paths)>1 else'页面已声明')+' |')
        count=len(aliases)
    else:
        groups={d:[]for d in DOMAINS};archived=[];originals=[]
        for p,text in notes.items():
            if not p.startswith('30-Projects/'):continue
            try:
                m=metadata(text)
                if m.get('kind') in ['guide','inbox','source']:
                    if m.get('kind')!='guide':originals.append(p)
                    continue
                if m.get('type')!='项目':raise ValueError('project page has unsupported type; requires review')
                domain=m.get('domain');status=m.get('status')
                if domain not in groups:raise ValueError('unknown domain')
                if status not in ['进行中','稳定','归档']:raise ValueError('unknown project status')
                item=(p,status,domain)
                if status=='归档':archived.append(item)
                else:groups[domain].append(item)
            except Exception as e:errors.append({'path':p,'error':str(e)[:200]})
        rows=['---','kind: guide','---','# 项目入口','','由现有项目页属性生成，不自动新建项目或改变归档状态。']
        for domain,items in groups.items():
            rows+=['','## '+domain,'']
            rows+=['- '+link(p)+'（'+status+'）'for p,status,_ in sorted(items)]or['暂无项目。']
        rows+=['','<details>','<summary>已归档项目</summary>','']
        rows+=['- '+link(p)+'（'+domain+'）'for p,_,domain in sorted(archived)]or['暂无归档项目。']
        rows+=['','</details>']
        if originals:rows+=['','## 相关原始记录','']+['- '+link(p)for p in sorted(originals)]
        count=sum(map(len,groups.values()))+len(archived)
    return {'body':'\n'.join(rows)+'\n','count':count,'errors':errors}
def read(c,path):
    # Server programs consume the documented whole-note response internally.
    # Segmenting a large registry re-read the entire CouchDB note for every
    # 4,000 characters. Do not send these large bodies into an LLM context.
    value=c.tool('read_note',path=path)
    if value.startswith('Note not found:'):return None
    if not value.startswith('[Open in Obsidian](')or '\n\n---\n\n'not in value:raise ValueError('Unexpected whole-note response '+path)
    body=value.split('\n\n---\n\n',1)[1]
    if body.startswith('[Segment offset='):raise ValueError('Unexpected segmented response '+path)
    return body

def note_paths(value):
    return set(urllib.parse.unquote(x).removesuffix('.md')+'.md' for x in re.findall(r'\(obsidian://open\?vault=[^&\n]+&file=([^\n)]+)\)',value))

def changed_alias_sources(c):
    """Read only pages that can contribute aliases."""
    paths=set()
    for folder in ('20-Wiki','30-Projects'):
        result=c.tool('search_notes',terms=['aliases'],folder=folder,limit=20000)
        if 'catching up' in result or 'may be incomplete' in result:raise ValueError('Aliases search index incomplete')
        marker=re.search(r'\[SearchPage (\{.*\})\]',result)
        if not marker or json.loads(marker[1]).get('next_offset') is not None:raise ValueError('Aliases search incomplete')
        paths.update(note_paths(result))
    return sorted(p for p in paths if p.startswith(('20-Wiki/','30-Projects/')) and p!='20-Wiki/别名表.md')

def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['aliases','projects']);p.add_argument('--root',default='/srv/wiki/stack');p.add_argument('--publish',action='store_true');p.add_argument('--output');p.add_argument('--prefix',default='');p.add_argument('--fixture');p.add_argument('--test-held-lease');p.add_argument('--held-lease');p.add_argument('--runtime-dir');args=p.parse_args()
    if args.prefix and not re.fullmatch(r'00-Test/[A-Za-z0-9_/-]+/',args.prefix):raise ValueError('Invalid isolated prefix')
    if args.fixture and not args.prefix:raise ValueError('Fixtures require an isolated prefix')
    if args.test_held_lease and not(args.fixture and args.prefix):raise ValueError('Held test lease only for isolated fixture rehearsal')
    root=pathlib.Path(args.root);s=importlib.util.spec_from_file_location('v',root/'vault-client.py');v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
    creds=json.loads((root/'credentials.json').read_text());c=v.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),creds['mcp_token'])
    if args.fixture:
        texts=json.loads(pathlib.Path(args.fixture).read_text())
    elif args.kind=='aliases':
        paths=changed_alias_sources(c)
        if not paths:raise ValueError('No alias candidates; refusing to publish empty view')
        texts={p:read(c,p)for p in paths}
    else:
        listing=c.tool('list_notes',limit=20000)
        if 'catching up'in listing or'may be incomplete'in listing:raise ValueError('Index not complete')
        paths=sorted(set(urllib.parse.unquote(x).removesuffix('.md')+'.md'for x in re.findall(r'\(obsidian://open\?vault=[^&\n]+&file=([^\n)]+)\)',listing)))
        if not paths:raise ValueError('Empty listing')
        texts={p:read(c,p)for p in paths if p.startswith('30-Projects/')}
    result=generate(texts,args.kind)
    result['source_hashes']={p:hashlib.sha256(t.encode()).hexdigest()for p,t in texts.items()};result['at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    if args.output:pathlib.Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if result['errors']:raise ValueError(json.dumps(result['errors'],ensure_ascii=False))
    if args.publish:
        held=args.held_lease or args.test_held_lease
        lease=json.loads(pathlib.Path(held).read_text())if held else json.loads(c.tool('curation_writer_acquire',owner='派生入口生成：'+args.kind))
        if lease['status']!='ACQUIRED':print(json.dumps({'status':'BUSY','changed':False}));return
        try:
            before=json.loads(c.tool('curation_status'))
            target=args.prefix+('20-Wiki/别名表.md'if args.kind=='aliases'else'00-总入口/项目入口.md');old=read(c,target)
            # Recheck every input used in the derivation under the lease.
            for p,t in texts.items():
                if not args.fixture and read(c,p)!=t:raise ValueError('Concurrent source change '+p)
            backup=(pathlib.Path(args.runtime_dir)if args.runtime_dir else root/'private')/'generated-view-backups';backup.mkdir(mode=0o700,exist_ok=True)
            name=args.kind+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json';(backup/name).write_text(json.dumps({'path':target,'body':old},ensure_ascii=False));(backup/name).chmod(0o600)
            changed=old!=result['body']
            try:
                if changed:
                    answer=c.tool('write_note',path=target,content=result['body'])
                    if 'saved'not in answer.lower():raise ValueError('Write not confirmed')
                if read(c,target)!=result['body']:raise ValueError('Server readback mismatch')
            except Exception:
                # These targets are generated navigation only. Never restore a source page.
                current=read(c,target)
                if old is not None and current==result['body']:
                    c.tool('write_note',path=target,content=old)
                    if read(c,target)!=old:raise ValueError('Generated-view rollback readback failed')
                raise
            after=json.loads(c.tool('curation_status'))
            for k in ['next_index','successes','repairs','read_steps','activity_counter']:
                if before[k]!=after[k]:raise ValueError('Queue changed unexpectedly '+k)
            result.update(status='PASS',changed=changed,target=target,backup=str(backup/name))
        finally:
            if not held:c.tool('curation_writer_release',token=lease['token'])
    print(json.dumps({k:v for k,v in result.items()if k not in ['body','source_hashes']},ensure_ascii=False))
if __name__=='__main__':main()
