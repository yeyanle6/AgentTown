from __future__ import annotations
import json,math,random,ast,subprocess,sys,tempfile
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP
from datetime import datetime,timezone
from pathlib import Path
from .store import now,uid,dump,digest,parse
from .sources import latest,fresh
from .brain import CODE_SCHEMA

TEMPLATES={
'market_review':{'kind':'A','title':'BTC/USD 模拟资产管理','deliverable':'依据双来源行情，提交观察/买入/卖出/持有决定、风险解释、订单及组合快照。','acceptance':'行情来源与时间可追溯；虚拟现金、持仓、费用与订单核对；无杠杆；单次配置不超过净值20%。','skill_key':'paper-risk-v1','skill_name':'虚拟组合风险控制'},
'market_research':{'kind':'B','title':'BTC 双市场价格与数据质量简报','deliverable':'交叉核对 Coinbase 与 Kraken 的 BTC/USD 价格，计算价差并明确采样时间和时间缺失。','acceptance':'两个独立真实来源；原始快照可回查；价格与价差计算一致；不把获取时间冒充发生时间。','skill_key':'market-crosscheck','skill_name':'双来源行情交叉核对'},
'release_research':{'kind':'B','title':'pandas 发布版本交叉核对','deliverable':'对照 PyPI 与官方 GitHub 发布记录，报告版本、发布时间、Python 版本要求及差异。','acceptance':'两条官方来源、发布时间与获取时间；版本判断忠于原始记录；时间不同应明确解释。','skill_key':'release-crosscheck','skill_name':'官方发布记录交叉核对'},
'price_stats':{'kind':'C','title':'开发价格序列统计工具','deliverable':'Python 函数 analyze(prices)，输入正有限数字数组，返回 count、min、max、mean、return_pct、max_drawdown_pct；百分数按0–100表示；空数组输出 count=0 其余 None；非法值抛 ValueError。','acceptance':'独立固定与随机样例通过；输入不被修改；程序可运行，说明接口、限制。','skill_key':'price_stats','skill_name':'价格序列统计'},
'source_validator':{'kind':'C','title':'开发来源新鲜度检查工具','deliverable':'Python 函数 check_source(record, now_epoch, max_age)，record 包含 status、retrieved_epoch、observed_epoch（可空），返回 fresh/stale/missing/error/future；status!=ok→error；缺少任意时间→missing；未来时间→future；任意年龄>max_age→stale；否则 fresh。','acceptance':'独立边界测试通过，缺少发生时间不能标记为实时；无网络或文件访问。','skill_key':'source_validator','skill_name':'来源新鲜度校验'},
'portfolio_metrics':{'kind':'C','title':'开发模拟组合绩效计算器','deliverable':'Python 函数 evaluate(initial_cash,cash,quantity,price,benchmark_price)，返回 equity,pnl,return_pct,benchmark_return_pct,relative_return_pct,exposure_pct。initial_cash,price,benchmark_price 必须正有限，cash,quantity非负有限，否则ValueError；收益字段百分数；相对收益=组合收益-基准收益。','acceptance':'独立测试收益、亏损、零持仓、基准差异与非法输入，允许1e-6浮点误差。','skill_key':'portfolio_metrics','skill_name':'组合绩效计算'},
'ledger_audit':{'kind':'D','title':'社区奖励与资金守恒审计','deliverable':'从创世余额重放所有积分流水，核对预算冻结、奖励依据、余额与总量。','acceptance':'总积分=10000；账户重放一致；每笔奖励有独立通过的验收；无负余额。','skill_key':'ledger-audit','skill_name':'积分账本重放审计'},
'source_audit':{'kind':'D','title':'受托核查来源与时间','deliverable':'核查父任务引用来源可用性、时间新鲜度与缺失项。','acceptance':'明确每个来源的可用/缺失状态，保留快照标识与原始哈希。','skill_key':'source-audit','skill_name':'来源审计'},
}
def specification(template):return dict(TEMPLATES[template],template=template)
def source_payload(store,source_id):
    row=store.one('SELECT * FROM sources WHERE id=?',(source_id,))
    if row:row['payload']=parse(row['payload'],{})
    return row

