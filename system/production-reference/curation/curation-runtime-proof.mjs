/** A model assertion is insufficient: accept only a Worker-signed terminal failure. */
import {createHmac,timingSafeEqual}from 'node:crypto';
import fs from 'node:fs';
export function verifyRuntimeFailureProof(input,key){
 if(!input||typeof input.token!=='string'||typeof input.kind!=='string'||typeof input.run_id!=='string'||!/^[a-f0-9]{64}$/.test(input.proof??''))return false;
 try{
  key??=fs.readFileSync('/app/curation-results/runtime-diagnostic.key');
  const expected=createHmac('sha256',key).update(JSON.stringify([input.token,input.kind,input.run_id])).digest();
  return timingSafeEqual(expected,Buffer.from(input.proof,'hex'));
 }catch{return false;}
}
