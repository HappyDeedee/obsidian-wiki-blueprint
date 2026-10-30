/** Stateless MCP HTTP endpoint. Bearer identities are re-read for every request and tool call. */
import {createServer} from 'node:http';
import {pathToFileURL} from 'node:url';
import {loadConfig} from './config.mjs';
import {openVault} from './adapter.mjs';
import {createRuntime} from './runtime.mjs';
import {meetingImporter} from './meeting.mjs';

export function serve(runtime,config) {
  return createServer(async(req,res)=>{
    const send=(status,body)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(body));};
    if(req.url==='/health'&&req.method==='GET')return send(200,{status:'PASS',service:'wiki-host',scope:'process only'});
    if(req.url!=='/mcp')return send(404,{error:'not_found'});
    let who;try{who=await runtime.identity(req.headers.authorization);}catch{return send(401,{error:'unauthorized'});}
    if(req.method!=='POST')return send(405,{error:'method_not_allowed'});
    if(!/^application\/json(?:;|$)/i.test(req.headers['content-type']??''))return send(415,{error:'json_required'});
    if(req.headers.origin&&req.headers.origin!==config.http.public_origin)return send(403,{error:'origin_denied'});
    let body;try{
      const chunks=[];let n=0;for await(const chunk of req){n+=chunk.length;if(n>1024*1024)throw Error('body_limit');chunks.push(chunk);}
      body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if(Array.isArray(body)||body.jsonrpc!=='2.0'||typeof body.method!=='string')throw Error('invalid_jsonrpc');
    }catch{return send(400,{error:'invalid_request'});}
    if(body.id===undefined){res.writeHead(202);return res.end();}
    const result=value=>send(200,{jsonrpc:'2.0',id:body.id,result:value});
    try {
      if(body.method==='initialize')return result({protocolVersion:'2024-11-05',capabilities:{tools:{}},serverInfo:{name:'wiki-blueprint-host',version:'0.3.0-dev'}});
      if(body.method==='ping')return result({});
      if(body.method==='tools/list')return result({tools:await runtime.list(who)});
      if(body.method==='tools/call') {
        const p=body.params;if(!p||typeof p.name!=='string')throw Error('Invalid tool call');
        try{
          const value=await runtime.call(who,p.name,p.arguments??{});
          return result(value?.content?value:{content:[{type:'text',text:typeof value==='string'?value:JSON.stringify(value)}]});
        }catch{return result({isError:true,content:[{type:'text',text:'Tool rejected or failed. Inspect private operator diagnostics; no success receipt was issued.'}]});}
      }
      return send(200,{jsonrpc:'2.0',id:body.id,error:{code:-32601,message:'Method not found'}});
    }catch{return send(200,{jsonrpc:'2.0',id:body.id,error:{code:-32603,message:'Internal failure'}});}
  });
}
export async function main(){
  const config=await loadConfig(),vault=await openVault(config),runtime=await createRuntime(config,vault,{importMeeting:meetingImporter(config,vault)});
  const server=serve(runtime,config);server.requestTimeout=30000;server.headersTimeout=15000;
  server.listen(config.http.port,config.http.bind,()=>process.stdout.write('wiki host listening on configured loopback port\n'));
  const stop=()=>server.close(()=>process.exit(0));process.on('SIGTERM',stop);process.on('SIGINT',stop);
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)main().catch(()=>{process.stderr.write('Host startup failed; check private configuration and dependencies\n');process.exitCode=1;});
