from __future__ import annotations
import subprocess,json,concurrent.futures
from datetime import datetime,timezone
from .store import now,uid,dump,digest
FEEDS={
 'coinbase_btc':('Coinbase · BTC/USD','https://api.exchange.coinbase.com/products/BTC-USD/ticker'),
 'kraken_btc':('Kraken · XBT/USD','https://api.kraken.com/0/public/Ticker?pair=XBTUSD'),
 'pypi_pandas':('PyPI · pandas','https://pypi.org/pypi/pandas/json'),
 'github_pandas':('GitHub · pandas releases','https://api.github.com/repos/pandas-dev/pandas/releases/latest'),
}
def fetch_feed(key):
    label,url=FEEDS[key];observed=None;payload={};status='unavailable';error=None;delay=None
    try:
        run=subprocess.run(['/usr/bin/curl','--fail','--silent','--show-error','--max-time','20','--max-filesize','3000000','-H','User-Agent: AgentTown/0.1 (research simulation)','-H','Accept: application/json',url],capture_output=True,text=True,timeout=25)
        if run.returncode:raise ValueError(run.stderr[:300])
        raw=json.loads(run.stdout)
        if key=='coinbase_btc':
            observed=raw.get('time');payload={'price':float(raw['price']),'bid':float(raw['bid']),'ask':float(raw['ask']),'volume':float(raw['volume']),'trade_id':raw.get('trade_id'),'currency':'USD','symbol':'BTC'}
        elif key=='kraken_btc':
            if raw.get('error'):raise ValueError(str(raw['error']))
            data=next(iter(raw['result'].values()));payload={'price':float(data['c'][0]),'bid':float(data['b'][0]),'ask':float(data['a'][0]),'currency':'USD','symbol':'BTC','time_note':'该端点未提供成交时间，获取时间不等于发生时间'}
        elif key=='pypi_pandas':
            version=raw['info']['version'];release=raw.get('releases',{}).get(version,[]);observed=release[0].get('upload_time_iso_8601') if release else None
            payload={'name':'pandas','version':version,'summary':raw['info'].get('summary'),'requires_python':raw['info'].get('requires_python'),'release_url':'https://pypi.org/project/pandas/'+version+'/','project_urls':raw['info'].get('project_urls',{})}
        else:
            observed=raw.get('published_at');payload={'name':'pandas','version':raw['tag_name'].lstrip('v'),'release_url':raw['html_url'],'prerelease':raw['prerelease'],'summary':raw.get('body','')[:10000]}
        if 'price' in payload and not 0<payload['price']<10000000:raise ValueError('不合法行情值')
        json.dumps(payload,allow_nan=False)
        if observed:
            dt=datetime.fromisoformat(observed.replace('Z','+00:00'));delay=(datetime.now(timezone.utc)-dt).total_seconds()
            if delay < -60:raise ValueError('来源发生时间在未来，拒绝作为实时依据')
        status='ok'
    except Exception as exc:error=str(exc)[:400];payload={};observed=None;delay=None
    retrieved=now()
    if observed and delay is not None:delay=(datetime.fromisoformat(retrieved)-datetime.fromisoformat(observed.replace('Z','+00:00'))).total_seconds()
    payload['_agenttown']={'retrieved_at_basis':'request_finished'}
    return {'id':uid('src'),'name':key,'url':url,'observed_at':observed,'retrieved_at':retrieved,'mode':'real','status':status,'delay_seconds':delay,'payload':payload,'sha256':digest(payload),'error':error}
def collect(store,include_research=True):
    keys=list(FEEDS) if include_research else ['coinbase_btc','kraken_btc']
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(fetch_feed,keys))
    with store.tx():
        for row in rows:
            store.db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?)',(row['id'],row['name'],row['url'],row['observed_at'],row['retrieved_at'],row['mode'],row['status'],row['delay_seconds'],dump(row['payload']),row['sha256'],row['error']))
            previous=store.one('SELECT status FROM sources WHERE name=? AND id<>? ORDER BY retrieved_at DESC LIMIT 1',(row['name'],row['id']))
            if not previous or previous['status']!=row['status']:store.event('platform','source',FEEDS[row['name']][0]+(' 已连接' if row['status']=='ok' else ' 连接失败'),row['id'],{'url':row['url'],'error':row['error']})
    return rows
def latest(store,key):
    row=store.one('SELECT * FROM sources WHERE name=? ORDER BY retrieved_at DESC LIMIT 1',(key,))
    if row:row['payload']=json.loads(row['payload'])
    return row
def fresh(row,max_age=300,require_observed=False,at=None):
    if not row or row['status']!='ok' or row['mode']!='real':return False
    checked=at or datetime.now(timezone.utc)
    age=(checked-datetime.fromisoformat(row['retrieved_at'])).total_seconds()
    if not -10<=age<=max_age:return False
    if require_observed:
        if not row.get('observed_at'):return False
        observed_age=(checked-datetime.fromisoformat(row['observed_at'].replace('Z','+00:00'))).total_seconds()
        if not -10<=observed_age<=max_age:return False
    return True
