import os
import importlib.util, json, pathlib, sys
root=pathlib.Path("/srv/wiki/stack")
s=importlib.util.spec_from_file_location("ai",root/"aliases-incremental.py"); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
v=importlib.util.spec_from_file_location("vc",root/"vault-client.py"); vc=importlib.util.module_from_spec(v); v.loader.exec_module(vc)
cfg=json.loads((root/"credentials.json").read_text()); c=vc.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),cfg["mcp_token"],timeout=45)
result=m.refresh(c,root,root/"generated-refresh",publish=True,batch_size=40)
print(json.dumps(result,ensure_ascii=False))
if result.get("status") not in {"PASS","READY","CHECKPOINT"}: raise SystemExit(1)
