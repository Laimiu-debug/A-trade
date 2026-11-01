"""Describe actual entries with previous-close information; fit no signal."""
from datetime import datetime, timezone
from decimal import Decimal as D
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'artifacts/strategy-search/public-trading-evidence-v1'
OUT=BASE/'guiyin-entry-context-001'


def ref(p):return {'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}


def main():
    if OUT.exists():raise ValueError('Preserve prior entry-context description')
    source=BASE/'guiyin-ledger-validation-001/canonical-closed-cycles.json'
    es=[e for e in json.loads(source.read_text(encoding='utf-8')) if e['securityType']=='equity' and e['entryDate']>='2024-09-24']
    trades_path=BASE/'guiyin-ledger-audit-001/trades.json'
    by_row={t['sourceRow']:t for t in json.loads(trades_path.read_text(encoding='utf-8'))}
    matrix=ROOT/'artifacts/strategy-search/market-sector-stock-system-v1/price-matrix-v1/prices.npz'
    market_path=ROOT/'artifacts/strategy-search/market-sector-stock-accounts-v1/attempt-001/formation.candidate.json.gz'
    assert ref(matrix)['sha256']=='07411701006794937428d1c1b2232d1c99456b34db6c032fc7ca12f076eaee6e'
    assert ref(market_path)['sha256']=='b5628746426dd360fe056139c866d9abfecbf97023c41ef817536466b92075c9'
    with gzip.open(market_path,'rt',encoding='utf-8') as f:market={d['date']:d['market'] for d in json.load(f)['daily']}
    rows=[];rank_cache={}
    with np.load(matrix,allow_pickle=False) as p:
        dates=p['calendar'].tolist();symbols=p['symbols'].tolist();di={d:i for i,d in enumerate(dates)};si={s:i for i,s in enumerate(symbols)}
        close,high,valid=p['close'],p['high'],p['priceValid']
        for e in es:
            t=by_row[e['startRow']];i=di[e['entryDate']];j=i-1;previous_date=dates[j]
            price=D(t['grossYuan'])/t['quantity'];s=si.get(t['symbol'])
            row={'episodeId':e['episodeId'],'code':e['code'],'name':e['name'],'entryDate':e['entryDate'],
                 'firstSourceRow':e['startRow'],'firstExecutionAveragePrice':str(price),
                 'contextCutoffDate':previous_date,'contextAvailableBeforeEntryDay':True,
                 'marketTurnoverProxyPriorDayYuan':market[previous_date]['turnover'] if previous_date in market else None,
                 'marketPriorFiveToPriorTwentyRatio':market[previous_date]['turnover5ToPrior20'] if previous_date in market else None,
                 'existingResearchMarketAdmissionPriorClose':market[previous_date]['admission'] if previous_date in market else None,
                 'isTradersOwnRecordedMarketModel':False,
                 'firstPriceRelativeToPreviousClose':None,'entryPriceBucket':'source-context-unavailable',
                 'prior20Return':None,'prior20ReturnRankInFixedSurvivorCohort':None,'rankPopulation':None,
                 'firstPriceAbovePrior20High':None,'fullyClosedCycleReturnAfterVisibleCharges':e['cumulativePurchaseCostReturn'],
                 'outcomeLabelIsForRetrospectiveDescriptionOnly':True}
            if s is not None and valid[s,j]:
                prev=float(close[s,j]);rel=float(price)/prev-1
                row['firstPriceRelativeToPreviousClose']=rel
                row['entryPriceBucket']='above-previous-close' if rel>1e-6 else 'below-previous-close' if rel<-1e-6 else 'at-previous-close'
                if j>=20 and valid[s,j-20:j+1].all():
                    if j not in rank_cache:
                        mask=valid[:,j-20:j+1].all(axis=1)
                        values=close[mask,j]/close[mask,j-20]-1
                        rank_cache[j]=np.sort(values)
                    ret=float(close[s,j]/close[s,j-20]-1);rank=rank_cache[j]
                    row.update({'prior20Return':ret,'prior20ReturnRankInFixedSurvivorCohort':float(np.searchsorted(rank,ret,side='right')/len(rank)),
                                'rankPopulation':len(rank),'firstPriceAbovePrior20High':float(price)>float(np.max(high[s,j-19:j+1]))})
            rows.append(row)
    spec=importlib.util.spec_from_file_location('independent_guiyin_stats',ROOT/'scripts/validate-guiyin-ledger-analysis.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    ids={r['episodeId']:r for r in rows}
    buckets={key:m.stats([e for e in es if ids[e['episodeId']]['entryPriceBucket']==key]) for key in sorted(set(r['entryPriceBucket'] for r in rows))}
    descriptions={}
    for label,condition in [('winningCycles',lambda x:x>0),('losingCycles',lambda x:x<0)]:
        subset=[r for r in rows if condition(r['fullyClosedCycleReturnAfterVisibleCharges'])]
        ranks=[r['prior20ReturnRankInFixedSurvivorCohort'] for r in subset if r['prior20ReturnRankInFixedSurvivorCohort'] is not None]
        descriptions[label]={'cycles':len(subset),'rankCoverage':len(ranks),'medianPrior20Rank':statistics.median(ranks) if ranks else None,
                             'entryBuckets':{b:sum(r['entryPriceBucket']==b for r in subset) for b in buckets}}
    readout={'schema':'actual-entry-previous-close-description-v1','completedAt':datetime.now(timezone.utc).isoformat(),
             'builder':ref(Path(__file__)),'cycles':ref(source),'trades':ref(trades_path),'matrix':ref(matrix),'market':ref(market_path),
             'primaryCycles':len(rows),'priceContextAvailable':sum(r['firstPriceRelativeToPreviousClose'] is not None for r in rows),
             'prior20ContextAvailable':sum(r['prior20Return'] is not None for r in rows),'rows':rows,'entryPriceBuckets':buckets,
             'outcomeConditionalDescriptions':descriptions,
             'limitations':['All observations are retrospective on a selected champion; no predictive/causal effect tested.',
                            'Fixed surviving-stock cohort and raw local daily prices; not complete historical investable or theme universes.',
                            'Actual entry execution prices are source facts, but intraday triggers, queue/capacity and decision timestamps are absent.',
                            'Existing research market admission is not the trader rule. Exchange listing boards are not thematic sectors.'],
             'parameterSearch':False,'newStrategyTrials':0,'newStrategyAccounts':0,'targetAchieved':False}
    OUT.mkdir();(OUT/'readout.json').write_text(json.dumps(readout,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    chosen=[r for r in rows if r['episodeId'] in ['002820:676:750','000759:778:905','600203:510:525','002693:388:430']]
    print(json.dumps({'readout':ref(OUT/'readout.json'),'cycles':len(rows),'prior20Coverage':readout['prior20ContextAvailable'],
                      'buckets':buckets,'descriptions':descriptions,'selectedCaseContext':chosen},ensure_ascii=True))


if __name__=='__main__':main()
