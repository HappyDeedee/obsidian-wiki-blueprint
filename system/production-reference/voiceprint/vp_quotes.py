import json
from pathlib import Path
D=Path('/srv/wiki/meeting/data');C=json.loads(Path('/var/lib/kb-voiceprint/clusters.json').read_text())
def sents(h,spk):
    tr=json.loads((D/f'{h}.Transcription.json').read_text())['Transcription'];out={}
    for p in tr['Paragraphs']:
        if str(p['SpeakerId'])!=spk:continue
        for w in p['Words']:out.setdefault((p['ParagraphId'],w['SentenceId']),[]).append(w['Text'])
    return [''.join(v) for v in out.values()]
for c in C:
    if c['talk_min']<5:continue
    print('\n##',c['voice'],f"{c['meetings']}场 {c['talk_min']}分钟")
    for m in c['members']:
        ss=sorted(sents(m['meeting'],m['speaker']),key=len,reverse=True)
        q=[s for s in ss if 12<=len(s)<=70][:2]
        print(f"- {m['title']} 发言人{m['speaker']}（{m['talk_min']}分）：", ' / '.join(q))
