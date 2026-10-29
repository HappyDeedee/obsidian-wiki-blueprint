import {createDb} from '/app/packages/db/src/client.ts';
import {createSecretStore} from '/app/packages/adapters/src/secret-store-factory.ts';
import {resolveEncryptionKey} from '/app/packages/core/src/secrets-guard.ts';
import {createHash} from 'node:crypto';import{readFileSync}from'node:fs';
const{prisma,pool}=createDb(process.env.DATABASE_URL!);let store:any;
try{
 const input=JSON.parse(readFileSync(0,'utf8'));
 if(input.action!=='describe'&&process.env.WIKI_ENABLE_PAID_PROBE!=='true')throw Error('Own paid probe opt-in required');
 const routine=await prisma.routine.findUniqueOrThrow({where:{id:process.env.WIKI_ROUTINE_ID||'CONFIGURE_YOUR_ROUTINE_ID'}});
 const pref=await prisma.spaceModelPreference.findFirstOrThrow({where:{spaceId:routine.spaceId,userId:routine.userId,isDefault:true},include:{credential:true}});
 if(routine.modelId && routine.modelId!==pref.modelId)throw Error('ROUTINE_AND_DEFAULT_MODEL_DIFFER');
 const row=await prisma.secret.findUniqueOrThrow({where:{id:pref.credential.secretId}});store=createSecretStore(resolveEncryptionKey());const secret=JSON.parse(await store.load(row.ciphertext,row.id));
 if(!secret.baseUrl||!secret.apiKey)throw Error('Unexpected credential shape');
 const fingerprint=createHash('sha256').update(JSON.stringify([pref.modelId,pref.credential.id,secret.baseUrl,secret.apiKey,routine.modelId,routine.modelProvider])).digest('hex');
 if(input.action==='describe'){console.log(JSON.stringify({fingerprint,model:pref.modelId,endpointHost:new URL(secret.baseUrl).hostname}));}
 else{
  if(input.fingerprint!==fingerprint)throw Error('MODEL_OR_RELAY_CONFIGURATION_CHANGED');
  const started=Date.now();let result:any={at:new Date().toISOString(),model:pref.modelId,endpointHost:new URL(secret.baseUrl).hostname,fingerprint};
  try{
   const response=await fetch(secret.baseUrl.replace(/\/$/,'')+'/chat/completions',{method:'POST',headers:{Authorization:'Bearer '+secret.apiKey,'Content-Type':'application/json'},body:JSON.stringify({model:pref.modelId,messages:[{role:'user',content:'Reply exactly OK.'}],max_tokens:32,stream:false}),signal:AbortSignal.timeout(35000)});
   const text=await response.text();let data:any;try{data=JSON.parse(text)}catch{data={};}
   result={...result,status:response.status,available:response.ok&&!!data.choices?.[0]?.message?.content&&!!data.choices?.[0]?.finish_reason,error:String(data.error?.message??data.message??'').slice(0,180),elapsedMs:Date.now()-started};
  }catch{result={...result,available:false,error:'transport_timeout_or_connection_failure',elapsedMs:Date.now()-started};}
  console.log(JSON.stringify(result));
 }
}finally{if(store)await store.close();await prisma.$disconnect();await pool.end();}
