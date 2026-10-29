"""Group Tingwu speakers across meetings by voiceprint (private report, no vault writes)."""
import json, glob
from pathlib import Path
import numpy as np
D = Path('/srv/wiki/meeting/data'); O = Path('/var/lib/kb-voiceprint')
TH = 0.62
titles = {}
for r in D.glob('*.obsidian.json'):
    try:
        j = json.loads(r.read_text(encoding='utf-8'))
        np_ = j.get('note_path') or ''
        titles[r.name.split('.')[0]] = np_.split('/')[2] if np_.count('/') >= 2 else np_
    except Exception:
        pass
nodes = []
for f in sorted((O / 'meetings').glob('*.json')):
    m = json.loads(f.read_text(encoding='utf-8'))
    for spk, e in m['speakers'].items():
        if 'embedding' in e and e['segments_used'] >= 3 and e['talk_seconds'] >= 20:
            nodes.append({'meeting': m['meeting'], 'title': titles.get(m['meeting'], m['meeting'][:12]), 'speaker': spk,
                          'talk': e['talk_seconds'], 'cons': e['consistency'], 'v': np.array(e['embedding']),
                          'sample': (e.get('samples') or [{}])[0].get('text', '')})
V = np.stack([n['v'] for n in nodes]); S = V @ V.T
# average-linkage agglomerative; never merge two speakers from the same meeting
clusters = [[i] for i in range(len(nodes))]
def link(a, b):
    if {nodes[i]['meeting'] for i in a} & {nodes[j]['meeting'] for j in b}:
        return -1
    return float(np.mean([S[i, j] for i in a for j in b]))
while True:
    best = (-1, None, None)
    for x in range(len(clusters)):
        for y in range(x + 1, len(clusters)):
            s = link(clusters[x], clusters[y])
            if s > best[0]:
                best = (s, x, y)
    if best[0] < TH:
        break
    _, x, y = best
    clusters[x] += clusters[y]; del clusters[y]
clusters.sort(key=lambda c: -sum(nodes[i]['talk'] for i in c))
out = []
for k, c in enumerate(clusters, 1):
    members = [{'meeting': nodes[i]['meeting'], 'title': nodes[i]['title'], 'speaker': nodes[i]['speaker'],
                'talk_min': round(nodes[i]['talk'] / 60, 1), 'sample': nodes[i]['sample']} for i in c]
    inner = [S[i, j] for i in c for j in c if i < j]
    out.append({'voice': f'V{k:02d}', 'meetings': len(c), 'talk_min': round(sum(nodes[i]['talk'] for i in c) / 60, 1),
                'min_sim': round(float(min(inner)), 3) if inner else None, 'members': members})
(O / 'clusters.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print('speakers', len(nodes), 'voices', len(out), 'multi-meeting', sum(1 for c in out if c['meetings'] > 1))
for c in out[:14]:
    print(c['voice'], c['meetings'], c['talk_min'], c['min_sim'], ' | '.join(f"{m['title'][:22]}#{m['speaker']}" for m in c['members']))
# nearest cross-meeting similarities for calibration
pairs = sorted(((S[i, j], i, j) for i in range(len(nodes)) for j in range(i + 1, len(nodes)) if nodes[i]['meeting'] != nodes[j]['meeting']), reverse=True)
print('top cross sims', [round(float(p[0]), 3) for p in pairs[:25]])
same = [S[i, j] for i in range(len(nodes)) for j in range(i + 1, len(nodes)) if nodes[i]['meeting'] == nodes[j]['meeting']]
print('within-meeting (different speaker ids) sims: max', round(float(max(same)), 3), 'p90', round(float(np.percentile(same, 90)), 3))
