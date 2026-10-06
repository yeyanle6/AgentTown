from __future__ import annotations
import argparse,json,os,signal,threading,time,traceback,fcntl,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse
from .store import Store,now,uid,dump,parse
from .sources import collect,latest,fresh
from .skills import TEMPLATES,specification,market_order,mark_nav,research,develop,verify_artifact,apply_verified_skills,source_audit_report
from .brain import Brain

ROOT=Path(__file__).resolve().parent.parent
class Engine:
    def __init__(self,runtime):
        self.runtime=Path(runtime).resolve();self.runtime.mkdir(parents=True,exist_ok=True)
        self.store=Store(self.runtime/'community.sqlite3');self.brain=Brain(self.store,self.runtime)
        self.stop=threading.Event();self.wake=threading.Event();self.sync_error=None;self.sync_lock=threading.Lock();self.export_lock=threading.Lock()
    def seed(self):
        with self.store.tx():
            if self.store.get('seeded'):return
            for template,budget in [('market_review',120),('market_research',120),('release_research',100),('price_stats',160),('source_validator',140),('portfolio_metrics',160),('ledger_audit',60)]:
                spec=specification(template);self.store.create_task(spec['kind'],spec['title'],spec,budget,dedupe='initial:'+template)
            self.store.set('seeded',True)
    def command(self,command):
        cid=command.get('id') or uid('cmd');kind=command.get('kind');payload=command.get('payload',{})
        with self.store.tx():
            existing=self.store.one('SELECT result FROM commands WHERE id=?',(cid,))
            if existing:return parse(existing['result'])
            if now()>=self.store.get('stop_at') or self.store.get('state')=='completed':raise ValueError('本次自主运行已结束，当前仅支持查看记录')
            result={'id':cid,'status':'applied'}
            if kind=='pause':self.store.set('paused',True);self.store.set('state','paused')
            elif kind=='resume':
                if now()>=self.store.get('stop_at'):raise ValueError('本次运行已到截止点，不能继续接单')
                self.store.set('paused',False);self.store.set('state','running')
            elif kind=='tick':
                if not self.store.allowed():raise ValueError('社区已暂停或截止')
            elif kind=='create_task':
                template=payload.get('template');spec=specification(template) if template in TEMPLATES and template!='source_audit' else None
                if not spec:raise ValueError('请选择当前支持的交付模板')
                if payload.get('requirements'):raise ValueError('当前自动验收只支持模板明确的交付要求；额外要求暂不自动结算')
                result['task_id']=self.store.create_task(spec['kind'],payload.get('title') or spec['title'],spec,int(payload.get('budget',100)),origin='user',deadline=payload.get('deadline') or None,dedupe='user:'+cid)
            elif kind=='cancel_task':self.store.cancel_task(str(payload.get('task_id')),'用户取消任务，剩余预算退回公共资金')
            elif kind=='set_employee':
                employee=self.store.one('SELECT id FROM employees WHERE id=?',(payload.get('employee_id'),))
                if not employee:raise ValueError('员工不存在')
                self.store.set('disabled:'+employee['id'],bool(payload.get('disabled')))
            else:raise ValueError('未知干预操作')
            self.store.db.execute('INSERT INTO commands VALUES(?,?,?,?)',(cid,kind,dump(result),now()))
            self.store.event('user','intervention',{'pause':'用户暂停社区','resume':'用户恢复运行','tick':'用户请求立即调度','create_task':'用户发布工作要求','cancel_task':'用户撤销任务','set_employee':'用户调整员工可用性'}[kind],result.get('task_id'),payload)
        if kind=='pause':self.brain.cancel()
        self.wake.set();return result
    def propose(self,employee,decision):
        template=decision.get('proposal_template');budget=decision.get('proposal_budget',0);pid=uid('proposal');title=decision.get('proposal_title','')[:160];reason='';status='rejected';task_id=None
        spec={'deliverable':decision.get('proposal_deliverable',''),'acceptance':decision.get('proposal_acceptance',''),'deadline':self.store.get('stop_at'),'template':template}
        if template not in TEMPLATES or template=='source_audit' or not all([title,spec['deliverable'],spec['acceptance']]):reason='提案缺少交付/验收，或超出已验证工具范围'
        elif decision.get('proposal_kind')!=TEMPLATES[template]['kind']:reason='提案员工类型与执行能力不一致'
        elif any(spec[k]!=TEMPLATES[template][k] for k in ('deliverable','acceptance')):reason='提案要求超出或不同于当前可执行的验收合同，未冻结预算；请保留模板交付与验收原文，用标题和理由说明本次目标'
        elif not isinstance(budget,int) or isinstance(budget,bool) or not 20<=budget<=200:reason='员工提案预算上限200积分'
        elif self.store.one("SELECT id FROM tasks WHERE json_extract(spec,'$.template')=? AND status IN ('open','claimed','running','submitted')",(template,)):reason='已有同类未完成任务，拒绝重复占用公共资金'
        elif self.store.one("SELECT id FROM proposals WHERE employee_id=? AND status='approved' AND created_at>?",(employee['id'],(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())):reason='同一员工每小时最多批准一个自主提案'
        elif template in ('price_stats','source_validator','portfolio_metrics') and self.store.one('SELECT id FROM skills WHERE id=?',(template,)):reason='同一工具已验收，优先复用，不为重复交付付款'
        else:
            approved=specification(template);approved['employee_goal']=spec;task_id=self.store.create_task(approved['kind'],title,approved,budget,origin=employee['id']);status='approved';reason='范围、交付、验收、预算、截止和去重检查通过'
        self.store.db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?)',(pid,employee['id'],title or '未命名提案',dump(spec),budget if isinstance(budget,int) else 0,status,reason,task_id,now()))
        self.store.event('platform','proposal','提案'+('获准：' if status=='approved' else '被拒绝：')+reason,pid,{'employee':employee['id'],'budget':budget})
    def schedule(self):
        # New work only with newly collected real evidence, not fabricated task completion.
        timestamp=int(time.time())
        with self.store.tx():
            if not self.store.allowed():return
            for template,period,budget,key in [('market_review',1200,70,'coinbase_btc'),('market_research',3600,100,'kraken_btc'),('release_research',7200,90,'pypi_pandas'),('ledger_audit',1800,40,None)]:
                source=latest(self.store,key) if key else None
                if key and not fresh(source,600 if 'btc' in key else 7200):continue
                if self.store.one("SELECT id FROM tasks WHERE json_extract(spec,'$.template')=? AND status IN ('open','claimed','running','submitted')",(template,)):continue
                previous=self.store.one("SELECT created_at FROM tasks WHERE json_extract(spec,'$.template')=? ORDER BY created_at DESC LIMIT 1",(template,))
                if previous and (datetime.now(timezone.utc)-datetime.fromisoformat(previous['created_at'])).total_seconds()<period:continue
                spec=specification(template)
                try:self.store.create_task(spec['kind'],spec['title']+' · '+datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime('%H:%M'),spec,budget,origin='source' if key else 'platform',dedupe=f'{template}:{timestamp//period}')
                except ValueError as exc:self.store.event('platform','budget_limit',str(exc))
    def context(self,employee):
        tasks=self.store.rows("SELECT id,kind,title,spec,budget,status,attempts,note FROM tasks WHERE status='open' AND kind=? ORDER BY created_at LIMIT 12",(employee['role'],))
        for t in tasks:t['spec']=parse(t['spec']);t['note']=t['note'][:1500]
        return {'now':now(),'stop_at':self.store.get('stop_at'),'tasks':tasks,'sources':[{k:v for k,v in (latest(self.store,key) or {}).items() if k not in ('sha256',)} for key in ('coinbase_btc','kraken_btc','pypi_pandas','github_pandas')],'skills':self.store.rows('SELECT id,name,role,uses FROM skills'),'portfolio':self.store.one("SELECT * FROM portfolios WHERE employee_id='aiko'"),'public_budget':self.store.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],'templates':{key:{field:value[field] for field in ('kind','deliverable','acceptance')} for key,value in TEMPLATES.items() if key!='source_audit'},'recent_completed_tasks':self.store.rows("SELECT id,kind,title,status FROM tasks WHERE status='settled' ORDER BY rowid DESC LIMIT 8"),'market_history':[{'source_id':r['id'],'observed_at':r['observed_at'],'price':parse(r['payload'])['price']} for r in self.store.rows("SELECT id,observed_at,payload FROM sources WHERE name='coinbase_btc' AND status='ok' ORDER BY retrieved_at DESC LIMIT 30")[::-1]],'recent_decisions':[parse(r['decision']) for r in self.store.rows('SELECT decision FROM decisions WHERE employee_id=? ORDER BY created_at DESC LIMIT 2',(employee['id'],))]}
    def work(self,task):
        spec=parse(task['spec']);template=spec['template'];children=self.store.rows('SELECT status FROM tasks WHERE parent_id=?',(task['id'],))
        if children and any(c['status'] in ('open','claimed','running','submitted') for c in children):return
        decision_row=self.store.one("SELECT decision FROM decisions WHERE employee_id=? AND json_extract(decision,'$.task_id')=? ORDER BY created_at DESC LIMIT 1",(task['owner'],task['id']))
        decision=parse(decision_row['decision'],{}) if decision_row else {}
        with self.store.tx():
            if not self.store.allowed():return
            self.store.db.execute("UPDATE tasks SET status='running' WHERE id=?",(task['id'],));self.store.event(task['owner'],'executing','开始执行：'+task['title'],task['id'])
        try:
            if task['kind']=='A':content=market_order(self.store,task,decision);kind='investment';sources=content['sources']
            elif task['kind']=='B':content=research(self.store,template);kind='research';sources=[r['id'] for r in content['sources']]
            elif task['kind']=='C':content=develop(self.store,self.brain,task,spec);kind='code';sources=[]
            else:
                kind='audit';sources=[]
                if template=='ledger_audit':content={'type':'ledger_audit','audit':self.store.audit(),'method':'从创世余额逐笔重放，独立检查奖励依据与账户余额'}
                else:
                    content=source_audit_report(self.store,task);sources=[r['id'] for r in content['sources']]
            if task['kind'] in ('A','B','D'):content=apply_verified_skills(self.store,task,decision,content)
            with self.store.tx():self.store.submit(task['id'],dump(content),kind,sources)
        except Exception as exc:
            with self.store.tx():
                current=self.store.one('SELECT status FROM tasks WHERE id=?',(task['id'],))
                if current and current['status'] in ('running','claimed'):
                    if not self.store.allowed() or self.stop.is_set():
                        self.store.db.execute("UPDATE tasks SET status='claimed' WHERE id=?",(task['id'],))
                    elif task['attempts']+1>=task['max_attempts']:self.store.cancel_task(task['id'],'执行失败并达到重试上限：'+str(exc),'failed')
                    else:self.store.db.execute("UPDATE tasks SET status='open',owner=NULL,attempts=attempts+1,note=? WHERE id=?",('执行失败：'+str(exc),task['id']))
                    self.store.db.execute("UPDATE employees SET status='idle' WHERE id=?",(task['owner'],))
                    self.store.artifact(task['id'],'platform','执行失败记录 · '+task['title'],'execution_error',dump({'error':str(exc),'attempt':task['attempts']+1,'at':now(),'reward_paid':False}))
                self.store.event(task['owner'],'execution_failed','执行未完成：'+str(exc),task['id'])
    def review(self):
        for task in self.store.rows("SELECT * FROM tasks WHERE status='submitted' ORDER BY created_at"):
            if not self.store.allowed():return
            reviewer='platform-auditor' if task['owner']=='qa' else 'qa'
            try:
                checks=verify_artifact(self.store,task)
                with self.store.tx():self.store.verify_and_settle(task['id'],reviewer,checks)
            except Exception as exc:
                with self.store.tx():self.store.event(reviewer,'review_error','验收工具异常，未结算：'+str(exc),task['id'])
    def cycle(self):
        with self.store.tx():
            for expired in self.store.rows("SELECT id FROM tasks WHERE deadline<=? AND status NOT IN ('settled','failed','cancelled','expired')",(now(),)):self.store.cancel_task(expired['id'],'任务截止条件已触发','expired')
        self.schedule();self.review()
        for employee in self.store.rows('SELECT * FROM employees ORDER BY role'):
            if not self.store.allowed() or self.stop.is_set():return
            if self.store.get('disabled:'+employee['id'],False):continue
            existing=self.store.one("SELECT * FROM tasks WHERE owner=? AND status IN ('claimed','running')",(employee['id'],))
            if existing:self.work(existing);self.review();continue
            context=self.context(employee)
            # Idle reasoning is sampled at most once per 15m, but available work is considered each cycle.
            last=self.store.get('last_decision:'+employee['id'],0)
            if not context['tasks'] and time.time()-last<900:continue
            if time.time()<self.store.get('model_retry_after',0):continue
            decision=self.brain.decide(employee,context)
            with self.store.tx():self.store.set('last_decision:'+employee['id'],time.time())
            if not self.store.allowed():return
            try:
                with self.store.tx():
                    if decision.get('action')=='claim':
                        self.store.claim(decision['task_id'],employee['id'],decision.get('plan',[]))
                        task=self.store.one('SELECT * FROM tasks WHERE id=?',(decision['task_id'],))
                        if decision.get('delegate') and task['kind'] in ('A','B') and task['budget']>=100:
                            spec=specification('source_audit');self.store.create_task('D','受托来源核查 · '+task['title'],spec,40,origin=employee['id'],parent_id=task['id'],dedupe='delegation:'+task['id'])
                            self.store.event(employee['id'],'delegated','委托 Mia 核查来源，40积分来自原任务预算',task['id'])
                    elif decision.get('action')=='propose':self.propose(employee,decision)
                    elif decision.get('action')=='abandon':self.store.event(employee['id'],'declined','放弃不适合的任务：'+decision.get('reason',''),decision.get('task_id'))
                if decision.get('action')=='claim':self.work(task);self.review()
            except Exception as exc:
                with self.store.tx():self.store.event('platform','decision_rejected','决策未获执行：'+str(exc),decision.get('task_id'))
        with self.store.tx():self.store.set('cycles',self.store.get('cycles',0)+1);self.store.set('last_tick',now())
    def finish(self):
        self.brain.cancel()
        with self.store.tx():
            if self.store.get('state')=='completed':return
            for task in self.store.rows("SELECT id FROM tasks WHERE status NOT IN ('settled','failed','cancelled','expired')"):self.store.cancel_task(task['id'],'达到日本时间 08:00 的运行截止点，未验收预算退回','expired')
            self.store.set('state','completed');self.store.set('paused',True);self.store.set('ended_at',now());self.store.event('platform','completed','本次自主运行结束，账本与成果已保留')
            summary={'run_id':self.store.get('run_id'),'started_at':self.store.get('started_at'),'ended_at':now(),'deadline':self.store.get('stop_at'),'audit':self.store.audit(),'tasks':self.store.rows('SELECT status,COUNT(*) AS count FROM tasks GROUP BY status'),'orders':self.store.one('SELECT COUNT(*) AS count FROM orders'),'skills':self.store.rows('SELECT id,name,uses FROM skills'),'model_status':self.store.get('model_status')}
            self.store.artifact('system','platform','08:00 自主运行结算报告','run_report',dump(summary))
        self.export();self.sync()
    def export(self):
        with self.export_lock:
            snapshot=self.store.snapshot();tmp=self.runtime/'snapshot.tmp';tmp.write_text(dump(snapshot));tmp.replace(self.runtime/'snapshot.json')
    def sync(self):
        if not self.sync_lock.acquire(blocking=False):return
        try:self._sync()
        finally:self.sync_lock.release()
    def _sync(self):
        path=self.runtime/'cloud.json'
        if not path.exists():return
        config=json.loads(path.read_text());snapshot=self.store.snapshot();highwater=self.store.get('cloud_artifact_rowid',0);artifacts=self.store.rows('SELECT rowid AS sync_rowid,* FROM artifacts WHERE rowid>? ORDER BY rowid LIMIT 100',(highwater,))
        sources=self.store.rows('SELECT rowid AS sync_rowid,* FROM sources WHERE rowid>? ORDER BY rowid LIMIT 100',(self.store.get('cloud_source_rowid',0),))
        payload={'snapshot':snapshot,'artifacts':artifacts,'sources':sources,'command_results':self.store.rows('SELECT * FROM commands ORDER BY created_at DESC LIMIT 200')}
        # Credentials travel over stdin, never argv or logs. Endpoint is the registered private Site.
        curl_config='url = '+json.dumps(config['url']+'/api/sync')+'\nheader = '+json.dumps('OAI-Sites-Authorization: Bearer '+config['token'])+'\nheader = '+json.dumps('X-AgentTown-Key: '+config['sync_key'])+'\nheader = "Content-Type: application/json"\nrequest = "POST"\n'
        data_path=self.runtime/'sync-payload.json';data_path.write_text(dump(payload))
        try:
            run=subprocess.run(['/usr/bin/curl','--fail-with-body','--silent','--show-error','--max-time','25','--config','-','--data-binary','@'+str(data_path)],input=curl_config,text=True,capture_output=True,timeout=30)
            if run.returncode:raise ValueError('同步请求失败 '+run.stdout[:100])
            response=json.loads(run.stdout)
            if not response.get('ok'):raise ValueError('同步未被确认')
            if artifacts:
                with self.store.tx():self.store.set('cloud_artifact_rowid',max(a['sync_rowid'] for a in artifacts))
            if sources and {s['id'] for s in sources}.issubset(set(response.get('sources_accepted_ids',[]))):
                with self.store.tx():self.store.set('cloud_source_rowid',max(s['sync_rowid'] for s in sources))
            for cmd in response.get('commands',[]):
                try:self.command({'id':cmd['id'],'kind':cmd['kind'],'payload':parse(cmd['payload'],{})})
                except Exception as exc:
                    with self.store.tx():self.store.db.execute('INSERT OR IGNORE INTO commands VALUES(?,?,?,?)',(cmd['id'],cmd['kind'],dump({'status':'failed','error':str(exc)}),now()))
            self.sync_error=None
            with self.store.tx():self.store.set('cloud_last_sync',now());self.store.set('cloud_error',None)
        except Exception as exc:
            self.sync_error=str(exc)
            with self.store.tx():self.store.set('cloud_error',self.sync_error)
    def deadline_watch(self):
        while not self.stop.wait(.5):
            if now()>=self.store.get('stop_at') and self.store.get('state')!='completed':
                self.finish()
                self.wake.set()
                return
    def maintenance(self):
        last_fetch=0;last_research=0
        while not self.stop.is_set():
            try:
                with self.store.tx():self.store.set('heartbeat',now())
                if now()>=self.store.get('stop_at'):self.finish()
                if self.store.get('state')!='completed' and time.time()-last_fetch>120:
                    collect(self.store,time.time()-last_research>1800)
                    if time.time()-last_research>1800:last_research=time.time()
                    mark_nav(self.store);last_fetch=time.time()
                self.export();self.sync()
            except Exception as exc:
                print(now(),'maintenance',str(exc),flush=True)
            self.stop.wait(10)
    def run(self):
        if now()>=self.store.get('stop_at'):self.finish()
        else:
            self.seed()
            with self.store.tx():
                if not self.store.get('paused'):self.store.set('state','running')
                # Recover interrupted executions without deleting durable artifacts.
                self.store.db.execute("UPDATE tasks SET status='claimed' WHERE status='running'")
        threading.Thread(target=self.deadline_watch,daemon=True).start()
        threading.Thread(target=self.maintenance,daemon=True).start()
        while not self.stop.is_set():
            if self.store.allowed():
                try:self.cycle()
                except Exception:traceback.print_exc()
            self.wake.wait(90);self.wake.clear()
        self.brain.cancel();self.export()

