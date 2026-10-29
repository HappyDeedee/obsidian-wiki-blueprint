// Operator-only delivery of the two navigation guides requested by the owner.
import {readdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
let raw='';for await(const c of process.stdin)raw+=c;const input=JSON.parse(raw);
if(!['00-总入口/使用指南.md','00-总入口/上游能力对照.md'].includes(input.path)||typeof input.content!=='string')throw Error('Guide path not authorized');
const hash=t=>createHash('sha256').update(t).digest('hex');
const name=readdirSync('/app/dist').find(n=>/^vault-[A-Z0-9]+\.js$/.test(n));
const {Vault}=await import('/app/dist/'+name);
console.log=()=>{};console.warn=()=>{};
const v=new Vault({couchdbUrl:process.env.COUCHDB_URL,couchdbUser:process.env.COUCHDB_USER,couchdbPassword:process.env.COUCHDB_PASSWORD,database:process.env.COUCHDB_DATABASE,passphrase:process.env.COUCHDB_PASSPHRASE,obfuscatePaths:process.env.COUCHDB_OBFUSCATE_PROPERTIES==='true',caseSensitive:process.env.COUCHDB_CASE_SENSITIVE==='true'});
await v.init();const old=await v.readNote(input.path);
if((old===null?null:hash(old))!==input.expected_sha256)throw Error('Guide changed concurrently');
if(old!==input.content&&!await v.writeNote(input.path,input.content))throw Error('Guide write failed');
if(await v.readNote(input.path)!==input.content)throw Error('Guide readback mismatch');
process.stdout.write(JSON.stringify({status:'PASS',path:input.path}),()=>process.exit(0));
