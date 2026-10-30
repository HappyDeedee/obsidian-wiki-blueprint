import {parseTemporal,temporalTriggers,shanghaiDay,TEMPORAL_VERSION} from './knowledge-temporal.mjs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const {texts,as_of=shanghaiDay()}=JSON.parse(input),issues=[],due=[];
for(const [path,text] of Object.entries(texts)){
  const p=parseTemporal(text,as_of);if(p.issues.length)issues.push({path,issues:p.issues});
  const t=temporalTriggers(text,as_of);if(t.triggers.length)due.push({path,triggers:t.triggers});
}
console.log(JSON.stringify({version:TEMPORAL_VERSION,as_of,checked:Object.keys(texts).length,issues,due,scope:'Explicit flat dates only; body claim validity and absent dates require semantic source review.'}));