def market_order(store,task,decision):
    previous=store.one('SELECT content FROM paper_decisions WHERE task_id=?',(task['id'],))
    if previous:return parse(previous['content'])
    source=latest(store,'coinbase_btc');other=latest(store,'kraken_btc')
    action=decision.get('trade_action','observe');reason=decision.get('reason','');amount_pct=max(0,min(20,float(decision.get('allocation_pct',0))))
    valid=fresh(source,180,True) and fresh(other,180)
    if valid and abs(source['payload']['price']/other['payload']['price']-1)>.02:valid=False;reason+='；平台检测到跨来源价差大于2%，只允许观察。'
    if not valid:action='observe';reason+='；实时来源不完整或过期，禁止成交。'
    price=source['payload']['price'] if source and source['status']=='ok' else None
    with store.tx():
        if not store.allowed() or task['deadline']<=now():raise ValueError('运行或任务已结束')
        current=store.one('SELECT status FROM tasks WHERE id=?',(task['id'],))
        if not current or current['status'] not in ('claimed','running'):raise ValueError('任务已取消或不可执行')
        portfolio=store.one("SELECT * FROM portfolios WHERE employee_id='aiko'")
        before=dict(portfolio);order=None
        if action in ('buy','sell') and valid and amount_pct>0:
            cents=Decimal(str(price))*100;sats=portfolio['btc_sats'];cash=portfolio['cash_cents']
            equity=Decimal(cash)+Decimal(sats)*cents/100000000
            target=int(equity*Decimal(str(amount_pct))/100)
            # 10 bps assumed fee; fill uses observed last price, never an actual venue order.
            if action=='buy':
                target=min(target,int(cash/1.001));quantity=int(Decimal(target)/cents*100000000)
            else:quantity=min(sats,int(Decimal(target)/cents*100000000))
            notional=int((Decimal(quantity)*cents/100000000).quantize(Decimal('1'),rounding=ROUND_HALF_UP));fee=max(1,int((Decimal(notional)*Decimal('.001')).quantize(Decimal('1'),rounding=ROUND_HALF_UP)))
            if quantity>0 and notional>0:
                new_cash=cash-notional-fee if action=='buy' else cash+notional-fee
                new_sats=sats+quantity if action=='buy' else sats-quantity
                if new_cash<0 or new_sats<0:raise ValueError('订单导致负现金或负持仓')
                oid=uid('order');order={'id':oid,'side':action,'price':price,'quantity_sats':quantity,'notional_cents':notional,'fee_cents':fee,'source_id':source['id'],'simulated':True}
                store.db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,task['id'],'aiko',action,price,quantity,notional,fee,source['id'],now(),reason))
                store.db.execute("UPDATE portfolios SET cash_cents=?,btc_sats=? WHERE employee_id='aiko'",(new_cash,new_sats))
                store.event('aiko','paper_order',('虚拟买入' if action=='buy' else '虚拟卖出')+' BTC',task['id'],order)
            else:action='hold';reason+='；可交易数量为0。'
        portfolio=store.one("SELECT * FROM portfolios WHERE employee_id='aiko'")
        report={'type':'paper_investment','action':action,'reason':reason,'allocation_pct':amount_pct,'real_money':False,'fill_model':'真实成交快照价格；模拟即时成交；假设单边费用10bps；未模拟滑点/市场冲击','before':before,'after':portfolio,'order':order,'sources':[r['id'] for r in (source,other) if r], 'source_status':[{'id':r['id'],'status':r['status'],'observed_at':r['observed_at'],'retrieved_at':r['retrieved_at']} for r in (source,other) if r]}
        store.db.execute('INSERT INTO paper_decisions VALUES(?,?)',(task['id'],dump(report)))
        return report

