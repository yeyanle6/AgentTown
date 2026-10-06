#!/usr/bin/env python3
"""Read-only verification of local/cloud state; credentials are never printed."""
from pathlib import Path
import json,subprocess,hashlib,sys
root=Path(__file__).resolve().parents[1];config=json.loads((root/'.runtime/cloud.json').read_text())
def cloud_get(path):
 setting='url = '+json.dumps(config['url']+path)+'\nheader = '+json.dumps('OAI-Sites-Authorization: Bearer '+config['token'])+'\n'
 r=subprocess.run(['/usr/bin/curl','--silent','--show-error','--fail','--max-time','20','--config','-'],input=setting,capture_output=True,text=True,timeout=25)
 if r.returncode:raise SystemExit('Cloud read failed: '+str(r.returncode))
 return json.loads(r.stdout)
s=cloud_get('/api/state');a=s['artifacts'][0] if s['artifacts'] else None;verified=None
if a:
 artifact=cloud_get('/api/artifacts/'+a['id']);verified=hashlib.sha256(artifact['content'].encode()).hexdigest()==artifact['sha256']
print(json.dumps({'run_id':s['meta']['run_id'],'state':s['meta']['state'],'heartbeat':s['meta']['heartbeat'],'model_status':s['meta']['model_status'],'tasks':len(s['tasks']),'settled':len([t for t in s['tasks'] if t['status']=='settled']),'cloud_artifact_hash_verified':verified,'audit':s['audit'],'cloud_updated_at':s.get('cloud_updated_at')},ensure_ascii=False,indent=2))
