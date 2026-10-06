from __future__ import annotations
import sqlite3, json, uuid, hashlib, threading
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

STOP_AT = '2026-10-06T23:00:00+00:00'
ROLES = [('aiko','A','葵 Aiko','谨慎的模拟投资员','先核对价格与风险，再决定观察、买入、卖出或持有。'),('lin','B','林 Lin','来源优先的研究员','交叉核对事实，明确时间和未知信息。'),('dev','C','诺亚 Noah','工具开发者','选择可运行、可验证的小工具，复用已验收技能。'),('qa','D','米娅 Mia','独立测试员','复现问题，拒绝没有证据的通过。')]
def now(): return datetime.now(timezone.utc).isoformat()
def uid(prefix): return prefix+'_'+uuid.uuid4().hex[:12]
def dump(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(value): return hashlib.sha256((value if isinstance(value,str) else dump(value)).encode()).hexdigest()
def parse(value, default=None):
    try: return json.loads(value)
    except (ValueError,TypeError): return default

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.path,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employees(id TEXT PRIMARY KEY,role TEXT NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL,bio TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'idle',reputation INTEGER NOT NULL DEFAULT 50,completed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,balance INTEGER NOT NULL CHECK(balance>=0));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,project_id TEXT NOT NULL,title TEXT NOT NULL,spec TEXT NOT NULL,status TEXT NOT NULL,budget INTEGER NOT NULL CHECK(budget>0),owner TEXT,origin TEXT NOT NULL,created_at TEXT NOT NULL,deadline TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 2,plan TEXT NOT NULL DEFAULT '[]',note TEXT NOT NULL DEFAULT '',artifact_id TEXT,parent_id TEXT,dedupe TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,author TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL,mode TEXT NOT NULL,sources TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,artifact_id TEXT NOT NULL,reviewer TEXT NOT NULL,passed INTEGER NOT NULL,checks TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY,debit TEXT NOT NULL,credit TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),task_id TEXT,proof TEXT,idempotency TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(debit) REFERENCES accounts(id),FOREIGN KEY(credit) REFERENCES accounts(id));
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,ref TEXT,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,observed_at TEXT,retrieved_at TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,delay_seconds REAL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,employee_id TEXT NOT NULL,created_at TEXT NOT NULL,mode TEXT NOT NULL,context_hash TEXT NOT NULL,decision TEXT NOT NULL,usage TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,description TEXT NOT NULL,artifact_id TEXT NOT NULL,verification_id TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,uses INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skill_runs(id TEXT PRIMARY KEY,skill_id TEXT NOT NULL,task_id TEXT NOT NULL,artifact_id TEXT NOT NULL,inputs TEXT NOT NULL,result TEXT NOT NULL,passed INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portfolios(employee_id TEXT PRIMARY KEY,cash_cents INTEGER NOT NULL CHECK(cash_cents>=0),btc_sats INTEGER NOT NULL CHECK(btc_sats>=0),initial_cents INTEGER NOT NULL,benchmark_price REAL,high_water_cents INTEGER NOT NULL,max_drawdown REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,task_id TEXT NOT NULL UNIQUE,employee_id TEXT NOT NULL,side TEXT NOT NULL,price REAL NOT NULL,quantity_sats INTEGER NOT NULL,notional_cents INTEGER NOT NULL,fee_cents INTEGER NOT NULL,source_id TEXT NOT NULL,created_at TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_decisions(task_id TEXT PRIMARY KEY,content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nav(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,employee_id TEXT NOT NULL,nav_cents INTEGER NOT NULL,price REAL NOT NULL,benchmark_cents INTEGER NOT NULL,drawdown REAL NOT NULL);
CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,kind TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,employee_id TEXT NOT NULL,title TEXT NOT NULL,spec TEXT NOT NULL,budget INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,task_id TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status,created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_sources_name ON sources(name,retrieved_at);
CREATE INDEX IF NOT EXISTS idx_decisions_employee ON decisions(employee_id,created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
''')
        with self.tx():
            if not self.get('run_id'):
                for key,value in {'run_id':uid('run'),'started_at':now(),'stop_at':STOP_AT,'paused':False,'state':'ready','model_status':'unverified','last_tick':None,'cycles':0,'source_mode':'real','heartbeat':None,'engine_version':'0.1.0','public_initial_credits':10000}.items():self.set(key,value)
                self.db.executemany('INSERT INTO employees(id,role,name,title,bio) VALUES(?,?,?,?,?)',ROLES)
                self.db.execute("INSERT INTO accounts VALUES('treasury',10000)")
                self.db.executemany('INSERT INTO accounts VALUES(?,0)',[(r[0],) for r in ROLES])
                self.db.execute("INSERT INTO portfolios VALUES('aiko',1000000,0,1000000,NULL,1000000,0)")
                self.event('platform','genesis','社区成立：公共资金 10,000 积分，模拟组合 $10,000',detail={'credits':'virtual','portfolio':'paper only'})
    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield;self.db.execute('COMMIT')
            except Exception:self.db.execute('ROLLBACK');raise
    def rows(self,sql,args=()):
        with self.lock:return [dict(r) for r in self.db.execute(sql,args).fetchall()]
    def one(self,sql,args=()):
        rows=self.rows(sql,args);return rows[0] if rows else None
    def get(self,key,default=None):
        row=self.one('SELECT value FROM meta WHERE key=?',(key,));return parse(row['value'],default) if row else default
    def set(self,key,value):self.db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,dump(value)))
    def event(self,actor,kind,title,ref=None,detail=None):
        self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',(uid('evt'),now(),actor,kind,title,ref,dump(detail or {})))
    def allowed(self):return now()<self.get('stop_at') and not self.get('paused') and self.get('state')!='completed'
    def transfer(self,debit,credit,amount,task_id,proof,key):
        if self.one('SELECT id FROM journal WHERE idempotency=?',(key,)):return False
        if not isinstance(amount,int) or amount<=0:raise ValueError('积分必须为正整数')
        self.db.execute('INSERT OR IGNORE INTO accounts VALUES(?,0)',(credit,))
        changed=self.db.execute('UPDATE accounts SET balance=balance-? WHERE id=? AND balance>=?',(amount,debit,amount)).rowcount
        if changed!=1:raise ValueError('可用资金不足')
        self.db.execute('UPDATE accounts SET balance=balance+? WHERE id=?',(amount,credit))
        self.db.execute('INSERT INTO journal VALUES(?,?,?,?,?,?,?,?)',(uid('txn'),debit,credit,amount,task_id,proof,key,now()))
        return True
    def create_task(self,kind,title,spec,budget=100,origin='platform',project_id=None,deadline=None,parent_id=None,dedupe=None):
        if kind not in ('A','B','C','D'):raise ValueError('该员工类型仍在规划中')
        if not isinstance(budget,int) or not 20<=budget<=500:raise ValueError('单任务预算须为 20–500 积分')
        if not title.strip() or len(title)>160:raise ValueError('任务标题需为 1–160 字')
        if not isinstance(spec,dict) or not spec.get('deliverable') or not spec.get('acceptance'):raise ValueError('缺少交付要求或验收标准')
        deadline=datetime.fromisoformat((deadline or self.get('stop_at')).replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
        if deadline>self.get('stop_at') or deadline<=now():raise ValueError('截止时间须在本次运行期内')
        if dedupe:
            existing=self.one('SELECT id FROM tasks WHERE dedupe=?',(dedupe,))
            if existing:return existing['id']
        if not self.allowed():raise ValueError('社区已暂停或达到截止时间')
        task_id=uid('task');project_id=project_id or {'A':'market','B':'research','C':'tools','D':'assurance'}[kind]
        self.db.execute('INSERT INTO tasks(id,kind,project_id,title,spec,status,budget,origin,created_at,deadline,parent_id,dedupe) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(task_id,kind,project_id,title,dump(spec),'open',budget,origin,now(),deadline,parent_id,dedupe))
        debit='escrow:'+parent_id if parent_id else 'treasury'
        self.transfer(debit,'escrow:'+task_id,budget,task_id,'任务预算获规则批准','reserve:'+task_id)
        self.event(origin,'task_created','发布任务：'+title,task_id,{'budget':budget,'acceptance':spec['acceptance']})
        return task_id
    def claim(self,task_id,employee_id,plan):
        task=self.one('SELECT * FROM tasks WHERE id=?',(task_id,));emp=self.one('SELECT * FROM employees WHERE id=?',(employee_id,))
        if not task or not emp or task['kind']!=emp['role'] or task['status']!='open' or task['deadline']<=now() or not self.allowed():raise ValueError('任务不可接取')
        if self.one("SELECT id FROM tasks WHERE owner=? AND status IN ('claimed','running')",(employee_id,)):raise ValueError('员工已有工作')
        self.db.execute("UPDATE tasks SET status='claimed',owner=?,plan=? WHERE id=?",(employee_id,dump(plan),task_id))
        self.db.execute("UPDATE employees SET status='working' WHERE id=?",(employee_id,))
        self.event(employee_id,'claimed','接单并制定计划：'+task['title'],task_id,{'plan':plan})
    def artifact(self,task_id,author,title,kind,content,sources=(),mode='real'):
        aid=uid('art');self.db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,task_id,author,title,kind,content,digest(content),now(),mode,dump(list(sources))))
        return aid
    def submit(self,task_id,content,kind,sources=()):
        task=self.one('SELECT * FROM tasks WHERE id=?',(task_id,))
        if task['status'] not in ('claimed','running') or task['deadline']<=now() or not self.allowed():raise ValueError('任务不能提交')
        aid=self.artifact(task_id,task['owner'],task['title'],kind,content,sources)
        self.db.execute("UPDATE tasks SET status='submitted',artifact_id=? WHERE id=?",(aid,task_id))
        self.db.execute("UPDATE employees SET status='idle' WHERE id=?",(task['owner'],))
        self.event(task['owner'],'submitted','提交成果，等待独立验收',task_id,{'artifact_id':aid,'sha256':digest(content)})
        return aid
    def verify_and_settle(self,task_id,reviewer,checks):
        task=self.one('SELECT * FROM tasks WHERE id=?',(task_id,))
        if not task or task['status']!='submitted':raise ValueError('成果不在待验收状态')
        artifact=self.one('SELECT * FROM artifacts WHERE id=?',(task['artifact_id'],))
        if reviewer==task['owner'] or reviewer==artifact['author']:raise ValueError('禁止自我验收')
        if reviewer!=('platform-auditor' if task['owner']=='qa' else 'qa'):raise ValueError('验收者不具备独立审核资格')
        if len(checks)<2:raise ValueError('验收证据不完整')
        if not self.allowed() or task['deadline']<=now():raise ValueError('运行已暂停或任务已截止')
        checks=list(checks)+[{'name':'交付物内容哈希完整','passed':digest(artifact['content'])==artifact['sha256'],'evidence':artifact['sha256']}]
        passed=bool(checks) and all(c.get('passed') is True for c in checks)
        vid=uid('verify');self.db.execute('INSERT INTO verifications VALUES(?,?,?,?,?,?,?)',(vid,task_id,artifact['id'],reviewer,int(passed),dump(checks),now()))
        self.artifact(task_id,reviewer,'独立验收报告 · '+task['title'],'verification',dump({'verdict':'passed' if passed else 'failed','reviewer':reviewer,'artifact_sha256':artifact['sha256'],'checks':checks,'verification_id':vid}))
        if passed:
            escrow=self.one('SELECT balance FROM accounts WHERE id=?',('escrow:'+task_id,))['balance']
            # Parent work pays only its remaining escrow after a bounded delegation.
            fee=min(10,escrow//10) if reviewer in [r[0] for r in ROLES] else 0
            if escrow-fee>0:self.transfer('escrow:'+task_id,task['owner'],escrow-fee,task_id,vid,'reward:'+task_id)
            if fee:self.transfer('escrow:'+task_id,reviewer,fee,task_id,vid,'review:'+task_id)
            self.db.execute("UPDATE tasks SET status='settled',note='独立验收通过，已结算' WHERE id=?",(task_id,))
            self.db.execute('UPDATE employees SET reputation=MIN(100,reputation+2),completed=completed+1 WHERE id=?',(task['owner'],))
            spec=parse(task['spec'],{});skill_id=spec.get('skill_key',task['kind']+'-evidence')
            self.db.execute('INSERT INTO skills VALUES(?,?,?,?,?, ?,1,0,?) ON CONFLICT(id) DO UPDATE SET uses=uses+1,artifact_id=excluded.artifact_id,verification_id=excluded.verification_id',(skill_id,spec.get('skill_name',task['title']),task['kind'],'来自已验收成果；复用时仍需验证',artifact['id'],vid,now()))
            self.event(reviewer,'settled','验收通过，奖励已结算',task_id,{'owner':task['owner'],'reward':escrow-fee,'review_reward':fee,'verification_id':vid})
        else:
            attempts=task['attempts']+1
            self.db.execute('UPDATE employees SET reputation=MAX(0,reputation-1) WHERE id=?',(task['owner'],))
            if attempts>=task['max_attempts']:self.cancel_task(task_id,'达到失败重试上限','failed')
            else:self.db.execute("UPDATE tasks SET status='open',owner=NULL,attempts=?,note=? WHERE id=?",(attempts,'验收未通过：'+dump(checks),task_id))
            self.event(reviewer,'rejected','验收未通过：保留证据，未发放奖励',task_id,{'verification_id':vid,'checks':checks})
        return vid,passed
    def cancel_task(self,task_id,reason,status='cancelled'):
        task=self.one('SELECT * FROM tasks WHERE id=?',(task_id,))
        if not task or task['status'] in ('settled','cancelled','failed','expired'):return
        for child in self.rows("SELECT id FROM tasks WHERE parent_id=? AND status NOT IN ('settled','cancelled','failed','expired')",(task_id,)):self.cancel_task(child['id'],'父任务已停止：'+reason,status)
        escrow=self.one('SELECT balance FROM accounts WHERE id=?',('escrow:'+task_id,))
        if escrow and escrow['balance']:self.transfer('escrow:'+task_id,'treasury',escrow['balance'],task_id,reason,'refund:'+task_id)
        self.artifact(task_id,'platform','停止记录 · '+task['title'],'outcome',dump({'status':status,'reason':reason,'previous_status':task['status'],'owner':task['owner'],'attempts':task['attempts'],'unspent_budget_refunded':escrow['balance'] if escrow else 0,'at':now(),'reward_paid':False}))
        self.db.execute('UPDATE tasks SET status=?,note=? WHERE id=?',(status,reason,task_id))
        if task['owner']:self.db.execute("UPDATE employees SET status='idle' WHERE id=?",(task['owner'],))
        self.event('platform',status,reason,task_id)
    def audit(self):
        balances={r['id']:r['balance'] for r in self.rows('SELECT * FROM accounts')};replayed={'treasury':10000}
        valid=True
        for r in self.rows('SELECT * FROM journal ORDER BY rowid'):
            replayed[r['debit']]=replayed.get(r['debit'],0)-r['amount'];replayed[r['credit']]=replayed.get(r['credit'],0)+r['amount']
            if replayed[r['debit']]<0:valid=False
            if r['idempotency'].startswith(('reward:','review:')):
                v=self.one('SELECT * FROM verifications WHERE id=?',(r['proof'],));t=self.one('SELECT * FROM tasks WHERE id=?',(r['task_id'],))
                if not v or not v['passed'] or v['reviewer']==t['owner']:valid=False
                elif not all(c.get('passed') is True for c in parse(v['checks'],[])):valid=False
                else:
                    artifact=self.one('SELECT * FROM artifacts WHERE id=?',(v['artifact_id'],))
                    if not artifact or digest(artifact['content'])!=artifact['sha256']:valid=False
        conserved=sum(balances.values())==10000
        matches=all(replayed.get(k,0)==v for k,v in balances.items())
        portfolio_ok=True
        for p in self.rows('SELECT * FROM portfolios'):
            cash=p['initial_cents'];sats=0
            for order in self.rows('SELECT * FROM orders WHERE employee_id=? ORDER BY rowid',(p['employee_id'],)):
                expected=int((Decimal(order['quantity_sats'])*Decimal(str(order['price']))/Decimal(1000000)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                fee=max(1,int((Decimal(expected)*Decimal('.001')).quantize(Decimal('1'),rounding=ROUND_HALF_UP)))
                sign=1 if order['side']=='buy' else -1
                cash-=sign*order['notional_cents']+order['fee_cents'];sats+=sign*order['quantity_sats']
                if min(cash,sats)<0 or expected!=order['notional_cents'] or fee!=order['fee_cents']:portfolio_ok=False
            if cash!=p['cash_cents'] or sats!=p['btc_sats']:portfolio_ok=False
        return {'passed':conserved and matches and valid and portfolio_ok,'paper_portfolio_reconciles':portfolio_ok,'total_credits':sum(balances.values()),'initial_credits':10000,'ledger_reconciles':matches,'rewards_have_proof':valid,'transactions':len(self.rows('SELECT id FROM journal')),'checked_at':now()}
    def snapshot(self):
        with self.lock:
            meta={r['key']:parse(r['value']) for r in self.rows('SELECT * FROM meta')}
            result={'meta':meta,'employees':self.rows('SELECT e.*,a.balance FROM employees e JOIN accounts a ON a.id=e.id'),'accounts':self.rows('SELECT * FROM accounts'),'tasks':self.rows('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 250'),'artifacts':self.rows('SELECT id,task_id,author,title,kind,sha256,created_at,mode,sources FROM artifacts ORDER BY created_at DESC LIMIT 250'),'verifications':self.rows('SELECT * FROM verifications ORDER BY created_at DESC LIMIT 250'),'events':self.rows('SELECT * FROM events ORDER BY created_at DESC LIMIT 200'),'sources':self.rows('SELECT * FROM sources ORDER BY retrieved_at DESC LIMIT 60'),'decisions':self.rows('SELECT * FROM decisions ORDER BY created_at DESC LIMIT 60'),'skills':self.rows('SELECT * FROM skills'),'skill_runs':self.rows('SELECT * FROM skill_runs ORDER BY created_at DESC LIMIT 100'),'journal':self.rows('SELECT * FROM journal ORDER BY created_at DESC LIMIT 250'),'portfolios':self.rows('SELECT * FROM portfolios'),'orders':self.rows('SELECT * FROM orders ORDER BY created_at DESC LIMIT 150'),'nav':self.rows('SELECT * FROM nav ORDER BY created_at DESC LIMIT 300')[::-1],'proposals':self.rows('SELECT * FROM proposals ORDER BY created_at DESC LIMIT 60'),'audit':self.audit()}
            for table,keys in {'tasks':['spec','plan'],'artifacts':['sources'],'events':['detail'],'sources':['payload'],'decisions':['decision','usage'],'verifications':['checks'],'proposals':['spec'],'skill_runs':['inputs','result']}.items():
                for row in result[table]:
                    for key in keys:row[key]=parse(row[key],{})
            return result
