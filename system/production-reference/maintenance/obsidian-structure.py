"""Deterministic structure checks through the existing authenticated MCP, no LLM calls."""
import os
import pathlib,json,re,sys,datetime,collections,importlib.util,hashlib,urllib.parse,subprocess,time
root=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else '/srv/wiki/stack')
spec=importlib.util.spec_from_file_location('vault_client',root/'vault-client.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
creds=json.loads((root/'credentials.json').read_text(encoding='utf-8-sig'))
client=module.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),creds['mcp_token'],timeout=180)
listing=client.tool('list_notes',limit=20000)
if 'Showing ' in listing.splitlines()[0] or 'may be incomplete' in listing or 'catching up' in listing:
    raise RuntimeError('Index not complete; structure inspection not declared complete')
paths=[urllib.parse.unquote(p).removesuffix('.md')+'.md' for p in re.findall(r'\(obsidian://open\?vault=[^&\n]+&file=([^\n]+)\)\s*$',listing,re.M)]
if not paths:raise RuntimeError('No complete note listing')
catalog=set(paths);short=collections.defaultdict(list)
for p in paths:short[pathlib.PurePosixPath(p).stem].append(p)
def body(path):
    offset=0;parts=[]
    while True:
        for attempt in range(2):
            try:
                result=client.tool('read_note',path=path,offset=offset,limit=4000)
                break
            except (TimeoutError,ConnectionError,OSError):
                if attempt:raise
                time.sleep(1)
        # Listing URLs may contain literal parentheses. Never truncate a path at the first one.
        match=re.search(r'\[Segment offset=(\d+) next_offset=(END|\d+) total_chars=(\d+)\]\n',result)
        if not match or int(match[1])!=offset:raise RuntimeError('Cannot read complete segment '+path)
        parts.append(result[match.end():])
        if match[2]=='END':
            value=''.join(parts)
            if len(value)!=int(match[3]):raise RuntimeError('Incomplete body '+path)
            return value
        offset=int(match[2])
skip_prefix=('00-Test/','00-Inbox/','10-Sources/Codex/','10-Sources/Codex增量/','90-Logs/','50-写作范本/')
scope=[p for p in paths if not p.startswith(skip_prefix)]
texts={p:body(p) for p in scope}
curated=[p for p in scope if p.startswith(('20-Wiki/','30-Projects/')) and p not in {'20-Wiki/知识索引.md','20-Wiki/总览.md'} and not re.search(r'^kind:\s*(?:inbox|guide|source)\s*$',texts[p],re.M)]
empty=[];broken=[];ambiguous=[];inbound=collections.Counter();edges=[]
for p,text in texts.items():
    clean=re.sub(r'\A---\s*\n[\s\S]*?\n---\s*\n','',text).strip()
    if not clean or re.fullmatch(r'#+\s*[^\n]+',clean):empty.append(p)
    # Code snippets are examples, not actual graph links.
    clean=re.sub(r'```[\s\S]*?```','',clean)
    for match in re.finditer(r'!?\[\[([^\]]+)\]\]',clean):
        target=match[1].replace('\\|','|').split('|',1)[0].split('#',1)[0].strip()
        if not target:continue
        normalized=target if target.endswith('.md') else target+'.md'
        local=(pathlib.PurePosixPath(p).parent/normalized).as_posix()
        found=[normalized] if normalized in catalog else [local] if local in catalog else [x for x in catalog if x.endswith('/'+normalized)]
        if not found:found=short.get(pathlib.PurePosixPath(target).stem,[])
        if len(found)==1:inbound[found[0]]+=1;edges.append([p,found[0]])
        elif len(found)>1:ambiguous.append({'page':p,'target':target,'candidates':found})
        else:
            suffix=pathlib.PurePosixPath(target).suffix
            if suffix and suffix!='.md':continue  # File attachments require the binary manifest.
            broken.append({'page':p,'target':target})
index=texts.get('20-Wiki/知识索引.md','')
unindexed=[p for p in curated if p[:-3] not in index]
orphan_candidates=[p for p in curated if not inbound[p]]
source_less=[p for p in curated if not re.search(r'(?:sources:|来源|source_file:)',texts[p])]
temporal=json.loads(subprocess.check_output(['sudo','docker','exec','-i','obsidian-mcp','node','/app/curation/temporal-inspect.mjs'],input=json.dumps({'texts':{p:texts[p] for p in curated}},ensure_ascii=False),text=True,encoding='utf-8',timeout=45))
report={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'method':'2026-10-15.9 complete paginated reads and escaped table aliases; zero LLM calls','catalog_notes':len(paths),'bodies_read':len(texts),'source_only_or_log_notes':len(paths)-len(texts),'curated_notes':len(curated),'empty_or_heading_only':empty,'curated_missing_from_index':unindexed,'curated_missing_source_markers':source_less,'broken_link_candidates':broken,'ambiguous_links':ambiguous,'curated_orphan_candidates':orphan_candidates,'graph_edges':len(edges),'semantic_lint':'NOT RUN','source_history_semantics':'NOT RUN','alias_and_binary_attachment_resolution':'NOT COMPLETE','scope':'All note paths enumerated; original Codex transcripts, inbox, logs and test bodies excluded from structural content checks','covered_hashes':{p:hashlib.sha256(text.encode()).hexdigest() for p,text in texts.items()}}
report['temporal']=temporal
(root/'structure-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
note='# 结构巡检报告\n\n检查时间：'+report['checked_at']+'\n\n确定性结构检查，0次LLM调用。全目录 '+str(len(paths))+' 篇，读取正文 '+str(len(texts))+' 篇；Codex历史原文、收件箱、日志及测试内容未做语义检查。\n\n'
note+='| 项目 | 数量 |\n|---|---:|\n| 空白或仅标题候选 | '+str(len(empty))+' |\n| 结论页未进知识索引 | '+str(len(unindexed))+' |\n| 结论页缺来源标记 | '+str(len(source_less))+' |\n| 断链候选 | '+str(len(broken))+' |\n| 歧义链接 | '+str(len(ambiguous))+' |\n'
note+='\n## 需处理的候选\n\n'
note+='时间字段检查 '+str(temporal['checked'])+' 页，日期异常 '+str(len(temporal['issues']))+' 页，明确到期/到复核日候选 '+str(len(temporal['due']))+' 页。仅扫描平面日期，不将缺日期判为过期；正文混合有效期需内容体检核源。\n\n'
for item in temporal['issues'][:40]:note+='- 日期异常：`'+item['path']+'` '+json.dumps(item['issues'],ensure_ascii=False)+'\n'
for item in temporal['due'][:40]:note+='- 到期复核：`'+item['path']+'` '+json.dumps(item['triggers'],ensure_ascii=False)+'\n'
for p in empty[:30]:note+='- 空白候选：`'+p+'`\n'
for p in unindexed:note+='- 未进索引：`'+p+'`\n'
for row in broken[:80]:note+='- 链接待核：`'+row['page']+'` → `'+row['target']+'`\n'
note+='\n别名、附件和历史引用未完全解析，链接问题为候选，未自动修复。未执行矛盾、过时、语义重复或完整内容lint；本报告不能称全库通过。详细证据保存在小服务器structure-evidence.json。\n'
saved=client.tool('write_note',path='90-Logs/结构巡检报告.md',content=note)
assert 'saved' in saved.lower()
print(json.dumps({k:v for k,v in report.items() if k not in {'covered_hashes','broken_link_candidates','ambiguous_links','empty_or_heading_only'}},ensure_ascii=False))