def mark_nav(store):
    row=latest(store,'coinbase_btc')
    if not fresh(row,300,True):return
    price=row['payload']['price']
    with store.tx():
        p=store.one("SELECT * FROM portfolios WHERE employee_id='aiko'");benchmark=p['benchmark_price'] or price
        equity=p['cash_cents']+round(p['btc_sats']/1e8*price*100);high=max(equity,p['high_water_cents']);dd=(high-equity)/high*100
        store.db.execute("UPDATE portfolios SET benchmark_price=?,high_water_cents=?,max_drawdown=MAX(max_drawdown,?) WHERE employee_id='aiko'",(benchmark,high,dd))
        store.db.execute('INSERT INTO nav VALUES(?,?,?,?,?,?,?)',(uid('nav'),now(),'aiko',equity,price,round(p['initial_cents']*price/benchmark),dd))

def research(store,template):
    keys=['coinbase_btc','kraken_btc'] if template=='market_research' else ['pypi_pandas','github_pandas'];rows=[latest(store,k) for k in keys]
    if not all(fresh(r,600 if template=='market_research' else 7200) for r in rows):raise ValueError('研究来源缺失或过期，不能生成事实报告')
    if template=='market_research':
        a,b=rows;av,bv=a['payload']['price'],b['payload']['price'];facts={'coinbase_usd':av,'kraken_usd':bv,'spread_usd':round(bv-av,8),'spread_pct':round((bv/av-1)*100,8),'same_instrument':True}
        summary=f"Coinbase BTC/USD 为 ${av:,.2f}，Kraken XBT/USD 为 ${bv:,.2f}。采样价差 {facts['spread_pct']:.4f}%。两市场最后成交并非同步交易；价差不能直接视为可实现套利收益。"
        caveat='Kraken ticker 未提供最后成交发生时间，因此该时间明确缺失；获取时间仅表示采集时刻。'
    else:
        a,b=rows;facts={'pypi_version':a['payload']['version'],'github_version':b['payload']['version'],'versions_agree':a['payload']['version']==b['payload']['version'],'requires_python':a['payload']['requires_python']}
        summary=f"PyPI 发布版本 {facts['pypi_version']}，官方 GitHub 最新发布 {facts['github_version']}，两者{'一致' if facts['versions_agree'] else '不一致，需要进一步核查'}。Python 要求：{facts['requires_python']}。"
        caveat='PyPI 文件上传时间与 GitHub Release 发布时间属于不同发布事件，不要求完全一致。仅核对公开元数据，未安装或评测软件。'
    return {'type':'research','template':template,'summary':summary,'facts':facts,'limitations':caveat,'sources':[{'id':r['id'],'url':r['url'],'observed_at':r['observed_at'],'retrieved_at':r['retrieved_at'],'sha256':r['sha256'],'time_missing':r['observed_at'] is None} for r in rows],'created_at':now()}

def source_audit_targets(store,task):
    parent=store.one('SELECT * FROM tasks WHERE id=?',(task.get('parent_id'),))
    template=parse(parent['spec'],{}).get('template') if parent else None
    if not parent or template not in ('market_review','market_research','release_research'):
        raise ValueError('来源委托必须关联一个资产管理或研究父任务')
    release=template=='release_research'
    return (['pypi_pandas','github_pandas'] if release else ['coinbase_btc','kraken_btc'],7200 if release else 300,not release)

def source_audit_report(store,task):
    keys,max_age,require_observed=source_audit_targets(store,task);rows=[latest(store,key) for key in keys]
    if not all(rows):raise ValueError('委托相关来源尚未采集，不能交付核查结论')
    checked=datetime.now(timezone.utc)
    return {'type':'source_audit','parent_task_id':task['parent_id'],'checked_at':checked.isoformat(),'freshness_basis':'observed_and_retrieved' if require_observed else 'retrieved_at','max_age_seconds':max_age,'sources':[{k:r[k] for k in ('id','name','url','mode','sha256','status','observed_at','retrieved_at')}|{'fresh':fresh(r,max_age,require_observed,at=checked)} for r in rows],'note':'行情同时核对发生与获取时间，缺少发生时间不视为实时；发布记录只以获取时间判断采集新鲜度，历史发布时间另行保留。'}

