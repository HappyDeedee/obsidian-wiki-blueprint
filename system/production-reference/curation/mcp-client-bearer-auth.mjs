// Optional owner-managed API keys, stored as SHA256 hashes. Removing a record
// revokes that key on the next request; existing master/OAuth auth is unchanged.
import {readFile} from 'node:fs/promises';
import {createHash, timingSafeEqual} from 'node:crypto';

export async function validateClientBearer(header, filePath) {
  if (typeof header !== 'string' || !header.startsWith('Bearer ') || header.length <= 7) return false;
  try {
    const records = JSON.parse(await readFile(filePath, 'utf8'));
    if (!Array.isArray(records)) return false;
    const actual = createHash('sha256').update(header).digest();
    return records.some(record => record?.enabled === true &&
      typeof record.sha256 === 'string' && /^[a-f0-9]{64}$/.test(record.sha256) &&
      timingSafeEqual(actual, Buffer.from(record.sha256, 'hex')));
  } catch {
    // Missing, unreadable, or invalid credential files must never grant access.
    return false;
  }
}
