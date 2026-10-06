import unittest,tempfile,json
from datetime import datetime,timedelta,timezone
from engine.store import Store,dump,now,uid,digest
from engine.skills import specification,market_order,verify_artifact,validate_code,run_code_tests
from engine.sources import fresh
from engine.main import Engine

class EngineTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name+'/community.sqlite3')
  with self.s.tx():self.s.set('stop_at',(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat())
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def task(self,kind='B',template='market_research',budget=100,owner='lin'):
  with self.s.tx():
   tid=self.s.create_task(kind,'Test',specification(template),budget)
   self.s.claim(tid,owner,['Plan']);self.s.submit(tid,dump({'test':True}),'test')
  return tid
 def test_settlement_proof_idempotency_and_conservation(self):
  tid=self.task();checks=[{'name':'source','passed':True,'evidence':'test only'},{'name':'values','passed':True,'evidence':'test only'}]
  with self.s.tx():self.s.verify_and_settle(tid,'qa',checks)
  self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='lin'")['balance'],90)
  with self.assertRaises(ValueError):
   with self.s.tx():self.s.verify_and_settle(tid,'qa',checks)
  self.assertTrue(self.s.audit()['passed']);self.assertEqual(self.s.audit()['total_credits'],10000)
 def test_self_review_and_unqualified_review_rejected(self):
  tid=self.task()
  for reviewer in ('lin','dev','unknown'):
   with self.assertRaises(ValueError):
    with self.s.tx():self.s.verify_and_settle(tid,reviewer,[{'passed':True},{'passed':True}])
  self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='lin'")['balance'],0)
 def test_empty_checks_cannot_mint_rewards(self):
  tid=self.task()
  with self.assertRaises(ValueError):
   with self.s.tx():self.s.verify_and_settle(tid,'qa',[])
 def test_tampered_artifact_fails(self):
  tid=self.task()
  with self.s.tx():
   self.s.db.execute('UPDATE artifacts SET content=? WHERE task_id=?',('tampered',tid))
   _,passed=self.s.verify_and_settle(tid,'qa',[{'passed':True},{'passed':True}])
  self.assertFalse(passed);self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='lin'")['balance'],0)
 def test_budget_failure_rolls_back(self):
  before=len(self.s.rows('SELECT * FROM tasks'))
  with self.assertRaises(ValueError):
   with self.s.tx():self.s.create_task('B','Huge',specification('market_research'),100000)
  self.assertEqual(len(self.s.rows('SELECT * FROM tasks')),before);self.assertTrue(self.s.audit()['passed'])
 def test_delegation_is_bounded_and_cancel_cascades(self):
  with self.s.tx():
   parent=self.s.create_task('B','Parent',specification('market_research'),100)
   child=self.s.create_task('D','Child',specification('source_audit'),40,parent_id=parent)
  self.assertEqual(self.s.one('SELECT balance FROM accounts WHERE id=?',('escrow:'+parent,))['balance'],60)
  with self.assertRaises(ValueError):
   with self.s.tx():self.s.create_task('D','Too much',specification('source_audit'),80,parent_id=parent)
  with self.s.tx():self.s.cancel_task(parent,'cancel')
  self.assertEqual(self.s.one('SELECT status FROM tasks WHERE id=?',(child,))['status'],'cancelled');self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],10000)
 def test_missing_and_stale_prices_never_trade(self):
  with self.s.tx():
   tid=self.s.create_task('A','No data',specification('market_review'),100);self.s.claim(tid,'aiko',['observe'])
  report=market_order(self.s,self.s.one('SELECT * FROM tasks WHERE id=?',(tid,)),{'trade_action':'buy','allocation_pct':20,'reason':'test'})
  self.assertEqual(report['action'],'observe');self.assertEqual(len(self.s.rows('SELECT * FROM orders')),0)
 def test_order_crash_recovery_is_idempotent(self):
  with self.s.tx():
   for key in ('coinbase_btc','kraken_btc'):
    payload={'price':100000};self.s.db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid('src'),key,'https://example.test',now(),now(),'real','ok',0,dump(payload),digest(payload),None))
   tid=self.s.create_task('A','Trade',specification('market_review'),100);self.s.claim(tid,'aiko',['buy'])
  task=self.s.one('SELECT * FROM tasks WHERE id=?',(tid,));a=market_order(self.s,task,{'trade_action':'buy','allocation_pct':20,'reason':'verified test'});b=market_order(self.s,task,{'trade_action':'buy','allocation_pct':20,'reason':'retry'})
  self.assertEqual(a,b);self.assertEqual(len(self.s.rows('SELECT * FROM orders')),1);self.assertEqual(a['after']['btc_sats'],2000000)
 def test_portfolio_replay_detects_unrecorded_cash(self):
  with self.s.tx():self.s.db.execute("UPDATE portfolios SET cash_cents=cash_cents+1")
  self.assertFalse(self.s.audit()['paper_portfolio_reconciles'])
 def test_module_internals_do_not_escape_sandbox(self):
  from engine.skills import run_cases
  for code in ['import json\ndef analyze(x):\n return json.codecs.open("/tmp/agenttown-forbidden","w")','import statistics\ndef analyze(x):\n return statistics.sys.version']:
   result=run_cases(code,[{'function':'analyze','args':[[]],'expected':None}]);self.assertFalse(all(r['passed'] for r in result))
 def test_deadline_blocks_claim_and_payment(self):
  tid=self.task()
  with self.s.tx():self.s.set('stop_at','2000-01-01T00:00:00+00:00')
  with self.assertRaises(ValueError):
   with self.s.tx():self.s.verify_and_settle(tid,'qa',[{'passed':True},{'passed':True}])
  engine=Engine(self.tmp.name);engine.store=self.s
  for kind,payload in [('pause',{}),('resume',{}),('tick',{}),('create_task',{'template':'ledger_audit','budget':20}),('cancel_task',{'task_id':tid}),('set_employee',{'employee_id':'qa','disabled':True})]:
   with self.assertRaises(ValueError):engine.command({'kind':kind,'payload':payload})
  self.assertEqual(self.s.one('SELECT status FROM tasks WHERE id=?',(tid,))['status'],'submitted');self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM commands')['n'],0)
 def test_foreign_code_capabilities_rejected(self):
  for code in ['import os','open("/etc/passwd")','x.__class__','getattr(x,"secret")']:
   with self.assertRaises(ValueError):validate_code(code)
 def test_faulty_program_does_not_pass(self):
  checks=run_code_tests('def check_source(record, now_epoch, max_age):\n return "fresh"','source_validator');self.assertTrue(any(not c['passed'] for c in checks))
 def test_source_timestamp_required_for_trading(self):
  row={'status':'ok','mode':'real','retrieved_at':now(),'observed_at':None};self.assertTrue(fresh(row));self.assertFalse(fresh(row,require_observed=True))
 def test_failed_attempt_limit_refunds(self):
  tid=self.task()
  with self.s.tx():self.s.verify_and_settle(tid,'qa',[{'passed':False},{'passed':True}]);self.s.claim(tid,'lin',['retry']);self.s.submit(tid,'{}','test');self.s.verify_and_settle(tid,'qa',[{'passed':False},{'passed':True}])
  self.assertEqual(self.s.one('SELECT status FROM tasks WHERE id=?',(tid,))['status'],'failed');self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],10000);self.assertTrue(self.s.audit()['passed'])
 def test_source_acquisition_time_is_recorded_after_network_response(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  from engine.sources import fetch_feed
  raw={'price':'100','bid':'99','ask':'101','volume':'2','trade_id':1,'time':'2020-01-01T00:00:00Z'}
  with patch('engine.sources.now',return_value='2020-01-01T00:00:03+00:00') as stamp:
   def response(*args,**kwargs):
    self.assertFalse(stamp.called)
    return SimpleNamespace(returncode=0,stdout=dump(raw),stderr='')
   with patch('engine.sources.subprocess.run',side_effect=response):row=fetch_feed('coinbase_btc')
  self.assertEqual(row['status'],'ok');self.assertEqual(row['retrieved_at'],'2020-01-01T00:00:03+00:00');self.assertEqual(row['delay_seconds'],3);self.assertEqual(row['payload']['_agenttown']['retrieved_at_basis'],'request_finished')
 def test_invalid_external_values_are_missing_and_remain_json_serializable(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  from engine.sources import fetch_feed
  base={'price':'100','bid':'99','ask':'101','volume':'2','trade_id':1,'time':'2020-01-01T00:00:00Z'}
  responses=[SimpleNamespace(returncode=0,stdout=dump(dict(base,bid='NaN')),stderr=''),SimpleNamespace(returncode=0,stdout=dump(dict(base,time='invalid timestamp')),stderr=''),SimpleNamespace(returncode=28,stdout='',stderr='request timed out')]
  for response in responses:
   with patch('engine.sources.subprocess.run',return_value=response):row=fetch_feed('coinbase_btc')
   self.assertEqual(row['status'],'unavailable');self.assertNotIn('price',row['payload']);self.assertIsNone(row['observed_at']);self.assertIsNone(row['delay_seconds']);self.assertTrue(row['error']);json.dumps(row,allow_nan=False)
 def delegated_task(self,template):
  role='A' if template=='market_review' else 'B';owner='aiko' if role=='A' else 'lin'
  with self.s.tx():
   for key in ('coinbase_btc','kraken_btc','pypi_pandas','github_pandas'):
    release='pandas' in key;payload={'version':'3.0.3','requires_python':'>=3.11'} if release else {'price':100000}
    observed=(datetime.now(timezone.utc)-timedelta(days=20)).isoformat() if release else None if key=='kraken_btc' else now()
    self.s.db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid('src'),key,'https://example.test/'+key,observed,now(),'real','ok',None,dump(payload),digest(payload),None))
   parent=self.s.create_task(role,'Parent',specification(template),100);self.s.claim(parent,owner,['Delegate source checks'])
   child=self.s.create_task('D','Child',specification('source_audit'),40,parent_id=parent);self.s.claim(child,'qa',['Verify parent sources'])
  return parent,child
 def test_release_delegation_checks_release_sources_and_shared_budget(self):
  engine=Engine(self.tmp.name);engine.store=self.s;parent,child=self.delegated_task('release_research')
  engine.work(self.s.one('SELECT * FROM tasks WHERE id=?',(child,)))
  task=self.s.one('SELECT * FROM tasks WHERE id=?',(child,));report=json.loads(self.s.one('SELECT content FROM artifacts WHERE id=?',(task['artifact_id'],))['content'])
  self.assertEqual({r['name'] for r in report['sources']},{'pypi_pandas','github_pandas'});self.assertEqual(report['freshness_basis'],'retrieved_at');self.assertTrue(all(r['fresh'] for r in report['sources']))
  engine.review();engine.work(self.s.one('SELECT * FROM tasks WHERE id=?',(parent,)));engine.review()
  self.assertEqual(self.s.one('SELECT status FROM tasks WHERE id=?',(child,))['status'],'settled');self.assertEqual(self.s.one('SELECT status FROM tasks WHERE id=?',(parent,))['status'],'settled')
  self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],9900);self.assertEqual(self.s.one("SELECT SUM(balance) AS total FROM accounts WHERE id LIKE 'escrow:%'")['total'],0);self.assertTrue(self.s.audit()['passed'])
 def test_market_delegation_cannot_claim_missing_time_is_fresh(self):
  from engine.skills import source_audit_report
  _,child=self.delegated_task('market_review');task=self.s.one('SELECT * FROM tasks WHERE id=?',(child,));report=source_audit_report(self.s,task)
  self.assertEqual({r['name'] for r in report['sources']},{'coinbase_btc','kraken_btc'});self.assertFalse(next(r for r in report['sources'] if r['name']=='kraken_btc')['fresh'])
  with self.s.tx():self.s.submit(child,dump(report),'audit',[r['id'] for r in report['sources']])
  task=self.s.one('SELECT * FROM tasks WHERE id=?',(child,));self.assertTrue(all(c['passed'] for c in verify_artifact(self.s,task)))
  report['sources'][1]['fresh']=True;content=dump(report)
  with self.s.tx():self.s.db.execute('UPDATE artifacts SET content=?,sha256=? WHERE id=?',(content,digest(content),task['artifact_id']))
  self.assertFalse(all(c['passed'] for c in verify_artifact(self.s,task)))
 def test_source_delegation_requires_parent_and_cannot_request_public_budget(self):
  from engine.skills import source_audit_report
  engine=Engine(self.tmp.name);engine.store=self.s
  with self.assertRaises(ValueError):source_audit_report(self.s,{'parent_id':None})
  with self.assertRaises(ValueError):engine.command({'kind':'create_task','payload':{'template':'source_audit','budget':40}})
  spec=specification('source_audit');decision={'proposal_template':'source_audit','proposal_title':'Unbound audit','proposal_kind':'D','proposal_deliverable':spec['deliverable'],'proposal_acceptance':spec['acceptance'],'proposal_budget':40}
  with self.s.tx():engine.propose(self.s.one("SELECT * FROM employees WHERE id='qa'"),decision)
  self.assertEqual(self.s.one('SELECT status FROM proposals ORDER BY rowid DESC LIMIT 1')['status'],'rejected');self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],10000)
 def test_proposals_cannot_silently_drop_delivery_requirements(self):
  engine=Engine(self.tmp.name);engine.store=self.s;employee=self.s.one("SELECT * FROM employees WHERE id='lin'")
  contract=specification('ledger_audit');decision={'proposal_template':'ledger_audit','proposal_title':'Audit next milestone','proposal_kind':'D','proposal_deliverable':contract['deliverable'],'proposal_acceptance':contract['acceptance'],'proposal_budget':40}
  for change in [{'proposal_deliverable':contract['deliverable']+' and deliver a new game'},{'proposal_acceptance':'Trust me'},{'proposal_kind':'C'},{'proposal_budget':201}]:
   with self.s.tx():engine.propose(employee,dict(decision,**change))
   self.assertEqual(self.s.one('SELECT status FROM proposals ORDER BY rowid DESC LIMIT 1')['status'],'rejected')
   self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],10000)
  with self.s.tx():engine.propose(employee,decision)
  p=self.s.one('SELECT * FROM proposals ORDER BY rowid DESC LIMIT 1');task=self.s.one('SELECT * FROM tasks WHERE id=?',(p['task_id'],))
  self.assertEqual(p['status'],'approved');self.assertEqual(json.loads(task['spec'])['deliverable'],decision['proposal_deliverable']);self.assertEqual(json.loads(task['spec'])['acceptance'],decision['proposal_acceptance']);self.assertEqual(task['deadline'],self.s.get('stop_at'))
  with self.s.tx():engine.propose(employee,decision)
  self.assertEqual(self.s.one('SELECT status FROM proposals ORDER BY rowid DESC LIMIT 1')['status'],'rejected');self.assertEqual(self.s.one("SELECT balance FROM accounts WHERE id='treasury'")['balance'],9960)
 def test_observation_report_checks_archived_time_and_account(self):
  with self.s.tx():
   for key in ('coinbase_btc','kraken_btc'):
    payload={'price':100000};self.s.db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid('src'),key,'https://example.test',now(),now(),'real','ok',0,dump(payload),digest(payload),None))
   tid=self.s.create_task('A','Observe',specification('market_review'),100);self.s.claim(tid,'aiko',['Observe actual account'])
  task=self.s.one('SELECT * FROM tasks WHERE id=?',(tid,));report=market_order(self.s,task,{'trade_action':'observe','allocation_pct':0,'reason':'Insufficient evidence for trading; preserve capital'})
  with self.s.tx():self.s.submit(tid,dump(report),'investment',report['sources'])
  task=self.s.one('SELECT * FROM tasks WHERE id=?',(tid,))
  self.assertTrue(all(c['passed'] for c in verify_artifact(self.s,task)))
  for corruption in ('time','balance'):
   invalid=json.loads(dump(report))
   if corruption=='time':invalid['source_status'][0]['observed_at']='2000-01-01T00:00:00Z'
   else:invalid['before']['cash_cents']+=1;invalid['after']['cash_cents']+=1
   content=dump(invalid)
   with self.s.tx():self.s.db.execute('UPDATE artifacts SET content=?,sha256=? WHERE id=?',(content,digest(content),task['artifact_id']))
   self.assertFalse(all(c['passed'] for c in verify_artifact(self.s,task)))
 def test_source_sync_requires_explicit_archive_ack(self):
  from pathlib import Path
  from unittest.mock import patch
  from types import SimpleNamespace
  engine=Engine(self.tmp.name);engine.store=self.s
  Path(self.tmp.name,'cloud.json').write_text(dump({'url':'https://example.test','token':'test','sync_key':'test'}))
  sid=uid('src')
  with self.s.tx():self.s.db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,'test','https://example.test',None,now(),'real','ok',None,dump({'price':123.45}),digest({'price':123.45}),None))
  for response,expected in [({'ok':True,'commands':[]},0),({'ok':True,'commands':[],'sources_accepted_ids':['unrelated']},0),({'ok':True,'commands':[],'sources_accepted_ids':[sid]},1)]:
   with patch('engine.main.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=dump(response))):engine._sync()
   self.assertEqual(self.s.get('cloud_source_rowid',0),expected)
  sent=json.loads(Path(self.tmp.name,'sync-payload.json').read_text())['sources'][0]
  self.assertEqual(digest(sent['payload']),sent['sha256'])
 def test_concurrent_snapshot_export_is_atomic(self):
  from concurrent.futures import ThreadPoolExecutor
  from pathlib import Path
  engine=Engine(self.tmp.name);engine.store=self.s
  with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:engine.export(),range(10)))
  self.assertEqual(json.loads(Path(self.tmp.name,'snapshot.json').read_text())['meta']['run_id'],self.s.get('run_id'))
if __name__=='__main__':unittest.main()
