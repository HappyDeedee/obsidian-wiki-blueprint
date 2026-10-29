"""从声纹聚类结果生成发言人确认卡片（只新建，不覆盖已有卡片）。"""
import json, shutil, time
from pathlib import Path
from urllib.parse import quote
D = Path('/srv/wiki/meeting/data'); VP = Path('/var/lib/kb-voiceprint'); R = Path('/var/lib/kb-confirm')
C = json.loads((VP / 'clusters.json').read_text())
PRE = {
 'V01': ('李相睿', '中', '10-20 招投标业务流程痛点与优化研讨，同一编号说“我叫李向瑞……他陈书毅”（转写把名字写错了）'),
 'V04': ('主人本人', '中', '10-15 AI标书生成文件格式支持讨论，同一编号录了开场白“这个是AI招标……跟李向锐的讨论方案”'),
 'V02': ('云姐', '低', '10-15 示例项目乙交接那场有人称对方“云姐”，只是推断'),
}
def sentences(h, spk):
    tr = json.loads((D / f'{h}.Transcription.json').read_text())['Transcription']; cur = {}
    for p in tr['Paragraphs']:
        if str(p['SpeakerId']) != spk: continue
        for w in p['Words']: cur.setdefault((p['ParagraphId'], w['SentenceId']), []).append(w['Text'])
    return [''.join(v) for v in cur.values()]
n = 0
for c in C:
    if c['talk_min'] < 5: continue
    iid = f"spk-{c['voice']}"
    if (R / 'items' / f'{iid}.json').exists(): continue
    clip = VP / 'clips' / f"{c['voice']}.m4a"; assets = []
    if clip.exists():
        (R / 'assets' / 'voices').mkdir(parents=True, exist_ok=True)
        shutil.copy2(clip, R / 'assets' / 'voices' / clip.name); assets = [f'voices/{clip.name}']
    ev = []
    for m in sorted(c['members'], key=lambda m: -m['talk_min'])[:4]:
        ss = sorted([s for s in sentences(m['meeting'], m['speaker']) if 12 <= len(s) <= 70], key=len, reverse=True)
        path = f"00-Inbox/会议/{m['title']}/完整转写.md"
        ev.append({'text': '“' + (ss[0] if ss else m['sample']) + '”',
                   'source': f"{m['title']} · 发言人{m['speaker']} · 说了{m['talk_min']}分钟",
                   'link': f"obsidian://open?vault=MyWiki&file={quote(path)}"})
    pre = PRE.get(c['voice'])
    item = {'id': iid, 'type': 'speaker_identity', 'status': 'open', 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
            'question': f"声音 {c['voice']} 是谁？",
            'payload': {'voice': c['voice'], 'members': c['members'],
                        'summary': f"出现在 {c['meetings']} 场会议，共说了约 {c['talk_min']} 分钟；声纹相似度最低 {c['min_sim']}"},
            'evidence': ev, 'assets': assets,
            'prefill': {'value': pre[0], 'confidence': pre[1], 'source': pre[2]} if pre else {},
            'target': '私有声纹库中这个声音的姓名（不写入知识库）；之后的会议纪要用这个名字替换发言人编号。名字没确认前，不建人物页。'}
    (R / 'items' / f'{iid}.json').write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding='utf-8'); n += 1
print('created', n)
