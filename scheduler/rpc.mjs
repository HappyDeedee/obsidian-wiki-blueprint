/** Local authenticated MCP client. Never falls back to a production endpoint. */
export function mcpClient(config){
  const url=new URL(config.agent.mcp_url);
  if(!['127.0.0.1','[::1]','localhost'].includes(url.hostname)||!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw Error('Generic worker connects only to its explicitly configured local host');
  const token=process.env.WIKI_AGENT_TOKEN??config.agent.mcp_token;
  if(typeof token!=='string'||token.length<32)throw Error('NEEDS_USER: own curator identity required');
  return {
    async call(method,params={}){
      const response=await fetch(url,{method:'POST',redirect:'error',signal:AbortSignal.timeout(config.agent.tool_timeout_ms),headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
      if(!response.ok)throw Error('Local MCP request failed, status '+response.status);
      const body=await response.json();if(body.error||!body.result)throw Error('Local MCP protocol failure');return body.result;
    },
    async tool(name,args={}){
      const result=await this.call('tools/call',{name,arguments:args});
      if(result.isError)throw Error('Local MCP tool rejected: '+name);
      const text=result.content?.find(c=>c.type==='text')?.text;
      if(typeof text!=='string')throw Error('Expected structured tool result');
      try{return JSON.parse(text);}catch{throw Error('Expected JSON tool result: '+name);}
    }
  };
}
