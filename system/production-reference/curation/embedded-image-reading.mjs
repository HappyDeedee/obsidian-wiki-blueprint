import {createHash} from 'node:crypto';
const sha256 = value => createHash('sha256').update(value).digest('hex');

/** Only known exported attachment references with a complete, verifiable image
 * payload are omitted from the text view. The stored original and its offsets
 * remain authoritative. Unknown data and ordinary examples stay visible. */
export function embeddedImageRanges(content, kind) {
  if (kind !== 'Codex对话') return [];
  const pattern = /\[附件引用：(data:image\/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\r\n]{4096,}))(?=\])/g;
  const ranges = [];
  let previousUnit = 0, previousPoint = 0;
  for (const match of content.matchAll(pattern)) {
    const encoded = match[3].replace(/[\r\n]/g, '');
    if (encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) continue;
    const bytes = Buffer.from(encoded, 'base64');
    if (bytes.toString('base64') !== encoded) continue;
    const mime = match[2].toLowerCase();
    const hex = bytes.subarray(0, 12).toString('hex');
    const valid = mime === 'png' ? hex.startsWith('89504e470d0a1a0a') && bytes.subarray(-12).toString('hex') === '0000000049454e44ae426082'
      : /jpe?g/.test(mime) ? hex.startsWith('ffd8ff') && bytes.subarray(-2).toString('hex') === 'ffd9'
      : mime === 'gif' ? /^GIF8[79]a/.test(bytes.subarray(0, 6).toString()) && bytes.at(-1) === 0x3b
      : bytes.subarray(0,4).toString() === 'RIFF' && bytes.subarray(8,12).toString() === 'WEBP' && bytes.readUInt32LE(4) + 8 === bytes.length;
    if (!valid) continue;
    const unit = match.index + '[附件引用：'.length;
    const start = previousPoint + Array.from(content.slice(previousUnit, unit)).length;
    const end = start + match[1].length; // the data URI itself is ASCII
    ranges.push({start,end,mime:'image/'+mime,sha256:sha256(bytes),encoded_sha256:sha256(match[1]),status:'image_not_read'});
    previousUnit = unit + match[1].length; previousPoint = end;
  }
  return ranges;
}

/** Return at most budget text characters, jumping only over verified image
 * encoding. read_range remains in ORIGINAL code points, even when resuming
 * halfway through an image stored by an older version. */
export function textReadingSegment(content, offset, budget, ranges) {
  const chars = Array.from(content), total = chars.length;
  let end = offset, visible = 0;
  const parts = [], excluded = [];
  for (const range of ranges) {
    if (range.end <= end) continue;
    if (visible >= budget) break;
    const textEnd = Math.min(range.start, end + budget - visible, total);
    if (textEnd > end) { parts.push(chars.slice(end,textEnd).join(''));visible += textEnd-end;end=textEnd; }
    if (end < range.start || visible >= budget) break;
    const from = end;
    end = range.end;
    excluded.push({...range,omitted_range:[from,end]});
    parts.push(`\n[图片编码已略过；图片内容未读取；sha256=${range.sha256}]\n`);
  }
  if (end < total && visible < budget) {
    const next = Math.min(total,end + budget-visible);
    parts.push(chars.slice(end,next).join(''));visible += next-end;end=next;
  }
  return {end,segment:parts.join(''),visible_chars:visible,excluded};
}