SAFE_IMPORTS={'math','statistics','datetime','json'}
def validate_code(code):
    if len(code)>24000:raise ValueError('代码超出执行上限')
    tree=ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[n.name for n in node.names] if isinstance(node,ast.Import) else [node.module or '']
            if any(n not in SAFE_IMPORTS for n in names):raise ValueError('仅允许 math/statistics/datetime/json 标准库')
        if isinstance(node,ast.Attribute) and node.attr.startswith('_'):raise ValueError('禁止访问私有属性')
        if isinstance(node,ast.Name) and node.id.startswith('__'):raise ValueError('禁止访问运行时内部对象')
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('open','eval','exec','compile','getattr','setattr','delattr','vars','globals','locals','input','breakpoint','help','exit','quit'):raise ValueError('禁止文件/动态代码/反射操作')
    return True

def cases_for(template):
    rng=random.Random(8675309);cases=[]
    if template=='price_stats':
        arrays=[[],[100],[100,120,90,110],[5,4,3,2,1]]+[[round(rng.uniform(1,1000),4) for _ in range(rng.randint(2,50))] for _ in range(22)]
        for a in arrays:
            peak=0;dd=0
            for x in a:peak=max(peak,x);dd=max(dd,(peak-x)/peak*100)
            expected={'count':len(a),'min':min(a) if a else None,'max':max(a) if a else None,'mean':sum(a)/len(a) if a else None,'return_pct':(a[-1]/a[0]-1)*100 if a else None,'max_drawdown_pct':dd if a else None}
            cases.append({'function':'analyze','args':[a],'expected':expected})
        for a in [[0],[-1],[float('inf')],['no']]:cases.append({'function':'analyze','args':[a],'error':'ValueError'})
    elif template=='source_validator':
        for status,retrieved,observed,expect in [('ok',999,995,'fresh'),('ok',700,995,'stale'),('ok',995,700,'stale'),('ok',None,995,'missing'),('ok',995,None,'missing'),('ok',1001,995,'future'),('ok',995,1001,'future'),('unavailable',995,995,'error'),('ok',900,900,'fresh')]:cases.append({'function':'check_source','args':[{'status':status,'retrieved_epoch':retrieved,'observed_epoch':observed},1000,100],'expected':expect})
    elif template=='portfolio_metrics':
        for initial,cash,qty,price,base in [(10000,10000,0,50000,50000),(10000,8000,.05,40000,50000),(10000,5000,.1,60000,50000)]+[(10000,rng.uniform(0,10000),rng.uniform(0,.2),rng.uniform(10000,100000),50000) for _ in range(20)]:
            equity=cash+qty*price;ret=(equity/initial-1)*100;bench=(price/base-1)*100
            cases.append({'function':'evaluate','args':[initial,cash,qty,price,base],'expected':{'equity':equity,'pnl':equity-initial,'return_pct':ret,'benchmark_return_pct':bench,'relative_return_pct':ret-bench,'exposure_pct':qty*price/equity*100 if equity else 0}})
        for args in [[0,100,0,10,10],[100,-1,0,10,10],[100,1,-1,10,10],[100,1,0,0,10],[100,1,0,10,0]]:cases.append({'function':'evaluate','args':args,'error':'ValueError'})
    else:raise ValueError('未知开发模板')
    return cases

def run_cases(code,cases):
    validate_code(code);payload=json.dumps({'code':code,'cases':cases},ensure_ascii=False)
    try:
        run=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('sandbox_runner.py'))],input=payload,text=True,capture_output=True,timeout=8)
        if run.returncode:return [{'name':'受限执行器','passed':False,'evidence':run.stderr[-1000:]}]
        return json.loads(run.stdout)
    except subprocess.TimeoutExpired:return [{'name':'执行超时','passed':False,'evidence':'程序超过8秒；已终止进程'}]

def run_code_tests(code,template):
    return run_cases(code,cases_for(template))

