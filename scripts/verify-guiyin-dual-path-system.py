"""Separate raw-window formation enumeration and cash/share/fee replay oracle."""
from collections import Counter, defaultdict
import math
from statistics import mean
import numpy as np


def check(ok, label):
    if not ok:raise ValueError(label)


def equal(a,b,label):
    if isinstance(a,(float,int)) and isinstance(b,(float,int)):
        check(math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-7),f'{label}: {a} != {b}')
    else:check(a==b,f'{label}: {a} != {b}')


def ranks(values):
    v=np.asarray(values,float);a=v[np.isfinite(v)]
    return np.asarray([(np.sum(a<x)+.5*np.sum(a==x))/len(a) if np.isfinite(x) and len(a) else np.nan for x in v])


def formation_audit(m,joins,f,benchmark,original):
    maps={j['snapshotId']:j for j in joins};old={d['date']:d for d in original['daily']}
    symbols=m['symbols'].tolist();actual=defaultdict(list)
    for e in f['allCandidates']:actual[e['signalDate']].append(e)
    events,sector_rows=0,0
    for d in f['daily']:
        i=d['index'];join=maps[d['snapshotId']]
        check(join['publicationDate']<d['date'] and join['availableFrom']<=d['date'],'Publication lookahead')
        equal(d['market'],old[d['date']]['market'],'Inherited market context unchanged')
        membership={g['sectorCode']:np.asarray(g['cohortRowIndices'],int) for g in join['sectors']}
        c=m['close'];valid=m['priceValid'];amt=m['amount']
        with np.errstate(divide='ignore',invalid='ignore'):
            ret5=np.where(valid[:,i-5:i+1].all(axis=1),c[:,i]/c[:,i-5]-1,np.nan)
            ret20=np.where(valid[:,i-20:i+1].all(axis=1),c[:,i]/c[:,i-20]-1,np.nan)
            prior20=np.where(valid[:,i-21:i].all(axis=1),c[:,i-1]/c[:,i-21]-1,np.nan)
            ma20=np.where(valid[:,i-19:i+1].all(axis=1),np.mean(c[:,i-19:i+1],axis=1),np.nan)
            a5=np.where((valid[:,i-4:i+1] & (amt[:,i-4:i+1]>0)).all(axis=1),np.mean(amt[:,i-4:i+1],axis=1),np.nan)
            ap20=np.where((valid[:,i-20:i] & (amt[:,i-20:i]>0)).all(axis=1),np.mean(amt[:,i-20:i],axis=1),np.nan)
        all_expected={};sector_expected=[]
        for s in d['sectors']:
            rows=membership[s['sectorCode']];n=len(rows)
            masks=[np.isfinite(v[rows]) for v in (ret5,ret20,ma20,a5/ap20)]
            counts=[int(v.sum()) for v in masks];equal(counts,s['coverageCounts'],'Raw sector counts')
            ratio=d['market']['turnover5ToPrior20']
            known=n>=5 and min(counts)/n>=.95 and ratio is not None and ratio>0 and np.isfinite(benchmark[i]) and np.isfinite(benchmark[i-20]) and np.isfinite(benchmark[i-5])
            equal(bool(known),s['known'],'Raw sector known state')
            if known:
                avg5=float(np.mean(ret5[rows[masks[0]]]))
                avg20=float(np.mean(ret20[rows[masks[1]]]))
                eligible=rows[masks[2]]
                breadth=float(np.mean(np.rint(c[eligible,i]*100).astype(np.int64)*20>np.rint(c[eligible,i-19:i+1]*100).astype(np.int64).sum(axis=1)))
                participation=float(a5[rows[masks[3]]].sum()/ap20[rows[masks[3]]].sum()/ratio)
                rel5=avg5-(benchmark[i]/benchmark[i-5]-1);rel20=avg20-(benchmark[i]/benchmark[i-20]-1)
                for k,v in dict(return5=avg5,return20=avg20,breadth20=breadth,participation=participation,relative5=rel5,relative20=rel20).items():equal(s[k],v,'Raw sector:'+k)
                equal(s['strong'],bool(avg5>0 and rel20>0 and breadth>.5 and participation>=1),'Sector strong state')
                sector_expected.append(s)
            prior_rank=ranks(prior20[rows])
            for j,r in enumerate(rows):
                if not valid[r,i-21:i+1].all() or not (m['volume'][r,i-21:i+1]>0).all() or not (amt[r,i-21:i+1]>0).all() or m['explicitSuspended'][r,i-21:i+1].any():continue
                cc,pc=c[r,i],c[r,i-1]
                tr=[]
                for k in range(i-19,i+1):tr.append(max(m['high'][r,k]-m['low'][r,k],abs(m['high'][r,k]-c[r,k-1]),abs(m['low'][r,k]-c[r,k-1]))/c[r,k-1])
                ntr=math.fsum(tr)/20
                if not 0<ntr<1 or ap20[r]<5e7 or not np.isfinite(prior_rank[j]):continue
                hi,lo=m['high'][r,i],m['low'][r,i]
                pos=(cc-lo)/(hi-lo) if hi>lo else 0
                ccents,pcents,hc,lc=round(cc*100),round(pc*100),round(hi*100),round(lo*100)
                a=pcents*100<=115*round(c[r,i-21]*100) and cc>max(m['high'][r,i-20:i]) and pc<=max(m['high'][r,i-21:i-1]) and ccents*100>=pcents*105 and amt[r,i]>=1.5*ap20[r] and hi>lo and ccents*4>=hc*3+lc
                b=pcents>round(c[r,i-21]*100) and prior_rank[j]>=.8 and pcents*10>sum(round(x*100) for x in c[r,i-10:i]) and cc<pc and lc*5<=sum(round(x*100) for x in c[r,i-5:i]) and ccents*10>=sum(round(x*100) for x in c[r,i-9:i+1]) and hi>lo and ccents*2>=hc+lc and amt[r,i]>=.5*ap20[r]
                if a or b:
                    all_expected[symbols[r]]=dict(pattern='A_ignition' if a else 'B_corePullback',floor=max(.95*cc,min(min(m['low'][r,i-2:i+1]),.99*cc)),ntr=ntr,rank=float(prior_rank[j]))
            sector_rows+=1
        observed={e['symbol']:e for e in actual[d['date']]}
        equal(set(observed),set(all_expected),'All candidate success/failure independent enumeration')
        for symbol,e in observed.items():
            expected=all_expected[symbol]
            equal(e['pattern'],expected['pattern'],'Candidate path');equal(e['failurePrice'],expected['floor'],'Candidate floor')
            equal(e['meanNtr20'],expected['ntr'],'Raw true range');equal(e['priorIndustryRank20'],expected['rank'],'Prior rank')
        events+=len(observed)
        if sector_expected:
            rr=[ranks([s[k] for s in sector_expected]) for k in ('relative5','relative20','participation')]
            for j,s in enumerate(sector_expected):equal(s['score'],sum(round(v[j]*2*len(sector_expected)) for v in rr)/(6*len(sector_expected)),'Independent sector score')
        ordered=sorted(sector_expected,key=lambda s:(-s['score'],s['sectorCode']))
        equal(d['selectedSectors'],[s['sectorCode'] for s in ordered if s['strong']][:3],'Top3 rotation')
    # Independent exact schedule composition from raw-audited candidates, all policies.
    for policy,schedule in f['schedules'].items():
        expected=[]
        for d in f['daily']:
            if policy!='withoutMarket' and d['market']['admission'] is not True:continue
            sectors=sorted([s for s in d['sectors'] if s['known']],key=lambda s:(-s['score'],s['sectorCode']))
            chosen=[]
            for s in sectors:
                if policy!='withoutSector' and s['sectorCode'] not in d['selectedSectors']:continue
                choices=[e for e in actual[d['date']] if e['sectorCode']==s['sectorCode'] and (policy!='Aonly' or e['pattern']=='A_ignition') and (policy!='Bonly' or e['pattern']=='B_corePullback')]
                if choices:chosen.append(min(choices,key=lambda e:(e['pattern'],-e['priorIndustryRank20'],-e['priorAmount20'],e['symbol'])))
            expected.extend((d['date'],e['symbol'],j) for j,e in enumerate(chosen[:5],1))
        equal([(e['signalDate'],e['symbol'],e['originalRank']) for e in schedule],expected,'Independent complete schedule:'+policy)
    return dict(passed=True,decisionDays=len(f['daily']),rawSectorWindowsChecked=sector_rows,allCandidateEventsIndependentlyEnumerated=events,allSevenSchedulesChecked=True,publicationDayExcluded=True,marketContextInheritedWithoutChange=True,accountOutcomesObserved=False)


