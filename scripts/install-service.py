#!/usr/bin/env python3
"""Install/update the user-owned service outside macOS protected Documents."""
import os,sys,shutil,plistlib,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=Path.home()/'Library/Application Support/AgentTown'
app.mkdir(parents=True,exist_ok=True)
for name in ['engine','scripts']:
 shutil.copytree(root/name,app/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
source=root/'.runtime';target=app/'.runtime'
if source.exists() and not source.is_symlink():
 if target.exists():raise SystemExit('Refusing to overwrite existing runtime; reconcile databases first')
 shutil.move(str(source),str(target));source.symlink_to(target,target_is_directory=True)
elif not source.exists():
 target.mkdir(exist_ok=True);source.symlink_to(target,target_is_directory=True)
plist={'Label':'com.agenttown.community','ProgramArguments':[sys.executable,str(app/'scripts/run-community.py')],'WorkingDirectory':str(app),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'StandardOutPath':str(target/'engine.log'),'StandardErrorPath':str(target/'engine-error.log'),'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONUNBUFFERED':'1'}}
path=Path.home()/'Library/LaunchAgents/com.agenttown.community.plist';path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(plistlib.dumps(plist))
for attempt in range(12):
 result=subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],capture_output=True,text=True)
 if result.returncode==0:break
 if attempt==11:raise SystemExit(result.stderr)
 time.sleep(1)
print('Installed AgentTown service:',app)
