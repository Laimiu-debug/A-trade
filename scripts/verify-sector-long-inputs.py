"""Independent direct source windows and branch composition; no V4 import."""
from collections import defaultdict
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_long_system_input_compare',ROOT/'scripts/verify-market-sector-stock-inputs.py')
V=importlib.util.module_from_spec(spec);spec.loader.exec_module(V)
POLICIES=('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry','catchupOnly')


def audit_suspensions(dataset,matrix):
    expected=np.zeros(matrix['close'].shape,bool)
    rows={s:i for i,s in enumerate(matrix['symbols'].tolist())};dates={d:i for i,d in enumerate(matrix['calendar'].tolist())}
    checked=0;seen=set()
    for security in dataset['securities']:
        symbol=security['symbol']
        if symbol not in rows or symbol in seen:raise ValueError('Suspension source cohort differs')
        seen.add(symbol)
        for bar in security['bars']:
            if bar['date'] not in dates:raise ValueError('Source bar outside independent matrix calendar')
            expected[rows[symbol],dates[bar['date']]]=bool(bar.get('suspended'))
            checked+=1
    if seen!=set(rows) or not np.array_equal(matrix['explicitSuspended'],expected):raise ValueError('Explicit suspension source-mask mismatch')
    return {'passed':True,'sourceBarsChecked':checked,'explicitSuspendedSlots':int(expected.sum()),'missingFlagsNotClaimedHistoricalStatus':True}


