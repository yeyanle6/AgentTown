#!/usr/bin/env python3
"""Keep the machine awake only until the authorized simulation deadline."""
import subprocess,sys,os
from pathlib import Path
from datetime import datetime,timezone
root=Path(__file__).resolve().parents[1];os.chdir(root)
stop=datetime.fromisoformat('2026-10-06T23:00:00+00:00')
seconds=max(0,int((stop-datetime.now(timezone.utc)).total_seconds()))
if seconds and Path('/usr/bin/caffeinate').exists():subprocess.Popen(['/usr/bin/caffeinate','-i','-t',str(seconds)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
os.execv(sys.executable,[sys.executable,'-m','engine.main'])