def source_cycle_coverage(m,joins,f,cycles):
    dates=m['calendar'].tolist();indices={d:i for i,d in enumerate(dates)};symbols={s[2:]:s for s in m['symbols'].tolist()}
    by_date={d['date']:d for d in f['daily']};candidates={(e['signalDate'],e['symbol']):e for e in f['allCandidates']}
    selected={(s['signalDate'],s['symbol']) for s in f['schedules']['complete']}
    out=[]
    for e in cycles:
        if e['securityType']!='equity' or e['entryDate']<'2024-09-24':continue
        i=indices.get(e['entryDate']);cutoff=dates[i-1] if i is not None and i else None
        symbol=symbols.get(e['code']);candidate=candidates.get((cutoff,symbol));context=by_date.get(cutoff)
        out.append(dict(episodeId=e['episodeId'],code=e['code'],name=e['name'],entryDate=e['entryDate'],previousCloseCutoff=cutoff,
            sourceCycleReturnPct=e['cumulativePurchaseCostReturn']*100,sourceOutcome='win' if e['cumulativePurchaseCostReturn']>0 else 'loss',
            previousCloseDailyPath=candidate['pattern'] if candidate else None,completeSystemScheduledAtPreviousClose=(cutoff,symbol) in selected,
            marketAdmittedAtPreviousClose=context['market']['admission'] if context else None,
            dailyPathUnknownOrAbsentReason='notInPinnedCohort' if symbol is None else 'dailyConfirmedPathNotPresent' if candidate is None else None))
    check(len(out)==134,'All134 source primary closed equity cycles required')
    groups=[]
    for path in ('A_ignition','B_corePullback',None):
        group=[r for r in out if r['previousCloseDailyPath']==path]
        groups.append(dict(path=path or 'noDailyProxySignal',cycles=len(group),wins=sum(r['sourceOutcome']=='win' for r in group),losses=sum(r['sourceOutcome']=='loss' for r in group),scheduled=sum(r['completeSystemScheduledAtPreviousClose'] for r in group)))
    return dict(passed=True,allSourceCyclesIncluded=134,wins=67,losses=67,groups=groups,rows=out,meaning='Counterfactual previous-close daily proxy coverage, not original intraday-intent classification; unmatched wins and losses are retained')


