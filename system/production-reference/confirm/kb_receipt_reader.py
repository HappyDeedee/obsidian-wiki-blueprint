"""Select bounded receipt metadata without materializing large source snapshots."""
import json,pathlib,re,mmap
DEC=json.JSONDecoder()
SPACE=re.compile(rb'\S')
MARK=re.compile(rb'["{}\[\]]')
END=re.compile(rb'[,}\]\s]')
FIELDS=(('status',),('maintenance_finalized',),('prepared','record','paths'),
 ('prepared','confirmation','question_id'),('prepared','confirmation','question_version'),
 ('result','knowledge_paths'),('result','disposition'),('completed_at',),('at',),
 ('plan','recordPath'),('input','reason'))

def selected_receipt(path,fields=FIELDS):
 p=pathlib.Path(path)
 if p.stat().st_size>32*1024**2:raise ValueError('receipt exceeds 32 MiB safety limit')
 # Map the existing file read-only; never expand its full snapshots in memory.
 handle=p.open('rb');text=mmap.mmap(handle.fileno(),0,access=mmap.ACCESS_READ);found={}
 def ws(pos):
  m=SPACE.search(text,pos)
  if m is None:raise ValueError('unexpected end of receipt')
  return m.start()
 def string_end(pos):
  pos+=1
  while True:
   end=text.find(b'"',pos)
   if end<0:raise ValueError('unterminated string')
   back=end-1
   while back>=pos and text[back]==92:back-=1
   if (end-1-back)%2==0:return end+1
   pos=end+1
 def skip(pos):
  pos=ws(pos)
  if text[pos]==34:return string_end(pos)
  if text[pos]not in(123,91):
   end=END.search(text,pos)
   return end.start()if end else len(text)
  stack=[text[pos]];pos+=1
  while stack:
   m=MARK.search(text,pos)
   if m is None:raise ValueError('unclosed container')
   pos=m.start();ch=text[pos]
   if ch==34:pos=string_end(pos);continue
   if ch in(123,91):stack.append(ch)
   elif (stack.pop(),ch)not in ((123,125),(91,93)):raise ValueError('mismatched container')
   pos+=1
  return pos
 def visit(pos,prefix):
  pos=ws(pos)
  if prefix in fields:
   end=skip(pos)
   if end-pos>1024**2:raise ValueError('metadata field exceeds 1 MiB safety limit')
   found[prefix]=json.loads(text[pos:end])
   return end
  if text[pos]!=123:return skip(pos)
  pos=ws(pos+1)
  if text[pos]==125:return pos+1
  while True:
   end=string_end(pos);key=json.loads(text[pos:end]);pos=end
   if not isinstance(key,str):raise ValueError('object key is not a string')
   pos=ws(pos)
   if text[pos]!=58:raise ValueError('missing colon')
   branch=prefix+(key,);pos=ws(pos+1)
   pos=visit(pos,branch)if any(x[:len(branch)]==branch for x in fields)else skip(pos)
   pos=ws(pos)
   if text[pos]==125:return pos+1
   if text[pos]!=44:raise ValueError('missing comma')
   pos=ws(pos+1)
 try:
  end=visit(0,())
  if text[end:].strip():raise ValueError('trailing receipt data')
  return {'.'.join(k):v for k,v in found.items()}
 finally:text.close();handle.close()
