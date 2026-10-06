#!/usr/bin/env python3
import sys,json,os
from pathlib import Path
# Read secrets only from a hidden terminal stdin, never shell arguments.
root=Path(__file__).resolve().parents[1];path=root/'.runtime/cloud.json'
print('Ready for hidden cloud configuration JSON',flush=True)
value=json.loads(sys.stdin.readline());required=('url','token','sync_key')
if not all(isinstance(value.get(k),str) and value[k] for k in required):raise SystemExit('Invalid configuration')
path.parent.mkdir(exist_ok=True);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump(value,f)
print('Cloud configuration saved with restricted permissions',flush=True)