def audit(matrix,joins,context,anchor_source,actual):
    calendar,symbols=matrix['calendar'].tolist(),matrix['symbols'].tolist()
    maps={j['snapshotId']:{s['sectorCode']:s['cohortRowIndices'] for s in j['sectors']} for j in joins}
    anchor=defaultdict(dict)
    for policy,signals in anchor_source['schedules'].items():
        for signal in signals:
            m=anchor_source['metadata'][signal['signalId']]
            if m['pattern']=='catchup':anchor[policy,signal['signalDate']][m['sectorCode']]=m
    schedules,metadata,counts={p:[] for p in POLICIES},{},[]
    for day in context['daily']:
        i=day['index'];ordered=sorted((s for s in day['sectors'] if s['featureValid']),key=lambda s:(-s['score'],s['sectorCode']))
        chosen_long={}
        for sector in ordered:
            code=sector['sectorCode'];rows=maps[day['snapshotId']][code];choices=[]
            returns=np.full(len(rows),np.nan)
            if i>=120:
                good=matrix['priceValid'][rows,i-120:i+1].all(axis=1)&(~matrix['explicitSuspended'][rows,i-120:i+1]).all(axis=1)
                for j,r in enumerate(rows):
                    if good[j]:returns[j]=round(float(matrix['close'][r,i])*100)/round(float(matrix['close'][r,i-120])*100)-1
            known=np.isfinite(returns);n=int(known.sum())
            if len(rows)>=5 and n/len(rows)>=.95 and i>=140:
                ranks=V.ranks(returns);average=math.fsum(returns[known].tolist())/n
                for j,r in enumerate(rows):
                    if not (returns[j]>0 and returns[j]>average and ranks[j]>=.8):continue
                    c=matrix['close'][r,i]
                    if not (c>max(matrix['high'][r,i-120:i]) and matrix['close'][r,i-1]<=max(matrix['high'][r,i-121:i-1])):continue
                    block=slice(i-140,i+1)
                    if not matrix['priceValid'][r,block].all() or matrix['explicitSuspended'][r,block].any():continue
                    if not all(np.isfinite(matrix[k][r,block]).all() and (matrix[k][r,block]>0).all() for k in ('open','high','low','close','volume','amount')):continue
                    if not ((matrix['low'][r,block]<=np.minimum(matrix['open'][r,block],matrix['close'][r,block])).all() and (np.maximum(matrix['open'][r,block],matrix['close'][r,block])<=matrix['high'][r,block]).all()):continue
                    cent=lambda t:round(float(matrix['close'][r,t])*100)
                    now_cent=cent(i);sum60=sum(cent(t) for t in range(i-59,i+1));sum120=sum(cent(t) for t in range(i-119,i+1));old120=sum(cent(t) for t in range(i-139,i-19))
                    if not (now_cent*60>sum60 and sum60*2>sum120 and sum120>old120 and cent(i-1)*100<=cent(i-21)*115):continue
                    amount=math.fsum(matrix['amount'][r,i-20:i].tolist())/20
                    if amount<50_000_000:continue
                    floor=max(float(min(matrix['low'][r,i-10:i])),.95*c)
                    terms=[]
                    for t in range(i-19,i+1):
                        previous=matrix['close'][r,t-1]
                        terms.append(max(matrix['high'][r,t]-matrix['low'][r,t],abs(matrix['high'][r,t]-previous),abs(matrix['low'][r,t]-previous))/previous)
                    ntr=math.fsum(terms)/20
                    if not (0<=ntr<1 and 0<floor<c):continue
                    info={'pattern':'longLeaderBreakout','signalClose':float(c),'failurePrice':float(floor),'meanNtr20':float(ntr),
                        'sectorCode':code,'snapshotId':day['snapshotId'],'sectorScore':sector['score'],'stockReturn120Midrank':float(ranks[j]),'priorAmount20':amount,
                        'longEvidence':{'windowFrom':calendar[i-140],'windowThrough':day['date'],'observedSlots':141,'priorHigh120':float(max(matrix['high'][r,i-120:i])),
                            'ma60':sum60/6000,'ma120':sum120/12000,'ma120Lag20':old120/12000,'prior20Return':cent(i-1)/cent(i-21)-1,
                            'return120':float(returns[j]),'industryMeanReturn120':average,'industry120Known':n,'industryMembers':len(rows),
                            'priorLow10':float(min(matrix['low'][r,i-10:i])),'protective95Close':float(.95*c)}}
                    choices.append(((-amount,-ranks[j],symbols[r]),symbols[r],info))
            chosen_long[code]=sorted(choices,key=lambda q:q[0])[0] if choices else None
            if choices:counts.append({'date':day['date'],'sectorCode':code,'longLeaders':len(choices)})
        for policy in POLICIES:
            if policy!='withoutMarketAdmission' and day['market']['admission'] is not True:continue
            base_policy=policy if policy in ('withoutMarketAdmission','withoutSectorAdmission') else 'completeSystem'
            chosen=[]
            for sector in ordered:
                code=sector['sectorCode']
                if policy!='withoutSectorAdmission' and code not in day['selectedSectors']:continue
                leader=chosen_long[code] if policy!='catchupOnly' else None
                if leader:
                    _,symbol,info=leader;chosen.append((symbol,info))
                elif policy!='withoutCatchupEntry' and code in anchor[base_policy,day['date']]:
                    prior=anchor[base_policy,day['date']][code]
                    info={k:deepcopy(v) for k,v in prior.items() if k not in ('signalId','symbol','signalDate','originalRank')}
                    info.update(anchorSourceId=prior['signalId'],anchorOriginalRank=prior['originalRank']);chosen.append((prior['symbol'],info))
            for rank,(symbol,info) in enumerate(chosen[:5],1):
                identifier=f'sector-long-v4:{policy}:{day["date"]}:{symbol}'
                s={'signalId':identifier,'strategyId':'marketSectorStockV1','symbol':symbol,'signalDate':day['date'],'originalRank':rank}
                schedules[policy].append(s);metadata[identifier]={**info,**{k:v for k,v in s.items() if k!='strategyId'}}
    V.same(actual['schedules'],schedules,'All long/catchup policy schedules')
    V.same(actual['metadata'],metadata,'Every direct source-window and anchor field')
    V.same(actual['longCandidateCounts'],counts,'All pre-admission long candidates')
    return {'passed':True,'decisionDays':len(context['daily']),'signals':sum(len(v) for v in schedules.values()),
        'allLongWindowsPeerRanksAnchorsAndCompositionChecked':True,'marketIndustryContext':'Previously independently verified raw V1 admission; holding-health projection verified separately'}
