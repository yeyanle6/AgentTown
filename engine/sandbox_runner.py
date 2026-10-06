"""Pure-function execution worker: no file/network tools, bounded CPU and output."""
import sys,json,math,statistics,datetime,resource,copy,types
resource.setrlimit(resource.RLIMIT_CPU,(3,3))
resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
# RLIMIT_AS is not portable on macOS; the parent imposes an 8-second deadline.
payload=json.loads(sys.stdin.read(1000000))
# Expose only pure public functions, never module internals such as json.codecs or statistics.sys.
allowed={'math':types.SimpleNamespace(**{k:getattr(math,k) for k in dir(math) if not k.startswith('_')}),
 'statistics':types.SimpleNamespace(**{k:getattr(statistics,k) for k in ['mean','fmean','median','median_low','median_high','mode','multimode','stdev','pstdev','variance','pvariance','quantiles','geometric_mean','harmonic_mean','StatisticsError']}),
 'datetime':types.SimpleNamespace(**{k:getattr(datetime,k) for k in ['date','datetime','time','timedelta','timezone','UTC']}),
 'json':types.SimpleNamespace(loads=json.loads,dumps=json.dumps)}
def importer(name,*args,**kwargs):
    if name not in allowed:raise ImportError('Import disabled')
    return allowed[name]
safe={k:__builtins__[k] if isinstance(__builtins__,dict) else getattr(__builtins__,k) for k in ['abs','all','any','bool','dict','enumerate','filter','float','int','isinstance','len','list','map','max','min','next','range','reversed','round','set','sorted','str','sum','tuple','zip','Exception','ValueError','TypeError','OverflowError','ZeroDivisionError']}
safe['__import__']=importer
namespace={'__builtins__':safe}
exec(payload['code'],namespace)
def equivalent(actual,expected):
    if isinstance(expected,dict):return isinstance(actual,dict) and set(actual)==set(expected) and all(equivalent(actual[k],v) for k,v in expected.items())
    if expected is None:return actual is None
    if isinstance(expected,(int,float)):return isinstance(actual,(int,float)) and math.isfinite(actual) and math.isclose(actual,expected,abs_tol=1e-6,rel_tol=1e-9)
    return actual==expected
results=[]
for i,case in enumerate(payload['cases']):
    args=copy.deepcopy(case['args']);original=copy.deepcopy(args)
    try:
        result=namespace[case['function']](*args)
        passed='error' not in case and equivalent(result,case.get('expected')) and args==original
        evidence={'input':case['args'],'expected':case.get('expected',case.get('error')),'actual':result,'input_unchanged':args==original}
    except Exception as exc:
        passed=case.get('error')==type(exc).__name__;evidence={'input':case['args'],'expected':case.get('error',case.get('expected')),'exception':type(exc).__name__,'message':str(exc)[:200]}
    results.append({'name':f'独立测试 {i+1:02d}','passed':passed,'evidence':evidence})
def json_safe(value):
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if isinstance(value,dict):return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,list):return [json_safe(v) for v in value]
    return value
print(json.dumps(json_safe(results),ensure_ascii=False,allow_nan=False))
