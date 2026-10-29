"""Cut ~10s audition clips per voice (private dir). Picks the 2 longest sentences
from the member with most talk time, prefers one from a second meeting."""
import json, sys
from pathlib import Path
import numpy as np, av
D = Path('/srv/wiki/meeting/data'); O = Path('/var/lib/kb-voiceprint'); OUT = O / 'clips'
OUT.mkdir(exist_ok=True)
SR = 16000
C = json.loads((O / 'clusters.json').read_text())
want = sys.argv[1:] or [c['voice'] for c in C[:7]]

def sentences(h, spk):
    tr = json.loads((D / f'{h}.Transcription.json').read_text())['Transcription']; cur = {}
    for p in tr['Paragraphs']:
        if str(p['SpeakerId']) != spk: continue
        for w in p['Words']:
            s = cur.setdefault((p['ParagraphId'], w['SentenceId']), [w['Start'], w['End'], ''])
            s[0] = min(s[0], w['Start']); s[1] = max(s[1], w['End']); s[2] += w['Text']
    return [(a / 1000, b / 1000, t) for a, b, t in cur.values()]

def cut(h, s, e):
    audio = next(D / f'{h}{x}' for x in ('.m4a', '.wav') if (D / f'{h}{x}').exists())
    c = av.open(str(audio)); st = c.streams.audio[0]
    c.seek(int(max(s - 0.5, 0) / st.time_base), stream=st, backward=True)
    rs = av.AudioResampler(format='flt', layout='mono', rate=SR); buf = []; t0 = None
    for fr in c.decode(st):
        t = float(fr.pts * st.time_base)
        if t0 is None: t0 = t
        if t > e: break
        for f in rs.resample(fr): buf.append(f.to_ndarray().reshape(-1))
    c.close(); x = np.concatenate(buf)
    return x[max(int((s - t0) * SR), 0):int((e - t0) * SR)]

for c in C:
    if c['voice'] not in want: continue
    mem = sorted(c['members'], key=lambda m: -m['talk_min'])
    picks = []
    for m in mem[:2]:
        ss = sorted([x for x in sentences(m['meeting'], m['speaker']) if 3 <= x[1] - x[0] <= 7], key=lambda x: -(x[1] - x[0]))
        if ss: picks.append((m, ss[0]))
    if len(picks) == 1:
        m = picks[0][0]
        ss = sorted([x for x in sentences(m['meeting'], m['speaker']) if 3 <= x[1] - x[0] <= 7], key=lambda x: -(x[1] - x[0]))
        if len(ss) > 1: picks.append((m, ss[1]))
    parts = []; texts = []
    for m, (s, e, t) in picks:
        parts.append(cut(m['meeting'], s, min(e, s + 6))); parts.append(np.zeros(int(SR * 0.6), np.float32))
        texts.append(f"{m['title']} 发言人{m['speaker']}：{t}")
    x = np.concatenate(parts); x = x / (np.abs(x).max() + 1e-9) * 0.9
    path = OUT / f"{c['voice']}.m4a"
    oc = av.open(str(path), 'w'); os_ = oc.add_stream('aac', rate=SR); os_.layout = 'mono'
    fr = av.AudioFrame.from_ndarray(x.reshape(1, -1).astype(np.float32), format='flt', layout='mono'); fr.sample_rate = SR
    for p in os_.encode(fr): oc.mux(p)
    for p in os_.encode(None): oc.mux(p)
    oc.close()
    print(json.dumps({'voice': c['voice'], 'sec': round(len(x) / SR, 1), 'texts': texts}, ensure_ascii=False))
