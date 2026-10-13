"""Compare completed oracle runs without recomputing either result."""
import json,sys
from pathlib import Path
directory=Path(sys.argv[1]);root=Path(__file__).resolve().parent.parent
original=json.loads((directory/'original/result.json').read_text(encoding='utf-8'))
copied=json.loads((directory/'copied/result.json').read_text(encoding='utf-8'))
def normalize(value):
 if isinstance(value,dict):return {key:sorted(item) if key=='labels' else normalize(item) for key,item in value.items() if key not in {'run_id','created_at','generated_at','updated_at','trace_id','cache_hit'}}
 if isinstance(value,list):return [normalize(item) for item in value]
 return value
differences=[key for key in original if normalize(original[key])!=normalize(copied[key])]
counts={strategy:{'signals':len(value['items']),'trades':copied['backtests'][strategy]['stats']['trade_count']} for strategy,value in copied['signals'].items()}
pools=copied['screener']['step_pools']
report={'matched':not differences,'differences':differences,'strategyCount':len(counts),'funnel':{key:len(value) for key,value in pools.items()},'funnelCases':{name:{key:len(value) for key,value in case['step_pools'].items()} for name,case in copied.get('funnel_cases',{}).items()},'plateauPoints':len(copied.get('plateau',{}).get('points',[])),'counts':counts,'fixture':str(directory)}
(root/'artifacts/finaltrade-parity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
if differences:raise AssertionError('Reference mismatch: '+','.join(differences))
