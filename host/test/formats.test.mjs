// Binary inventories are validated by Python tests; never treat text extraction
// as semantic acceptance or a synthetic tone as an ASR quality benchmark.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
test('public binary fixtures match their explicitly fictional inventory',async()=>{
  const inventory=JSON.parse(await readFile(new URL('../../benchmark/fixtures.json',import.meta.url),'utf8'));
  assert.equal(inventory.fictional,true);assert.equal(inventory.speech,false);
  assert.equal(Object.keys(inventory.files).length,6);
  for(const [name,expected]of Object.entries(inventory.files)){
    assert(!name.includes('/'));
    assert.equal(createHash('sha256').update(await readFile(new URL('../../benchmark/fixtures/'+name,import.meta.url))).digest('hex'),expected);
  }
});
