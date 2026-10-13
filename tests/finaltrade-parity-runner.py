"""Differential oracle: identical files/parameters, independently loaded engines."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from service.finaltrade_runtime import load_reference
parser=argparse.ArgumentParser()
parser.add_argument('directory');parser.add_argument('output');parser.add_argument('--oracle')
args=parser.parse_args()
directory=Path(args.directory)
fixture=json.loads((directory/'fixture.json').read_text(encoding='utf-8'))
store=load_reference(Path(args.output).parent/'state',directory/'market',args.oracle)
from app.models import ScreenerParams,ScreenerStepConfigs,BacktestRunRequest,BacktestPlateauRunRequest
catalogue=store.list_strategies().model_dump(mode='json')
params=ScreenerParams(markets=['sh','sz'],mode='strict',return_window_days=40,as_of_date=fixture['end'],turnover_threshold=.01,amount_threshold=5e7,amplitude_threshold=.01,top_n=500)
run=store.create_screener_run(params)
result={'catalogue':catalogue,'screener':run.model_dump(mode='json'),'signals':{},'backtests':{}}
configs=ScreenerStepConfigs.model_validate({
 'step2':{'retrace_min':0,'retrace_max':.8,'max_pullback_days':30,'min_ma10_above_ma20_days':0,'min_ma5_above_ma10_days':0,'max_price_vs_ma20':.5,'require_above_ma20':False,'allow_b_trend':True},
 'step3':{'min_vol_slope20':0,'min_up_down_volume_ratio':0,'max_pullback_volume_ratio':5,'allow_blowoff_top':True,'allow_divergence_5d':True,'allow_upper_shadow_risk':True,'allow_degraded':True},
 'step4':{'final_top_n':8,'min_ai_confidence':0,'allowed_theme_stages':['发酵中','高潮','退潮','Unknown'],'allow_degraded':True},
})
result['funnel_cases']={}
for name,mode,start,end in [('strict','strict',1,500),('loose','loose',1,500),('rank_window','strict',5,20)]:
 case_configs=configs.model_copy(deep=True)
 case_configs.step1.rank_start=start;case_configs.step1.top_n=end
 case_configs=ScreenerStepConfigs.model_validate(case_configs.model_dump())
 case=store.create_screener_run(params.model_copy(update={'mode':mode,'step_configs':case_configs}))
 result['funnel_cases'][name]=case.model_dump(mode='json')
 print('funnel',name,case.step_summary.model_dump(),flush=True)
assert all(getattr(case.step_pools,key) for case in [store.get_screener_run(result['funnel_cases']['strict']['run_id'])] for key in ['input','step1','step2','step3','step4']), 'Fixture must exercise every funnel stage'
for item in catalogue['items']:
 strategy=item['strategy_id']
 if not item['enabled']:continue
 defaults=store._strategy_registry.get(strategy).default_params
 signals=store.get_signals(mode='full_market',strategy_id=strategy,strategy_params=defaults,as_of_date=fixture['end'],window_days=60,min_score=0,min_event_count=0,signal_age_min=0,signal_age_max=240)
 result['signals'][strategy]=signals.model_dump(mode='json')
 payload=BacktestRunRequest(mode='full_market',strategy_id=strategy,strategy_params=defaults,date_from=fixture['start'],date_to=fixture['end'],window_days=60,min_score=0,min_event_count=0,max_symbols=20,enable_advanced_analysis=False)
 backtest=store.run_backtest(payload).model_dump(mode='json')
 result['backtests'][strategy]={key:backtest.get(key) for key in ['stats','trades','equity_curve','drawdown_curve','candidate_count','skipped_count','fill_rate','max_concurrent_positions','open_signals','plan_signals','range']}
 print(strategy,len(signals.items),backtest['stats']['trade_count'],flush=True)
base=BacktestRunRequest(mode='full_market',strategy_id='ths_force_rhythm_v1',date_from=fixture['start'],date_to=fixture['end'],window_days=60,min_score=0,min_event_count=0,max_symbols=20,enable_advanced_analysis=False)
plateau=store.run_backtest_plateau(BacktestPlateauRunRequest(base_payload=base,sampling_mode='grid',stop_loss_list=[.04,.06],take_profit_list=[.12,.18],max_points=4))
result['plateau']=plateau.model_dump(mode='json')
print('plateau',plateau.evaluated_combinations,flush=True)
Path(args.output).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