def serve(engine,port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_json(self,value,status=200):
            body=dump(value).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/api/state':return self.send_json(engine.store.snapshot())
            if self.path=='/api/health':return self.send_json({'ok':True,'state':engine.store.get('state'),'heartbeat':engine.store.get('heartbeat')})
            if self.path.startswith('/api/sources/'):
                row=engine.store.one('SELECT * FROM sources WHERE id=?',(self.path.split('/')[-1],));return self.send_json(row or {'error':'来源快照不存在'},200 if row else 404)
            if self.path.startswith('/api/artifacts/'):
                row=engine.store.one('SELECT * FROM artifacts WHERE id=?',(self.path.split('/')[-1],));return self.send_json(row or {'error':'成果不存在'},200 if row else 404)
            return self.send_json({'error':'not found'},404)
        def do_POST(self):
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).hostname not in ('127.0.0.1','localhost'):return self.send_json({'error':'跨站写入被拒绝'},403)
            if self.path!='/api/commands':return self.send_json({'error':'not found'},404)
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0<length<20000:raise ValueError('请求体大小不合法')
                payload=json.loads(self.rfile.read(length));self.send_json(engine.command(payload))
            except Exception as exc:self.send_json({'error':str(exc)},400)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);threading.Thread(target=server.serve_forever,daemon=True).start();return server

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',default=str(ROOT/'.runtime'));parser.add_argument('--port',type=int,default=8766);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    runtime=Path(args.runtime);runtime.mkdir(parents=True,exist_ok=True)
    lock=open(runtime/'engine.lock','w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('AgentTown engine is already running')
    lock.write(str(os.getpid()));lock.flush();engine=Engine(runtime)
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:(engine.stop.set(),engine.wake.set(),engine.brain.cancel()))
    server=serve(engine,args.port);print('AgentTown engine http://127.0.0.1:'+str(args.port),flush=True)
    if args.once:engine.seed();collect(engine.store);engine.cycle();engine.export()
    else:engine.run()
    server.shutdown()
if __name__=='__main__':main()
