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
corrected='ths_force_rhythm_v1'
differences=[]
for key in ('screener','funnel_cases'):
 if normalize(original.get(key))!=normalize(copied.get(key)):differences.append(key)
original_catalogue=normalize(original['catalogue']);copied_catalogue=normalize(copied['catalogue'])
original_version=next(row['version'] for row in original_catalogue['items'] if row['strategy_id']==corrected)
for row in copied_catalogue['items']:
 if row['strategy_id']==corrected:
  if row['version']!='1.0.1':differences.append('rhythm.corrected_version')
  row['version']=original_version
if original_catalogue!=copied_catalogue:differences.append('catalogue')
for key in ('signals','backtests'):
 if set(original[key])!=set(copied[key]):differences.append(key+'.strategy_ids')
 for strategy,value in original[key].items():
  if strategy!=corrected and normalize(value)!=normalize(copied[key].get(strategy)):differences.append(key+'.'+strategy)
if copied['signals'][corrected]['strategy_version']!='1.0.1':differences.append('rhythm.signal_version')
plateau=copied['plateau']
if not plateau['regions'] and (plateau['recommended_point'] is not None or plateau['best_point'] is not None):differences.append('plateau.invalid_recommendation')
if plateau['recommended_point'] is not None and not plateau['recommended_point']['passes_hard_filters']:differences.append('plateau.failed_hard_filters')
sys.path.insert(0,str(root/'vendor/finaltrade/backend'))
from app.core.strategy_registry import StrategyRegistry
from app.core.force_rhythm_strategy import calculate_force_rhythm_signal,evaluate_force_rhythm_signal
from app.tdx_loader import load_candles_for_symbol
registry=StrategyRegistry()
if type(registry._get_plugin(corrected)).__name__!='ForceRhythmPlugin':differences.append('rhythm.plugin')
checked=0
for trade in copied['backtests'][corrected]['trades']:
 candles=load_candles_for_symbol(str(directory/'market/vipdoc'),trade['symbol'],window=120,market_data_source='tdx_only')
 history=[row for row in candles if row.time<=trade['signal_date']]
 if not evaluate_force_rhythm_signal(calculate_force_rhythm_signal(history),registry.get(corrected).default_params)['signal']:differences.append('rhythm.invalid_entry.'+trade['symbol'])
 checked+=1
counts={strategy:{'signals':len(value['items']),'trades':copied['backtests'][strategy]['stats']['trade_count']} for strategy,value in copied['signals'].items()}
pools=copied['screener']['step_pools']
report={'matched':not differences,'differences':differences,'strategyCount':len(counts),'unchangedStrategyCount':len(counts)-1,'expectedCorrections':{'rhythmPluginAndIndependentExits':True,'eligibleRegionRequired':True,'originalRhythmTrades':original['backtests'][corrected]['stats']['trade_count'],'correctedRhythmTrades':copied['backtests'][corrected]['stats']['trade_count']},'funnel':{key:len(value) for key,value in pools.items()},'funnelCases':{name:{key:len(value) for key,value in case['step_pools'].items()} for name,case in copied.get('funnel_cases',{}).items()},'plateauPoints':len(copied.get('plateau',{}).get('points',[])),'counts':counts,'fixture':str(directory)}
report['correctedRhythmEntryConditionsChecked']=checked
(root/'artifacts/finaltrade-parity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
if differences:raise AssertionError('Reference mismatch: '+','.join(differences))