def fee(c,amount,side,day):
    stamp=c.get('stampDutyRate')
    if stamp is None:stamp=.0005 if day>='2023-08-28' else .001
    return max(c['minCommission'],amount*c['commissionRate'])+amount*c['transferFeeRate']+(amount*stamp if side=='sell' else 0)


def account_audit(r,data,system):
    config=r['config'];source={s['symbol']:s for s in data['securities']}
    wanted={t['symbol'] for t in r['trades']};bars={s:{b['date']:b for b in source[s]['bars']} for s in wanted}
    indices={d:i for i,d in enumerate(data['calendar'])};contexts={d['date']:d for d in system['daily']}
    opportunities={e['signalId']:e for e in r['research']['opportunities']};fills=defaultdict(list)
    for t in r['trades']:fills[t['date']].append(t)
    cash=config['initialCapital'];held={};marks={};closed=[];peak=cash
    for curve in r['curve']:
        day=curve['date']
        for t in fills[day]:
            s,q,price,side=t['symbol'],t['quantity'],t['price'],t['side'];b=bars[s][day]
            equal(price,b['open']*(1+(1 if side=='buy' else -1)*config['slippageBps']/10000),'Every raw opening fill')
            charge=fee(config,q*price,side,day);equal(t['fee'],charge,'Every independent fee')
            equal(t['amount'],q*price,'Fill notional');check(t['signalDate']<day,'No same-day close knowledge')
            if side=='buy':
                check(s not in held,'No additions');meta=system['metadata'][t['signalId']];op=opportunities[t['signalId']]
                equal(day,data['calendar'][indices[t['signalDate']]+1],'Single next-session entry')
                check(meta['failurePrice']<b['open']<=meta['signalClose']*(1+min(meta['meanNtr20'],.03)),'Opening guard')
                check(q<=op['plannedQuantity'] and q*price+charge<=op['budget']+1e-7,'Frozen reservation')
                check(q*(price-max(meta['failurePrice'],.95*price))<=op['stopRiskBudget']+1e-7,'Opening risk budget')
                old_total=0.;old_sector=0.;mapping=system['sectorMaps'][meta['snapshotId']]
                for sym,p in held.items():
                    raw=bars[sym].get(day,{}).get('open');mark=raw if raw is not None and raw>0 else marks[sym]
                    old_total+=p['quantity']*mark
                    if mapping.get(sym,p['sector'])==meta['sectorCode']:old_sector+=p['quantity']*mark
                check(old_total+q*price<=op['grossCap']*op['equityAtSignal']+1e-7,'Opening gross cap')
                check(old_sector+q*price<=.4*op['equityAtSignal']+1e-7,'Opening industry cap')
                cash-=q*price+charge
                held[s]=dict(quantity=q,cost=q*price+charge,invested=q*price+charge,realized=0.,entryDate=day,sector=meta['sectorCode'],pattern=meta['pattern'],signalId=t['signalId'])
                marks[s]=price
            else:
                check(s in held and held[s]['entryDate']<day,'Stock T+1')
                p=held[s];check(0<q<=p['quantity'],'Sell inventory')
                allocated=p['cost']*q/p['quantity'];pnl=q*price-charge-allocated
                equal(t['realizedPnl'],pnl,'FIFO cost and net realized PnL')
                cash+=q*price-charge;p['quantity']-=q;p['cost']-=allocated;p['realized']+=pnl
                if not p['quantity']:
                    closed.append(dict(symbol=s,entryDate=p['entryDate'],exitDate=day,pnlAmount=p['realized'],investedAmount=p['invested'],returnPct=p['realized']/p['invested']*100,holdingDays=indices[day]-indices[p['entryDate']]+1,pattern=p['pattern'],signalId=p['signalId'],exitReason=t['reason']))
                    del held[s]
            equal(t['quantityAfter'],held.get(s,{}).get('quantity',0),'Every fill ending quantity')
            equal(t['cashAfter'],cash,'Every sequential cash balance')
            check(cash>=-1e-7,'Unleveraged cash')
        for s in held:
            b=bars[s].get(day)
            if b is not None and b['volume']>0:marks[s]=b['close']
        mv=sum(p['quantity']*marks[s] for s,p in held.items());equity=cash+mv;peak=max(peak,equity)
        for k,v in dict(cash=cash,marketValue=mv,equity=equity,drawdownPct=(1-equity/peak)*100).items():equal(curve[k],v,'Every daily curve:'+k)
    equal(len(closed),len(r['closedTrades']),'All closed cycles')
    for a,b in zip(closed,r['closedTrades']):
        for k in b:equal(a[k],b[k],'Independent cycle:'+k)
    equal(len(held),len(r['positions']),'Open positions separate')
    return dict(passed=True,scope='Every executed fill, cash/share/cost/fee bridge and daily marked equity; every source candidate and schedule independently audited separately. Does not certify opening queue fills or unavailable source history.',dailyCurves=len(r['curve']),fills=len(r['trades']),closedCycles=closed,endingOpenPositions=len(held),feesRecomputed=True,tPlusOnePassed=True,allOpeningBudgetsAndCapsPassed=True)