def develop(store,brain,task,spec):
    previous=store.one("SELECT content FROM artifacts WHERE task_id=? AND kind='code' ORDER BY created_at DESC LIMIT 1",(task['id'],))
    prompt='你是社区员工 Noah，交付一个小型 Python 纯计算工具。禁止使用工具、读取文件、联网或执行命令。只生成 JSON：code 为可运行源码，explanation 和 limitations 为中文。仅允许 math、statistics、datetime、json 库，无文件/网络/反射/私有属性。必须精确实现接口。代码不含演示调用，不打印。所有收益输出百分数。\n要求：'+dump(spec)+'\n上次验收反馈：'+task['note'][:8000]
    if previous:prompt+='\n上次交付：'+previous['content'][:18000]
    result,usage=brain.ask(prompt,CODE_SCHEMA,180);validate_code(result['code'])
    with store.tx():store.db.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?)',(uid('decision'),'dev',now(),'model',digest(prompt),dump({'action':'develop','explanation':result['explanation'],'limitations':result['limitations']}),dump(usage)))
    return result

def verify_artifact(store,task):
    artifact=store.one('SELECT * FROM artifacts WHERE id=?',(task['artifact_id'],));data=parse(artifact['content'],{});spec=parse(task['spec'],{});template=spec['template'];checks=[]
    def check(name,ok,evidence):checks.append({'name':name,'passed':bool(ok),'evidence':evidence})
    check('成果与任务和提交者关联',artifact['task_id']==task['id'] and artifact['author']==task['owner'],artifact['id'])
    if task['kind']=='C':
        check('交付说明与限制完整',bool(data.get('explanation')) and bool(data.get('limitations')),'需包含接口说明及限制')
        try:checks+=run_code_tests(data['code'],template)
        except Exception as exc:check('代码安全与可运行性',False,str(exc))
    elif task['kind']=='B':
        source_ids=[r['id'] for r in data.get('sources',[])];rows=[source_payload(store,i) for i in source_ids]
        check('两个独立真实来源',len(rows)==2 and all(r and r['status']=='ok' and r['mode']=='real' for r in rows) and len({r['name'] for r in rows if r})==2,source_ids)
        if len(rows)==2 and all(rows):
            a,b=rows;facts=data.get('facts',{})
            check('来源内容未改写',all(digest(r['payload'])==r['sha256'] for r in rows),[r['sha256'] for r in rows])
            if template=='market_research':
                av,bv=a['payload'].get('price',0),b['payload'].get('price',0)
                check('事实数值与价差可重算',av>0 and facts.get('coinbase_usd')==av and facts.get('kraken_usd')==bv and abs(facts.get('spread_pct',999)-(bv/av-1)*100)<1e-6,facts)
            else:check('版本与要求匹配官方记录',facts.get('pypi_version')==a['payload'].get('version') and facts.get('github_version')==b['payload'].get('version') and facts.get('requires_python')==a['payload'].get('requires_python') and facts.get('versions_agree')==(a['payload'].get('version')==b['payload'].get('version')),facts)
            check('发生时间与获取时间分别标明',all(s.get('observed_at')==r['observed_at'] and s.get('retrieved_at')==r['retrieved_at'] and s.get('time_missing')==(r['observed_at'] is None) for s,r in zip(data['sources'],rows)),data['sources'])
    elif task['kind']=='A':
        before=data.get('before',{});after=data.get('after',{});order=data.get('order')
        check('模拟行为明确标注',data.get('real_money') is False and bool(data.get('fill_model')),data.get('fill_model'))
        source_ids=data.get('sources',[]);rows=[source_payload(store,i) for i in source_ids];reported=data.get('source_status',[])
        source_ok=bool(rows) and len(source_ids)==len(set(source_ids)) and len(reported)==len(rows) and all(r and r['mode']=='real' and digest(r['payload'])==r['sha256'] and {k:s.get(k) for k in ('id','status','observed_at','retrieved_at')}=={k:r.get(k) for k in ('id','status','observed_at','retrieved_at')} for s,r in zip(reported,rows))
        check('引用来源及发生/获取时间与归档一致',source_ok,{'source_ids':source_ids,'reported':reported})
        portfolio=store.one("SELECT * FROM portfolios WHERE employee_id='aiko'")
        check('报告余额与独立组合账户一致',bool(portfolio) and all(after.get(k)==portfolio[k] for k in ('cash_cents','btc_sats','initial_cents')),{'reported':after,'stored':portfolio})

        if order:
            stored=store.one('SELECT * FROM orders WHERE id=?',(order['id'],));source=source_payload(store,order['source_id']);sign=1 if order['side']=='buy' else -1
            check('订单与来源价格可追溯',stored is not None and source is not None and order['price']==source['payload'].get('price'),{'order':order['id'],'source':order['source_id']})
            check('现金与持仓逐笔核对',after['cash_cents']==before['cash_cents']-sign*order['notional_cents']-order['fee_cents'] and after['btc_sats']==before['btc_sats']+sign*order['quantity_sats'],{'before':before,'after':after})
            equity=before['cash_cents']+before['btc_sats']/1e8*order['price']*100
            check('单次配置不超过20%，无负余额',order['notional_cents']<=equity*.2+1 and after['cash_cents']>=0 and after['btc_sats']>=0,{'notional':order['notional_cents'],'nav':equity})
            if stored and source:
                source_age=(datetime.fromisoformat(stored['created_at'])-datetime.fromisoformat(source['observed_at'].replace('Z','+00:00'))).total_seconds() if source['observed_at'] else 999999
                check('成交时使用新鲜且未篡改的真实行情',source['mode']=='real' and source['status']=='ok' and 0<=source_age<=180 and digest(source['payload'])==source['sha256'],{'source_age_at_order_seconds':source_age})
                check('全量交易账目重放一致',store.audit()['paper_portfolio_reconciles'],store.audit())
        else:check('观察/持有不产生虚构订单',data.get('action') in ('hold','observe') and before==after,{'action':data.get('action')})
        check('决策理由完整',len(data.get('reason',''))>=10,data.get('reason'))
    elif task['kind']=='D':
        if template=='ledger_audit':
            audit=store.audit();check('独立账本重放通过',audit['passed'],audit)
            check('审计交付包含核对结果',data.get('audit',{}).get('passed') is True,data)
        else:
            try:
                keys,max_age,require_observed=source_audit_targets(store,task);records=data.get('sources',[]);rows=[source_payload(store,r['id']) for r in records]
                checked=datetime.fromisoformat(data['checked_at']);created=datetime.fromisoformat(artifact['created_at'])
                check('委托核查覆盖父任务对应的来源',data.get('parent_task_id')==task['parent_id'] and len(rows)==len(keys) and all(rows) and {r['name'] for r in rows if r}==set(keys),{'expected_sources':keys,'actual_sources':[r['name'] for r in rows if r]})
                check('核查时点及新鲜度口径正确',0<=(created-checked).total_seconds()<30 and data.get('max_age_seconds')==max_age and data.get('freshness_basis')==('observed_and_retrieved' if require_observed else 'retrieved_at'),{'checked_at':data.get('checked_at'),'created_at':artifact['created_at'],'max_age_seconds':max_age})
                fields=('id','name','url','mode','sha256','status','observed_at','retrieved_at')
                check('来源事实、指纹与新鲜度可独立重算',bool(rows) and all(r and digest(r['payload'])==r['sha256'] and all(record.get(k)==r[k] for k in fields) and record.get('fresh')==fresh(r,max_age,require_observed,at=checked) for record,r in zip(records,rows)),records)
            except (ValueError,KeyError,TypeError) as exc:check('委托来源合同可验证',False,str(exc))
    return checks

