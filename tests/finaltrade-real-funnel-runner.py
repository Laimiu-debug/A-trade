"""Compare a captured desktop funnel with an isolated original engine."""
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from service.finaltrade_runtime import load_reference
captured=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
directory=Path(tempfile.mkdtemp(prefix='atrade-original-real-funnel-'))
store=load_reference(directory,captured['source'],sys.argv[2])
from app.models import ScreenerParams,ScreenerRunDetail
captured['run']=ScreenerRunDetail.model_validate(captured['run']).model_dump(mode='json')
original=store.create_screener_run(ScreenerParams.model_validate(captured['run']['params'])).model_dump(mode='json')

def normalize(value):
    if isinstance(value,dict):
        return {key:sorted(item) if key=='labels' else normalize(item) for key,item in value.items() if key not in {'run_id','created_at'}}
    if isinstance(value,list):
        return [normalize(item) for item in value]
    return value

differences=[key for key in original if key not in {'run_id','created_at','step_pools'} and normalize(original[key])!=normalize(captured['run'][key])]
tie_order=[]
for key,rows in original['step_pools'].items():
    actual=captured['run']['step_pools'][key]
    order=lambda row:(-row['ret40'],row['symbol'],json.dumps(normalize(row),ensure_ascii=False,sort_keys=True))
    if normalize(sorted(rows,key=order))!=normalize(sorted(actual,key=order)):
        differences.append('step_pools.'+key)
    elif normalize(rows)!=normalize(actual):
        # These pools inherit return ranking; only equal-return order may vary.
        if key in {'input','step1','step2','step3'} and [row['ret40'] for row in rows]==[row['ret40'] for row in actual]:
            tie_order.append(key)
        else:
            differences.append('step_pools.'+key+'.ranking')
report={'matchedFieldsAndMembers':not differences,'orderMatched':not tie_order,'differences':differences,'equalReturnOrderDifferences':tie_order,'counts':{key:len(rows) for key,rows in original['step_pools'].items()}}
Path(sys.argv[3]).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
if differences:raise AssertionError('Actual data funnel differs: '+','.join(differences))
