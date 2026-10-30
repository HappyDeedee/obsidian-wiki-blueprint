import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {readAttachment} from '../adapter.mjs';
test('pinned ESM binary package decodes actual public PNG bytes',async()=>{
  const bytes=await readFile(new URL('../../benchmark/fixtures/sample.png',import.meta.url));
  const row={path:'00-Inbox/fixture.png',size:bytes.length,_rev:'1-fixture',type:'newnote',data:[bytes.toString('base64')]};
  const vault={manipulator:{get:async()=>row}};
  assert.equal((await readAttachment(vault,row.path)).data,bytes.toString('base64'));
});
test('changed revision fails instead of returning a mixed binary version',async()=>{
  let count=0;
  const vault={manipulator:{get:async()=>({size:3,_rev:++count===3?'2-changed':'1-fixture',type:'newnote',data:['YWJj']})}};
  await assert.rejects(readAttachment(vault,'00-Inbox/fixture.png'),/changed/i);
});