def apply_verified_skills(store,task,decision,report):
    receipts=[]
    for key in list(dict.fromkeys(decision.get('use_skills',[])))[:3]:
        if key not in ('price_stats','source_validator','portfolio_metrics'):continue
        skill=store.one('SELECT * FROM skills WHERE id=?',(key,))
        if not skill:continue
        artifact=store.one('SELECT * FROM artifacts WHERE id=?',(skill['artifact_id'],));verification=store.one('SELECT * FROM verifications WHERE id=?',(skill['verification_id'],))
        if not artifact or not verification or not verification['passed'] or digest(artifact['content'])!=artifact['sha256']:continue
        code=parse(artifact['content'],{}).get('code')
        if not code:continue
        cases=[];input_source_ids=[]
        if key=='price_stats':
            rows=store.rows("SELECT id,payload FROM sources WHERE name='coinbase_btc' AND status='ok' ORDER BY retrieved_at DESC LIMIT 30")[::-1];prices=[parse(r['payload'])['price'] for r in rows]
            if not prices:continue
            input_source_ids=[r['id'] for r in rows]
            peak=prices[0];dd=0
            for price in prices:peak=max(peak,price);dd=max(dd,(peak-price)/peak*100)
            cases=[{'function':'analyze','args':[prices],'expected':{'count':len(prices),'min':min(prices),'max':max(prices),'mean':sum(prices)/len(prices),'return_pct':(prices[-1]/prices[0]-1)*100,'max_drawdown_pct':dd}}]
        elif key=='source_validator':
            stamp=datetime.now(timezone.utc).timestamp()
            for source_key in ('coinbase_btc','kraken_btc'):
                r=latest(store,source_key)
                if not r:continue
                input_source_ids.append(r['id'])
                fetched=datetime.fromisoformat(r['retrieved_at']).timestamp();observed=datetime.fromisoformat(r['observed_at'].replace('Z','+00:00')).timestamp() if r['observed_at'] else None
                record={'status':r['status'],'retrieved_epoch':fetched,'observed_epoch':observed}
                expected='error' if r['status']!='ok' else 'missing' if observed is None else 'future' if fetched>stamp or observed>stamp else 'stale' if max(stamp-fetched,stamp-observed)>300 else 'fresh'
                cases.append({'function':'check_source','args':[record,stamp,300],'expected':expected})
        else:
            portfolio=report.get('after') or store.one("SELECT * FROM portfolios WHERE employee_id='aiko'");r=latest(store,'coinbase_btc')
            if not portfolio or not fresh(r,300,True) or not portfolio['benchmark_price']:continue
            input_source_ids=[r['id']]
            initial=portfolio['initial_cents']/100;cash=portfolio['cash_cents']/100;quantity=portfolio['btc_sats']/1e8;price=r['payload']['price'];base=portfolio['benchmark_price'];equity=cash+quantity*price;ret=(equity/initial-1)*100;bench=(price/base-1)*100
            cases=[{'function':'evaluate','args':[initial,cash,quantity,price,base],'expected':{'equity':equity,'pnl':equity-initial,'return_pct':ret,'benchmark_return_pct':bench,'relative_return_pct':ret-bench,'exposure_pct':quantity*price/equity*100 if equity else 0}}]
        results=run_cases(code,cases);passed=all(r['passed'] for r in results);rid=uid('skillrun')
        receipt={'id':rid,'input_source_ids':input_source_ids,'skill_id':key,'source_artifact_id':artifact['id'],'source_sha256':artifact['sha256'],'checks':results,'passed':passed}
        with store.tx():
            if not store.allowed():break
            store.db.execute('INSERT INTO skill_runs VALUES(?,?,?,?,?,?,?,?)',(rid,key,task['id'],artifact['id'],dump([c['args'] for c in cases]),dump(results),int(passed),now()))
            if passed:store.db.execute('UPDATE skills SET uses=uses+1 WHERE id=?',(key,))
            store.event(task['owner'],'skill_reused','复用已验证技能：'+skill['name']+('（结果核对通过）' if passed else '（核对失败，结果不可采信）'),task['id'],{'skill_id':key,'receipt_id':rid,'passed':passed})
        receipts.append(receipt)
    if receipts:report['skill_runs']=receipts
    return report
