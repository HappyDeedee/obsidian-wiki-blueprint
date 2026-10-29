"""Voiceprint extraction for one meeting (private; never writes to the vault).
usage: vp_extract.py <data_dir> <meeting_hash> <out_dir>
Reads <hash>.Transcription.json + audio, writes <out_dir>/<hash>.json with one
CAM++ embedding per Tingwu speaker id (mean of up to 20 clean segments).
"""
import json, sys, time
from pathlib import Path
import numpy as np, av, sherpa_onnx

MODEL = '/opt/kb-voiceprint/models/campplus_zh_16k.onnx'
SR = 16000
MIN_SEG, MAX_SEG = 2.0, 8.0
PER_SPEAKER = 20

data_dir, h, out_dir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
t0 = time.time()
tr = json.loads((data_dir / f'{h}.Transcription.json').read_text(encoding='utf-8'))['Transcription']
audio = next((data_dir / f'{h}{e}' for e in ('.m4a', '.wav', '.mp3') if (data_dir / f'{h}{e}').exists()), None)
if audio is None:
    raise SystemExit('no_audio')

# sentences per speaker: (start, end, text)
sent = {}
for p in tr.get('Paragraphs', []):
    spk = str(p.get('SpeakerId'))
    cur = {}
    for w in p.get('Words', []):
        s = cur.setdefault(w['SentenceId'], [w['Start'], w['End'], ''])
        s[0] = min(s[0], w['Start']); s[1] = max(s[1], w['End']); s[2] += w['Text']
    for s in cur.values():
        sent.setdefault(spk, []).append((s[0] / 1000, s[1] / 1000, s[2]))

plan = {}
for spk, ss in sent.items():
    good = [s for s in ss if s[1] - s[0] >= MIN_SEG]
    good.sort(key=lambda s: -(min(s[1] - s[0], MAX_SEG)))
    plan[spk] = sorted(good[:PER_SPEAKER])

def read_segment(container, stream, start, end):
    container.seek(int(max(start - 0.5, 0) / stream.time_base), stream=stream, any_frame=False, backward=True)
    rs = av.AudioResampler(format='flt', layout='mono', rate=SR)
    buf = []
    for frame in container.decode(stream):
        t = float(frame.pts * stream.time_base) if frame.pts is not None else 0
        if t > end:
            break
        for f in rs.resample(frame):
            arr = f.to_ndarray().reshape(-1)
            buf.append((t, arr))
    if not buf:
        return None
    t_first = buf[0][0]
    x = np.concatenate([a for _, a in buf])
    a = int((start - t_first) * SR); b = int((end - t_first) * SR)
    a = max(a, 0)
    seg = x[a:b]
    return seg if len(seg) > SR * 1.5 else None

cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=MODEL, num_threads=1)
ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
container = av.open(str(audio))
stream = container.streams.audio[0]
result = {'meeting': h, 'model': 'campplus_zh_16k', 'speakers': {}}
for spk, segs in plan.items():
    embs, used = [], []
    for (s, e, text) in segs:
        e2 = min(e, s + MAX_SEG)
        x = read_segment(container, stream, s, e2)
        if x is None:
            continue
        st = ext.create_stream(); st.accept_waveform(SR, x.astype(np.float32)); st.input_finished()
        v = np.array(ext.compute(st), dtype=np.float32); v /= np.linalg.norm(v) + 1e-9
        embs.append(v); used.append({'start': round(s, 2), 'end': round(e2, 2), 'text': text[:60]})
    total_s = sum(e - s for s, e, _ in sent.get(spk, []))
    entry = {'segments_used': len(embs), 'talk_seconds': round(total_s, 1), 'sentences': len(sent.get(spk, []))}
    if embs:
        m = np.mean(embs, axis=0); m /= np.linalg.norm(m) + 1e-9
        cons = float(np.mean([float(v @ m) for v in embs]))
        entry.update(embedding=[round(float(v), 5) for v in m], consistency=round(cons, 3), samples=used[:5])
    result['speakers'][spk] = entry
container.close()
result['seconds'] = round(time.time() - t0, 1)
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / f'{h}.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'meeting': h[:12], 'speakers': {k: (v['segments_used'], v.get('consistency'), v['talk_seconds']) for k, v in result['speakers'].items()}, 'seconds': result['seconds']}, ensure_ascii=False))
