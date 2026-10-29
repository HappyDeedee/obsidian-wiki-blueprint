"""纯程序的人名预测：用已确认的人的声纹，去比对待确认卡片里的声音。结果写入 predict.json，不改任何卡片和知识库。"""
import json, os, numpy as np
from pathlib import Path
VP = Path('/var/lib/kb-voiceprint'); R = Path('/var/lib/kb-confirm')
SUGGEST, GAP = 0.60, 0.05   # 最高分≥0.60 且领先第二名≥0.05 才给出“建议”
emb_cache = {}
def emb(meeting, spk):
    if meeting not in emb_cache:
        f = VP / 'meetings' / f'{meeting}.json'
        emb_cache[meeting] = json.loads(f.read_text())['speakers'] if f.exists() else {}
    e = emb_cache[meeting].get(str(spk), {}).get('embedding')
    if e is None: return None
    v = np.asarray(e, dtype=np.float32); return v / (np.linalg.norm(v) + 1e-9)

voices = json.loads((VP / 'voices.json').read_text()) if (VP / 'voices.json').exists() else {}
people = {}   # 显示名 -> {role, embs}
for key, v in voices.items():
    if v.get('status') == 'name' and v.get('name'): label = v['name']
    elif v.get('status') == 'self': label = '本人'
    else: continue
    p = people.setdefault(label, {'role': v.get('role'), 'embs': []})
    p['role'] = p['role'] or v.get('role')
    for m in v.get('members', []):
        x = emb(m['meeting'], m['speaker'])
        if x is not None: p['embs'].append(x)

out = {'people': [{'name': k, 'role': v['role']} for k, v in people.items() if k != '本人'], 'items': {}}
for f in (R / 'items').glob('*.json'):
    it = json.loads(f.read_text())
    if it.get('status') != 'open' or it.get('type') != 'speaker_identity': continue
    xs = [x for x in (emb(m['meeting'], m['speaker']) for m in it['payload']['members']) if x is not None]
    if not xs: continue
    scores = []
    for name, p in people.items():
        if not p['embs']: continue
        P = np.stack(p['embs'])
        scores.append((float(np.mean([np.max(P @ x) for x in xs])), name))
    scores.sort(reverse=True)
    top = [{'name': n, 'sim': round(s, 3)} for s, n in scores[:3]]
    sure = bool(top) and top[0]['sim'] >= SUGGEST and (len(top) < 2 or top[0]['sim'] - top[1]['sim'] >= GAP)
    out['items'][it['id']] = {'top': top, 'suggest': top[0]['name'] if sure else None}
tmp = R / 'predict.tmp'; tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1)); os.replace(tmp, R / 'predict.json')
print(json.dumps(out['items'], ensure_ascii=False))