def metrics(r):
    trades=r['closedTrades'];wins=[t for t in trades if t['pnlAmount']>0];losses=[t for t in trades if t['pnlAmount']<0]
    mw=mean([t['returnPct'] for t in wins]) if wins else None;ml=mean([t['returnPct'] for t in losses]) if losses else None
    gp=sum(t['pnlAmount'] for t in wins);gl=-sum(t['pnlAmount'] for t in losses)
    initial=r['config']['initialCapital'];curve=r['curve'];net=(curve[-1]['equity']/initial-1)*100
    bench=(curve[-1]['benchmarkEquity']/initial-1)*100 if curve[-1]['benchmarkEquity'] is not None else None
    q4=[c for c in curve if c['date']<='2024-12-31']
    return dict(closed=len(trades),wins=len(wins),losses=len(losses),winRatePct=len(wins)/len(trades)*100 if trades else None,meanWinReturnPct=mw,meanLossReturnPct=ml,
        payoffRatio=mw/-ml if mw is not None and ml is not None else None,profitFactor=gp/gl if gl else None,netReturnPct=net,maxDrawdownPct=max(c['drawdownPct'] for c in curve),
        benchmarkReturnPct=bench,excessReturnPct=net-bench if bench is not None else None,
        sourceOverlapReturnPct=(q4[-1]['equity']/initial-1)*100 if q4 else None,feesPaid=sum(t['fee'] for t in r['trades']),
        topFiveProfitShare=sum(sorted((t['pnlAmount'] for t in wins),reverse=True)[:5])/gp if gp else None,
        afterRemovingTopFiveProfitFactor=(gp-sum(sorted((t['pnlAmount'] for t in wins),reverse=True)[:5]))/gl if gl else None)
