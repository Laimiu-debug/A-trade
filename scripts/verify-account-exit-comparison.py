"""Independent registered shared-account arithmetic/source audit.

Never imports the new account engine, fee provider, metrics implementation or
runner. Frozen source loaders may be used for input I/O, not trade evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean, median, stdev
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
REGISTRATION=ROOT/'docs/account-exit-comparison-v1.json'
REGISTRATION_SHA256='1406190246a20ccb823b129f471bcd5c16ede59b78c56fe62947ff4c4f4cb21e'
DEFAULT_OUTPUT=ROOT/'artifacts/strategy-search/account-exit-comparison-v1'
VERIFIER_PATH=Path(__file__).resolve()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


VERIFIER_SHA256=sha(VERIFIER_PATH)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def parse(text):
    def unique(items):
        out={}
        for key,value in items:
            if key in out: raise ValueError('Duplicate JSON key: '+key)
            out[key]=value
        return out
    return json.loads(text,object_pairs_hook=unique,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON '+value)))


def read(path):
    path=Path(path)
    if path.suffix=='.gz':
        with gzip.open(path,'rt',encoding='utf-8') as stream: return parse(stream.read())
    return parse(path.read_bytes())


def records(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            row=parse(line)
            if not isinstance(row,dict): raise ValueError('Expected object JSONL row')
            yield row


class Checks:
    def __init__(self): self.n,self.failed,self.issues=0,0,[]
    def check(self,condition,name,**where):
        self.n+=1
        if not condition:
            self.failed+=1
            if len(self.issues)<50: self.issues.append({'check':name,**where})
        return bool(condition)
    def eq(self,actual,expected,name,**where):
        return self.check(type(actual) is type(expected) and actual==expected,name,**where)
    def near(self,actual,expected,name,**where):
        return self.check(type(actual) in (int,float) and math.isfinite(actual)
            and math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-7),name,**where)


def reference_fee(amount,side,day,config,includes=False):
    commission=max(config['minCommission'],amount*config['commissionRate'])
    transfer=0. if includes else amount*(.00002 if day<'2022-04-29' else .00001)
    rate=config.get('stampDutyRate')
    if rate is None: rate=.001 if day<'2023-08-28' else .0005
    stamp=amount*rate if side=='sell' else 0.
    return {'commission':commission,'transfer':transfer,'stampDuty':stamp,'total':commission+transfer+stamp}


def lot_quantity(notional,price,budget,config,day,*,includes=False,ceiling=None):
    upper=min(math.floor(notional/100)*100,ceiling if ceiling is not None else math.inf)
    quantity=max(0,math.floor(upper/100)*100)
    while quantity and quantity*price+reference_fee(quantity*price,'buy',day,config,includes)['total']>budget+1e-8:
        quantity-=100
    return quantity


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def compare(a,actual,expected,name,path=''):
    if isinstance(expected,dict):
        if not a.check(isinstance(actual,dict),name,path=path): return
        for key,value in expected.items():
            if a.check(key in actual,name+'-key',path=path+'.'+key):
                compare(a,actual[key],value,name,path+'.'+key)
    elif isinstance(expected,list):
        if not a.check(isinstance(actual,list),name,path=path): return
        a.eq(len(actual),len(expected),name+'-length',path=path)
        for i,(left,right) in enumerate(zip(actual,expected)): compare(a,left,right,name,path=f'{path}[{i}]')
    elif isinstance(expected,float): a.near(actual,expected,name,path=path)
    else: a.eq(actual,expected,name,path=path)


class AccountReference:
    """Separate daily state machine for the registered no-declared-actions scope.

    This reads source bars, not producer fills, to determine all order outcomes.
    It uses the registered conservative unknown-ST execution boundary. It does
    not turn that simulated rule or a source bar into broker-fill evidence.
    """
    def __init__(self,data,signals,config,weight,includes=False):
        self.data,self.c,self.weight,self.includes=data,config,weight,includes
        self.calendar=data['calendar']; self.ci={d:i for i,d in enumerate(self.calendar)}
        self.days=[d for d in self.calendar if config['startDate']<=d<=config['endDate']]
        self.sec={s['symbol']:s for s in data['securities']}
        if any(s.get('corporateActions') for s in self.sec.values()): raise ValueError('Declared actions require a separately reviewed accounting scope')
        self.bars={s:{b['date']:(i,b) for i,b in enumerate(v['bars'])} for s,v in self.sec.items()}
        self.ops=[{**r,'status':'notObserved','reason':None,'nextSession':None,'budget':None,
            'plannedQuantity':None,'reservePrice':None,'attemptDate':None,'filledQuantity':0,
            'unfilledQuantity':None,'fillId':None} for r in signals]
        self.byday=defaultdict(list)
        for op in self.ops: self.byday[op['signalDate']].append(op)
        self.positions={}; self.forced={}; self.quarantine={}; self.marks={}; self.markdates={}
        self.cash=float(config['initialCapital']); self.peak=self.cash; self.trades=[]; self.closed=[]; self.curve=[]
        self.daily=[]; self.reference_changes=[]; self.unaccounted=[]; self.capacity_issues=[]; self.unverified=[]

    def bar(self,s,d): return self.bars[s].get(d,(None,None))
    def member(self,s,d):
        if d in self.data.get('membershipByDate',{}): return s in self.data['membershipByDate'][d]
        q=self.sec[s]; return q.get('memberHistory',q.get('memberByDate',{})).get(d)
    def nominal(self,s,d):
        return .3 if s.startswith('bj') else .2 if s.startswith('sh68') or (s.startswith('sz30') and d>='2020-08-24') else .1
    def band(self,s,d):
        n=self.nominal(s,d)
        return n if n!=.1 or d>='2026-07-06' else .1 if self.sec[s].get('stByDate',{}).get(d) is False else .05
    def reference(self,s,i,d):
        return self.sec[s].get('precloseByDate',{}).get(d,self.sec[s]['bars'][i-1]['close'] if i else None)
    def roundqty(self,s,x):
        minimum,increment=(200,1) if s.startswith('sh68') else (100,1) if s.startswith('bj') else (100,100)
        q=max(0,math.floor(x/increment+1e-10)*increment)
        return q if q>=minimum else 0
    def affordable(self,s,ceiling,price,budget,day):
        q=self.roundqty(s,ceiling); inc=1 if s.startswith(('sh68','bj')) else 100
        while q and q*price+reference_fee(q*price,'buy',day,self.c,self.includes)['total']>budget+1e-8: q=self.roundqty(s,q-inc)
        return q
    def gate(self,s,d,side):
        q=self.sec[s]; i,b=self.bar(s,d)
        if s in self.quarantine: return 'rawPriceDiscontinuity'
        if q.get('delistDate') and d>=q['delistDate']: return 'delistedUnsettledPosition'
        if b is None: return 'missingBar'
        status=q.get('tradeStatusByDate',{}).get(d)
        if status in (False,0,'0') or b['volume']<=0: return 'suspended'
        if i<5: return 'insufficientListingObservations'
        previous=q['bars'][i-1]
        if self.ci[d]-self.ci[previous['date']]!=1 or previous['volume']<=0: return 'missingPreviousTradingBar'
        if side=='buy':
            if self.member(s,d) is False: return 'notHistoricalMember'
            if q.get('listingDate') and d<q['listingDate']: return 'notListed'
            if self.c['universe']['excludeSt'] and q.get('stByDate',{}).get(d) is True: return 'historicalST'
            if self.c['universe']['strictHistory'] and (status is None or d not in q.get('stByDate',{}) or self.member(s,d) is None or not q.get('listingDate')): return 'executionHistoryUnknown'
        ref=self.reference(s,i,d)
        if abs(b['open']/ref-1)>self.nominal(s,d)+.005:
            self.quarantine[s]=d; return 'rawPriceDiscontinuity'
        cent=lambda x:float(Decimal(str(x)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        upper,lower=cent(ref*(1+self.band(s,d))),cent(ref*(1-self.band(s,d)))
        if side=='buy' and b['open']>=upper-1e-8: return 'openAtUpperLimitOrUncertainST'
        if side=='sell' and b['open']<=lower+1e-8: return 'openAtLowerLimitOrUncertainST'
        price=b['open']*(1+self.c['slippageBps']/10000*(1 if side=='buy' else -1))
        if (side=='buy' and price>upper+1e-8) or (side=='sell' and price<lower-1e-8): return 'slippageBeyondLimitOrUncertainST'
        return None

    def order(self,s,d,side,request,origin,*,budget=None,op=None,reason=None):
        blocked=self.gate(s,d,side)
        if blocked: return blocked
        i,b=self.bar(s,d); sec=self.sec[s]
        price=b['open']*(1+self.c['slippageBps']/10000*(1 if side=='buy' else -1))
        cap=math.floor(sec['bars'][i-1]['volume']*self.c['maxParticipationRate'])
        ov=sec.get('openingVolumeByDate',{}).get(d)
        if ov is not None: cap=min(cap,math.floor(ov*self.c['maxParticipationRate']))
        position=self.positions.get(s); before=position['quantity'] if position else 0; pnl=None
        if side=='buy':
            if position is None and len(self.positions)>=self.c['maxPositions']: return 'positionSlotsOccupied'
            qty=self.affordable(s,min(request,cap),price,min(max(0.,self.cash),budget),d)
            if not qty: return 'cashOrPreviousVolumeBelowMinimum'
        else:
            if position['entryDate']>=d: return 'tPlusOne'
            desired=min(request,cap,before); qty=desired if desired==before else self.roundqty(s,desired)
            if not qty: return 'previousVolumeOrLotMinimum'
        fee=reference_fee(qty*price,side,d,self.c,self.includes)['total']
        if side=='sell' and self.cash+qty*price<fee: return 'insufficientSellFeeCash'
        if side=='buy':
            cost=qty*price+fee; self.cash-=cost
            if -1e-7<self.cash<0: self.cash=0.
            if position is not None: raise ValueError('Registered reference never adds to a position')
            position={'symbol':s,'quantity':qty,'costBasis':cost,'entryDate':d,'riskEntryPrice':price,
                'closePeak':price,'realizedPnl':0.,'investedAmount':cost}
            self.positions[s]=position; self.marks[s]=price; self.markdates[s]=d
        else:
            allocated=position['costBasis']*qty/before; pnl=qty*price-fee-allocated
            self.cash+=qty*price-fee; position['quantity']-=qty; position['costBasis']-=allocated; position['realizedPnl']+=pnl
            if position['quantity']==0:
                self.closed.append({'symbol':s,'entryDate':position['entryDate'],'exitDate':d,'pnlAmount':position['realizedPnl'],
                    'investedAmount':position['investedAmount'],'returnPct':position['realizedPnl']/position['investedAmount']*100,
                    'holdingDays':self.ci[d]-self.ci[position['entryDate']]+1})
                del self.positions[s]
        fill={'date':d,'signalDate':origin,'symbol':s,'side':side,'quantity':qty,'price':price,'openingPrice':b['open'],
            'amount':qty*price,'fee':fee,'slippage':qty*abs(price-b['open']),'cashAfter':self.cash,'realizedPnl':pnl,
            'reason':reason,'quantityBefore':before,'quantityAfter':self.positions.get(s,{}).get('quantity',0),
            'positionAction':'open' if side=='buy' else 'exit' if qty==before else 'reduce','unfilledQuantity':max(0,request-qty),
            'signalId':op['signalId'] if op else None,'originalRank':op['originalRank'] if op else None,
            'fillId':f'fill-{len(self.trades)+1:08d}'}
        if side=='sell': fill['riskEvidence']=deepcopy(self.forced[s]['riskEvidence'])
        self.trades.append(fill)
        return None

    def risk(self,s,d):
        p=self.positions[s]; q=self.sec[s]; _,b=self.bar(s,d); reason=None
        if self.member(s,d) is False: reason='baseEligibilityLost'
        elif q.get('delistDate') and d>=q['delistDate']: reason='delisted'
        elif self.c['universe']['excludeSt'] and q.get('stByDate',{}).get(d) is True: reason='historicalST'
        elif b is not None and b['volume']>0:
            p['closePeak']=max(p['closePeak'],b['close']); gain=b['close']/p['riskEntryPrice']-1
            if self.c['stopLossPct'] and gain<=-self.c['stopLossPct']: reason='stopLoss'
            elif self.c['trailingStopPct'] and b['close']/p['closePeak']-1<=-self.c['trailingStopPct']: reason='trailingStop'
            elif self.c['takeProfitPct'] and gain>=self.c['takeProfitPct']: reason='takeProfit'
            elif self.c['maxHoldingDays'] and self.ci[d]-self.ci[p['entryDate']]+1>=self.c['maxHoldingDays']: reason='maxHoldingDays'
        if reason and s not in self.forced:
            close=b['close'] if b else None
            self.forced[s]={'signalDate':d,'reason':reason,'riskEvidence':{'observedDate':d,'observedClose':close,
                'riskEntryPrice':p['riskEntryPrice'],'closePeak':p['closePeak'],'gainPct':close/p['riskEntryPrice']-1 if close is not None else None,
                'drawdownFromPeakPct':close/p['closePeak']-1 if close is not None else None,'holdingDays':self.ci[d]-self.ci[p['entryDate']]+1,
                **{k:self.c[k] for k in ('stopLossPct','takeProfitPct','trailingStopPct','maxHoldingDays')},'percentageUnit':'ratio'}}

    def run(self):
        pending=[]
        for d in self.days:
            before=len(self.trades)
            for s in self.sec:
                i,b=self.bar(s,d)
                if not i or b is None: continue
                previous=self.sec[s]['bars'][i-1]['close']; ref=self.sec[s].get('precloseByDate',{}).get(d)
                if ref is not None and abs(ref-previous)>max(.005,previous*1e-7):
                    event={'symbol':s,'date':d,'rawPreviousClose':previous,'preclose':ref}; self.reference_changes.append(event)
                    if s in self.positions:
                        p=self.positions[s]; self.unaccounted.append({**event,'quantity':p['quantity']})
                        p['riskEntryPrice']*=ref/previous; p['closePeak']*=ref/previous
            for s,order in sorted(list(self.forced.items())):
                if s not in self.positions: self.forced.pop(s,None)
                elif d>order['signalDate']:
                    self.order(s,d,'sell',self.positions[s]['quantity'],order['signalDate'],reason=order['reason'])
                    if s not in self.positions: self.forced.pop(s,None)
            sold={t['symbol'] for t in self.trades[before:] if t['side']=='sell'}
            attempts=[]
            for op in pending:
                s=op['symbol']; op['attemptDate']=d; attempts.append(op['signalId']); n=len(self.trades)
                blocked='alreadyHeldNoAdd' if s in self.positions else 'pendingExitOrSoldToday' if s in self.forced or s in sold else None
                if blocked is None: blocked=self.order(s,d,'buy',op['plannedQuantity'],op['signalDate'],budget=op['budget'],op=op,reason='fixedSignalEntry')
                if len(self.trades)>n:
                    fill=self.trades[-1]; full=fill['quantity']==op['plannedQuantity']
                    op.update(status='filled' if full else 'partial',reason='filled' if full else 'partialRemainderExpired',
                        filledQuantity=fill['quantity'],unfilledQuantity=op['plannedQuantity']-fill['quantity'],fillId=fill['fillId'])
                else: op.update(status='expired',reason=blocked or 'noFill')
            today=self.trades[before:]
            for s in sorted({t['symbol'] for t in today}):
                fills=[t for t in today if t['symbol']==s]; _,b=self.bar(s,d)
                volume=self.sec[s].get('openingVolumeByDate',{}).get(d); qty=sum(t['quantity'] for t in fills)
                invalid=qty>b['volume']+1e-8 or (volume is not None and volume>b['volume']+1e-8)
                if invalid: self.capacity_issues.append({'date':d,'symbol':s,'filledQuantity':qty,
                    'observedDailyVolume':b['volume'],'openingVolume':volume,'reason':'fills_or_opening_evidence_exceed_daily_volume'})
                if volume is None: self.unverified.append({'date':d,'symbol':s,'filledQuantity':qty})
                for t in fills: t.update(capacityBasis='openingVolumeEvidence' if volume is not None else 'previousVolumeApproximation',
                    openingCapacityVerified=volume is not None and not invalid,executionValid=not invalid)
            for s in self.sec:
                i,b=self.bar(s,d)
                if i and b is not None and b['volume']>0 and abs(b['close']/self.reference(s,i,d)-1)>self.nominal(s,d)+.005:
                    self.quarantine.setdefault(s,d)
            stale=[]; known_halts=[]
            for s in sorted(self.positions):
                _,b=self.bar(s,d); halted=b is not None and self.data.get('dataQuality',{}).get('certified') is True and self.sec[s].get('tradeStatusByDate',{}).get(d) in (False,0,'0')
                if b is not None and (b['volume']>0 or halted):
                    self.marks[s]=b['close']; self.markdates[s]=d
                    if halted and b['volume']==0: known_halts.append(s)
                else: stale.append(s)
            mv=sum(p['quantity']*self.marks[s] for s,p in self.positions.items()); equity=self.cash+mv
            prev=self.curve[-1]['equity'] if self.curve else self.c['initialCapital']; self.peak=max(self.peak,equity)
            self.curve.append({'date':d,'cash':self.cash,'marketValue':mv,'equity':equity,'dailyReturn':equity/prev-1 if prev else None,
                'drawdownPct':(1-equity/self.peak)*100 if self.peak else 0.,'staleSymbols':stale,'knownSuspendedSymbols':known_halts,
                'valuationReliable':not stale and not self.unaccounted and not self.capacity_issues and not any(s in self.quarantine for s in self.positions)})
            for s in sorted(self.positions): self.risk(s,d)
            free=max(0,self.c['maxPositions']-sum(s not in self.forced for s in self.positions)); pending=[]
            for op in self.byday[d]:
                s=op['symbol']; q=self.sec[s]; _,b=self.bar(s,d); status=q.get('tradeStatusByDate',{}).get(d)
                nexti=self.ci[d]+1; op['nextSession']=self.calendar[nexti] if nexti<len(self.calendar) else None
                reason='pendingForcedExit' if s in self.forced else 'alreadyHeldNoAdd' if s in self.positions else 'soldTodayOrQuarantined' if s in sold or s in self.quarantine else None
                if reason is None:
                    if self.member(s,d) is False: reason='notHistoricalMember'
                    elif q.get('delistDate') and d>=q['delistDate']: reason='delisted'
                    elif q.get('listingDate') and d<q['listingDate']: reason='notListed'
                    elif self.c['universe']['excludeSt'] and q.get('stByDate',{}).get(d) is True: reason='historicalST'
                    elif b is None or b['volume']<=0 or status in (False,0,'0'): reason='missingOrSuspendedSignalClose'
                    elif self.c['universe']['strictHistory'] and (status is None or d not in q.get('stByDate',{}) or self.member(s,d) is None or not q.get('listingDate')): reason='signalHistoryUnknown'
                if reason is None and free==0: reason='plannedSlotsOccupied'
                if reason: op.update(status='notPlanned',reason=reason); continue
                free-=1; budget=min(self.weight,self.c['maxStockWeight'])*equity; price=b['close']*(1+self.c['slippageBps']/10000)
                qty=self.affordable(s,budget/price,price,budget,d)
                op.update(budget=budget,reservePrice=price,plannedQuantity=qty,unfilledQuantity=qty)
                if op['nextSession'] is None or op['nextSession']>self.days[-1]: op.update(status='censored',reason='nextSessionOutsideStudy')
                elif qty==0: op.update(status='notPlanned',reason='budgetBelowLotMinimum')
                else: op.update(status='planned',reason='nextSessionOnly'); pending.append(op)
            holdings=[]
            for s,p in sorted(self.positions.items()):
                value=p['quantity']*self.marks[s]
                holdings.append({'symbol':s,'quantity':p['quantity'],'costBasis':p['costBasis'],'marketValue':value,
                    'weight':value/equity if equity else 0.,'markPrice':self.marks[s],'priceDate':self.markdates[s],
                    'costPrice':p['costBasis']/p['quantity'],'unrealizedPnl':value-p['costBasis'],
                    'unrealizedPnlPct':(value/p['costBasis']-1)*100 if p['costBasis'] else None,
                    'holdingDays':self.ci[d]-self.ci[p['entryDate']]+1})
            self.daily.append({'date':d,'buyAttemptOrder':attempts,'fixedSignalOpportunities':deepcopy(self.byday[d]),
                'cash':self.cash,'equity':equity,'holdings':holdings})
        return self


def audit_account(a,data,signals,result,checkpoint,config,weight,includes=False):
    ref=AccountReference(data,signals,config,weight,includes).run()
    compare(a,result['trades'],ref.trades,'independent-actual-trades')
    compare(a,result['closedTrades'],ref.closed,'independent-closed-episodes')
    compare(a,result['curve'],ref.curve,'independent-daily-equity')
    compare(a,result['daily'],ref.daily,'independent-opportunity-lifecycle')
    compare(a,result['research']['opportunities'],ref.ops,'independent-complete-opportunities')
    compare(a,result['forcedExits'],ref.forced,'independent-persistent-exits')
    a.eq(result['research']['opportunityCounts'],dict(Counter(o['status'] for o in ref.ops)),'opportunity-partition')
    positions=[{**p,'markPrice':ref.marks[s],'priceDate':ref.markdates[s],'marketValue':p['quantity']*ref.marks[s]} for s,p in sorted(ref.positions.items())]
    compare(a,result['positions'],positions,'independent-open-position-ledger')
    a.near(checkpoint['engineState']['cash'],ref.cash,'checkpoint-ending-cash')
    compare(a,checkpoint['engineState']['positions'],ref.positions,'checkpoint-position-ledger')
    compare(a,checkpoint['researchState']['opportunities'],ref.ops,'checkpoint-opportunity-ledger')
    a.eq(checkpoint['digest'],digest({k:v for k,v in checkpoint.items() if k!='digest'}),'account-checkpoint-digest')
    for key in ('curve','daily','trades','closedTrades','forcedExits','pendingTarget','actionLedger','cashReceivables','actionPayables','preExRights','reservedActionCash'):
        a.eq(checkpoint['engineState'][key],result[key],'result-checkpoint-exact',field=key)
    a.eq(result['research']['feeLedger'],checkpoint['feeState'],'result-fee-checkpoint-exact')
    compare(a,result['quality']['corporateActionDates'],ref.reference_changes,'observed-reference-changes')
    compare(a,result['quality']['unaccountedActionExposure'],ref.unaccounted,'unaccounted-rights-exposure')
    a.eq(result['quality']['quarantinedSymbols'],sorted(ref.quarantine),'price-quarantine')
    a.eq(result['quality']['executionCapacityIssues'],ref.capacity_issues,'capacity-consistency-issues')
    a.eq(result['quality']['unverifiedOpeningFills'],ref.unverified,'unverified-opening-evidence')
    a.eq(result['quality']['openingCapacityVerified'],not ref.capacity_issues and not ref.unverified,'opening-capacity-not-certified')
    a.eq(result['quality']['supplied'],data.get('dataQuality',{}),'input-data-quality-retained')
    for key in ('actionLedger','cashReceivables','actionPayables','preExRights'):
        a.check(not result[key],'no-declared-action-ledger',field=key)
    audit_fee_ledger(a,checkpoint,result,config,includes=includes)
    return ref


def audit_fee_ledger(a,checkpoint,result,config,*,includes=False):
    side=checkpoint['feeState']
    stripped={k:v for k,v in side.items() if k!='checkpointDigest'}
    a.eq(side.get('checkpointDigest'),digest(stripped),'fee-checkpoint-digest')
    quotes=side['quotes']; paid=side['paidLedger']; fills=result['trades']
    a.eq(len(paid),len(fills),'one-paid-record-per-fill')
    a.eq(len({p['quoteId'] for p in paid}),len(paid),'unique-paid-quote')
    a.eq(len({p['fillId'] for p in paid}),len(paid),'unique-fill-id')
    total=0.
    for i,q in enumerate(quotes):
        c=q['context']; day=c['effective_fee_date']
        a.eq(q['quoteId'],i,'quote-order')
        a.eq(c['symbol'],q['symbol'],'quote-symbol')
        expected=reference_fee(q['amount'],q['side'],day,config,includes)
        for key,value in expected.items(): a.near(q['breakdown'].get(key),value,'independent-quote-component',quote=i,field=key)
        if c['purpose']=='signalReserve':
            a.check(c['signal_date']==c['decision_date']==day and c['execution_date'] is None,'fee-reserve-known-date',quote=i)
        else:
            a.check(c['signal_date']<c['decision_date']==day,'fee-execution-clock',quote=i)
            a.eq(c['execution_date'],day if c['purpose']=='fillQuote' else None,'fee-execution-versus-quote',quote=i)
    annotations={'capacityBasis','openingCapacityVerified','executionValid'}
    for i,(row,fill) in enumerate(zip(paid,fills)):
        a.eq(row['tradeIndex'],i,'paid-order')
        q=quotes[row['quoteId']]
        a.eq(row['quote'],q,'paid-original-quote')
        a.eq({k:v for k,v in row['fill'].items() if k not in annotations},{k:v for k,v in fill.items() if k not in annotations},'paid-fill-core')
        c=q['context']
        a.eq(q['symbol'],fill['symbol'],'paid-quote-symbol')
        a.eq(q['side'],fill['side'],'paid-quote-side')
        a.near(q['amount'],fill['amount'],'paid-quote-amount')
        a.eq(q['market'],fill['symbol'][:2].upper(),'paid-quote-market')
        a.check(c['purpose']=='fillQuote' and c['execution_date']==fill['date'] and c['signal_date']==fill['signalDate'],'paid-fill-clock')
        a.eq(row['fillId'],fill['fillId'],'paid-fill-id')
        total+=q['breakdown']['total']
    a.near(side['paidFeeTotal'],total,'total-paid-fees')
    a.near(result['metrics']['totalFees'],total,'account-total-fees')


def stats(values):
    known=[v for v in values if v is not None]
    return {'count':len(values),'knownCount':len(known),'unknownCount':len(values)-len(known),
        'mean':math.fsum(known)/len(known) if known else None,'median':median(known) if known else None,
        'minimum':min(known) if known else None,'maximum':max(known) if known else None,
        'status':'empty' if not values else 'complete' if len(known)==len(values) else 'partialKnownValuesOnly'}


def quantiles(values):
    values=sorted(values); out={}
    for q in (0.,.05,.25,.5,.75,.95,1.):
        pos=(len(values)-1)*q; lo=math.floor(pos); hi=math.ceil(pos); fraction=pos-lo
        out[f'p{round(q*100):02d}']=math.fsum((values[lo]*(1-fraction),values[hi]*fraction)) if values else None
    return out


def closed_summary(rows):
    decorated=[]
    for i,r in enumerate(rows):
        pnl=r['pnlAmount']; value=pnl/r['investedAmount']*100 if r['investedAmount']>0 else None
        decorated.append({'inputIndex':i,'record':r,'netReturnPct':value,'pnlState':'win' if pnl>0 else 'loss' if pnl<0 else 'zero',
            'pnlAmount':pnl,'holdingSessions':float(r['holdingDays'])})
    decorated.sort(key=lambda r:(r['record']['exitDate'],r['record']['entryDate'],r['record']['symbol'],r['inputIndex']))
    returns=[r['netReturnPct'] for r in decorated]; known=[r for r in returns if r is not None]
    win=[r for r in known if r>0]; loss=[r for r in known if r<0]
    pnls=[r['pnlAmount'] for r in decorated]; wp=[p for p in pnls if p>0]; lp=[p for p in pnls if p<0]
    avg=lambda x:math.fsum(x)/len(x) if x else None
    med=lambda x:median(x) if x else None
    complete=len(known)==len(returns); streak=maximum=0
    for r in decorated:
        streak=streak+1 if r['pnlState']=='loss' else 0; maximum=max(maximum,streak)
    gp=math.fsum(wp); gl=-math.fsum(lp)
    payoff=avg(win)/-avg(loss) if win and loss and complete else None
    pf=gp/gl if gl>0 else None
    return {'count':len(rows),'wins':len(wp),'losses':len(lp),'zeros':sum(p==0 for p in pnls),'unknownPnlCount':0,
        'knownReturnCount':len(known),'unknownReturnCount':len(rows)-len(known),
        'status':'noClosedEpisodes' if not rows else 'complete' if complete else 'unknownValuesRetained',
        'netPnlAmount':math.fsum(pnls),'grossProfitAmount':gp,'grossLossAmount':gl,
        'winRatePct':len(wp)/len(rows)*100 if rows else None,'meanReturnPct':avg(known) if complete else None,
        'expectancyReturnPct':avg(known) if complete else None,'expectancyPnlAmount':avg(pnls),
        'medianReturnPct':med(known) if complete else None,'meanWinReturnPct':avg(win) if complete else None,
        'medianWinReturnPct':med(win) if complete else None,'meanLossReturnPct':avg(loss) if complete else None,
        'medianLossReturnPct':med(loss) if complete else None,'meanWinPnlAmount':avg(wp),'meanLossPnlAmount':avg(lp),
        'medianWinPnlAmount':med(wp),'medianLossPnlAmount':med(lp),'payoffRatio':payoff,'profitFactor':pf,
        'returnQuantilesPct':quantiles(known if complete else []),'maxConsecutiveLosses':maximum if rows else None,
        'observedConsecutiveLossesLowerBound':maximum if rows else None,'holdingSessions':stats([r['holdingSessions'] for r in decorated]),
        'metricStatus':{'payoffRatio':'known' if payoff is not None else 'unknownReturns' if not complete else 'noWinsOrLosses',
            'profitFactor':'known' if pf is not None else 'noLossDenominator','winRatePct':'known' if rows else 'noClosedEpisodes'},
        'secondaryClosedNet30':{'hits':sum(r>=30 for r in known),'denominator':len(rows),'unknownCount':len(rows)-len(known),
            'ratePct':sum(r>=30 for r in known)/len(rows)*100 if rows and complete else None},'episodes':decorated}


def audit_summary(a,summary,result,checkpoint,policy):
    a.eq(summary['schema'],'research-account-outcomes-v1','summary-schema')
    a.eq(summary['inputResultSha256'],digest(result),'summary-result-identity')
    a.eq(summary['policyId'],policy['id'],'summary-policy')
    a.eq(summary['policyLabel'],policy['title'],'summary-label')
    for k in ('certified','targetAchieved'): a.eq(summary[k],False,'summary-no-certification',field=k)
    curve=result['curve']; fills=result['trades']; closed=closed_summary(result['closedTrades']); initial=result['config']['initialCapital']
    end=curve[-1] if curve else {'equity':initial,'cash':initial,'marketValue':0.}
    compare(a,summary['closedEpisodes'],closed,'independent-closed-distribution')
    compare(a,summary['account'],{'status':result['status'],'configuredStart':result['config']['startDate'],
        'configuredEnd':result['config']['endDate'],'observedStart':curve[0]['date'] if curve else None,'cutoff':curve[-1]['date'] if curve else None,
        'initialCapital':initial,'finalEquity':end['equity'],'finalCash':end['cash'],'finalMarketValue':end['marketValue'],
        'netPnlAmount':math.fsum((end['equity'],-initial)),'netReturnPct':(end['equity']-initial)/initial*100,
        'maxDrawdownPct':max((r['drawdownPct'] for r in curve),default=None),'fillCount':len(fills),
        'buyFillCount':sum(t['side']=='buy' for t in fills),'sellFillCount':sum(t['side']=='sell' for t in fills),
        'feesPaid':math.fsum(t['fee'] for t in fills),'slippageAlreadyInFillPrices':math.fsum(t['slippage'] for t in fills),
        'grossTradedAmount':math.fsum(t['amount'] for t in fills),'turnoverPct':math.fsum(t['amount'] for t in fills)/initial*100,
        'noForcedLiquidation':True},'independent-account-metrics')
    specs=[('overall',result['config']['startDate'],result['config']['endDate'],'overall')]
    for year in range(int(result['config']['startDate'][:4]),int(result['config']['endDate'][:4])+1):
        specs.extend(((str(year),f'{year}-01-01',f'{year}-12-31','calendarYear'),
            (f'{year}-H1',f'{year}-01-01',f'{year}-06-30','calendarHalfYear'),
            (f'{year}-H2',f'{year}-07-01',f'{year}-12-31','calendarHalfYear')))
    a.eq(len(summary['periods']),len(specs),'complete-continuous-window-set')
    daily={r['date']:r for r in result['daily']}
    for actual,(ident,start,finish,kind) in zip(summary['periods'],specs):
        rows=[r for r in curve if start<=r['date']<=finish]; prior=[r for r in curve if r['date']<start]
        base=prior[-1]['equity'] if prior else initial; ending=rows[-1]['equity'] if rows else None
        peak=base; dd=0.
        for r in rows: peak=max(peak,r['equity']); dd=max(dd,(peak-r['equity'])/peak*100)
        unreliable=[r['date'] for r in rows if r['valuationReliable'] is not True]
        expected={'id':ident,'kind':kind,'requestedStart':start,'requestedEnd':finish,'observedStart':rows[0]['date'] if rows else None,
            'observedEnd':rows[-1]['date'] if rows else None,'observedSessions':len(rows),'enteringEquity':base,
            'enteringEquityDate':prior[-1]['date'] if prior else None,'enteringEquityBasis':'previousObservedClose' if prior else 'initialCapital',
            'endingEquity':ending,'netPnlAmount':ending-base if rows else None,'returnPct':(ending-base)/base*100 if rows else None,
            'maxDrawdownPct':dd if rows else None,'status':'noObservations' if not rows else 'observedValues;valuationUnverified' if unreliable else 'observedValues',
            'valuationUnreliableDates':unreliable,'withinConfiguredInterval':result['config']['startDate']<=start and finish<=result['config']['endDate'],
            'calendarCompletenessProven':False,'investedSessions':sum(r['marketValue']>0 for r in rows),'unknownInvestmentSessions':0,
            'investedSessionPct':sum(r['marketValue']>0 for r in rows)/len(rows)*100 if rows else None,
            'exposurePct':stats([r['marketValue']/r['equity']*100 if r['equity']>0 else None for r in rows]),
            'heldSessionAge':stats([float(p['holdingDays']) for r in rows for p in daily[r['date']]['holdings']]),
            'missingDailyHoldingsDates':[],'closedEpisodes':closed_summary([r for r in result['closedTrades'] if start<=r['exitDate']<=finish])}
        compare(a,actual,expected,'independent-continuous-period',ident)
    positive=sorted([r for r in closed['episodes'] if r['pnlAmount']>0],key=lambda r:(-r['pnlAmount'],r['record']['exitDate'],r['record']['entryDate'],r['record']['symbol']))
    top=positive[:5]; removed={r['inputIndex'] for r in top}; rest=[r for r in closed['episodes'] if r['inputIndex'] not in removed]; gp=closed['grossProfitAmount']
    compare(a,summary['concentration'],{'grossProfitAmount':gp,'positiveEpisodeCount':len(positive),
        'top1ProfitSharePct':sum(r['pnlAmount'] for r in positive[:1])/gp*100 if gp>0 else None,
        'top5ProfitSharePct':math.fsum(r['pnlAmount'] for r in top)/gp*100 if gp>0 else None,
        'status':'known' if positive else 'noPositiveProfitDenominator',
        'top5':[{'symbol':r['record']['symbol'],'entryDate':r['record']['entryDate'],'exitDate':r['record']['exitDate'],
            'pnlAmount':r['pnlAmount'],'netReturnPct':r['netReturnPct']} for r in top],
        'excludingTop5PositiveEpisodes':{'removedCount':len(top),'remainingCount':len(rest),'netPnlAmount':math.fsum(r['pnlAmount'] for r in rest),
            'meanReturnPct':math.fsum(r['netReturnPct'] for r in rest)/len(rest) if rest else None}},'independent-concentration')
    openrows=[]
    for p in result['positions']:
        age=sum(p['entryDate']<=r['date']<=curve[-1]['date'] for r in curve)
        openrows.append({'record':p,'marketValue':p['marketValue'],'unrealizedPnlAmount':math.fsum((p['marketValue'],-p['costBasis'])),
            'realizedPnlWhileOpen':p['realizedPnl'],'observedHoldingSessions':age,'holdingAgeStatus':'observedCurveSessions',
            'markStaleAtCutoff':p['priceDate']!=curve[-1]['date']})
    compare(a,summary['openPositions'],{'count':len(openrows),'positions':openrows,'holdingSessions':stats([r['observedHoldingSessions'] for r in openrows]),
        'excludedFromClosedEpisodeDenominator':True},'independent-unclosed-ledger')
    a.eq(summary['sourceQuality'],result['quality'],'source-quality-preserved')
    a.eq(summary['warnings'],result['warnings'],'source-warnings-preserved')
    a.eq(summary['opportunityDispositions'],{k:result['research'][k] for k in ('opportunityCounts','opportunities')},'summary-opportunity-denominator')
    compare(a,summary['unresolvedExits'],{'count':len(result['forcedExits']),'orders':result['forcedExits'],'pendingTarget':result['pendingTarget']},'summary-unresolved-exits')
    flows=math.fsum((t['amount'] if t['side']=='sell' else -t['amount'])-t['fee'] for t in fills)
    realized=math.fsum(p['realizedPnl'] for p in result['positions']); floating=math.fsum(p['marketValue']-p['costBasis'] for p in result['positions'])
    def reconcile(actual,expected):
        residual=math.fsum((actual,-expected)); same=math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-7)
        return {'observed':actual,'expected':expected,'residual':residual,'status':'reconciled' if same else 'mismatch'}
    compare(a,summary['reconciliation'],{'cashFlow':reconcile(end['cash'],math.fsum((initial,flows,0.))),
        'balanceSheet':reconcile(end['equity'],math.fsum((end['cash'],end['marketValue'],0.))),
        'positionMarketValue':reconcile(end['marketValue'],math.fsum(p['marketValue'] for p in result['positions'])),
        'episodePnlBridge':reconcile(end['equity']-initial,math.fsum((closed['netPnlAmount'],realized,floating))),
        'tradeNetCashFlow':flows,'actionNetCashFlow':0.,'closedEpisodePnl':closed['netPnlAmount'],
        'openPositionRealizedPnl':realized,'openPositionUnrealizedPnl':floating,'unknownActionStages':[],
        'paidFillCountMatches':True},'independent-account-reconciliation')
    a.check(not summary['quality']['numericUnknownPaths'] and not summary['quality']['integrityIssues'],'summary-numeric-and-integrity-account')


POLICIES=('hold','trail10','stop5trail10')
SOURCE_ID='control_rawEarlyStrengthEmergence'
VERIFIER_REL='scripts/verify-account-exit-comparison.py'


def local(root,name):
    path=(root/name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink(): raise ValueError('Invalid bound repository file: '+name)
    return path


def import_source(root,ref):
    path=local(root,ref['path'])
    if sha(path)!=ref['sha256']: raise ValueError('Frozen raw/metadata loader SHA differs')
    spec=importlib.util.spec_from_file_location('_account_exit_independent_frozen_io',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def archive_check(a,path,implementation,root,label):
    with zipfile.ZipFile(path) as z:
        a.eq(len(z.namelist()),len(implementation),label+'-member-count')
        a.eq(set(z.namelist()),set(implementation),label+'-exact-members')
        for name,expected in implementation.items():
            a.eq(hashlib.sha256(z.read(name)).hexdigest(),expected,label+'-member-sha',file=name)
            a.eq(sha(local(root,name)),expected,label+'-current-sha',file=name)


def calendar_evidence(a,parent,root,files):
    metadata=parent['manifest']['inputs']['calendar']; spec=parent['manifest']['registration']['calendar']
    a.eq(metadata['originalRestrictedDatesEqualSavedExchangeDates'],True,'independent-calendar-earlier-equality')
    a.eq(metadata['savedProvider'],spec,'independent-calendar-bound-metadata')
    compare(a,spec,{'provider':'baostock','api':'query_trade_dates','start':parent['sample']['warmupStart'],
        'end':parent['sample']['signalEnd']},'independent-calendar-source-range')
    path=local(root,spec['path']); a.eq(sha(path),spec['sha256'],'independent-calendar-source-sha')
    payload=read(path)
    compare(a,payload,{'provider':'baostock','api':'query_trade_dates','errorCode':'0','rowBoundReached':False,
        'parameters':{'start_date':spec['start'],'end_date':spec['end']},'fields':['calendar_date','is_trading_day']},'complete-saved-provider-calendar')
    begin=date.fromisoformat(spec['start']); end=date.fromisoformat(spec['end'])
    natural=[(begin+timedelta(days=i)).isoformat() for i in range((end-begin).days+1)]
    rows=payload['rows']; a.eq(len(rows),len(natural),'calendar-all-natural-days')
    actual=[]
    for day,row in zip(natural,rows):
        a.check(isinstance(row,list) and len(row)==2,'calendar-row-shape')
        a.eq(row[0],day,'calendar-date-no-gap-duplicate'); a.check(row[1] in ('0','1'),'calendar-flag-domain')
        if row[1]=='1': actual.append(row[0])
    a.eq(actual,parent['calendar'],'independent-calendar-exact-frozen-dates')
    a.eq(digest(actual),parent['identity']['calendarHash'],'independent-calendar-digest')
    files[str(path)]=spec['sha256']
    return {'path':spec['path'],'sha256':spec['sha256'],'provider':'baostock','api':'query_trade_dates',
        'start':spec['start'],'end':spec['end'],'calendarHash':digest(actual),'originalSource':metadata['originalSource'],
        'originalRestrictedDatesEqualSavedExchangeDates':True}


def prepare(a,output,root):
    doc=read(REGISTRATION); a.eq(sha(REGISTRATION),REGISTRATION_SHA256,'frozen-registration-sha')
    p=read(output/'protocol.json'); c=read(output/'attempts/attempt-0001/completion.json')
    a.eq(p['schema'],'account-exit-comparison-protocol-v1','protocol-schema')
    a.eq(p['protocolHash'],digest({k:v for k,v in p.items() if k!='protocolHash'}),'protocol-digest')
    a.eq(p['registration'],doc,'protocol-exact-registration'); a.eq(p['registrationSha256'],REGISTRATION_SHA256,'protocol-registration-sha')
    a.eq(p['policyOrder'],list(POLICIES),'fixed-policy-order'); a.eq(p['sourceStrategyId'],SOURCE_ID,'source-control-id')
    for obj in (doc,p,c):
        for k in ('certified','targetAchieved'): a.eq(obj[k],False,'uncertified-flags',field=k)
    io=import_source(root,doc['frozenSourceRunner']); parent=io.load_binding()
    a.eq(doc['sourceBinding'],parent['document']['sourceBinding'],'exact-frozen-panel-source')
    a.eq(doc['sourceRegistration']['sha256'],parent['identity']['registrationSha256'],'source-registration')
    sample=doc['sample']; dates=parent['dates']; symbols=parent['symbols']
    compare(a,sample,{'startDate':dates[0],'endDate':dates[-1],'sourceSignalStart':parent['sample']['signalStart'],
        'sourceSignalEnd':parent['sample']['signalEnd'],'warmupStart':parent['sample']['warmupStart'],
        'expectedSecurities':len(symbols),'expectedStudySessions':len(dates),'expectedSourceCalendarSessions':len(parent['calendar']),
        'snapshotFingerprint':parent['identity']['snapshotFingerprint'],'fullDatasetContentSha256':parent['identity']['fullDatasetContentSha256'],
        'expectedBars':parent['manifest']['inputs']['retainedBarCount']},'whole-frozen-population')
    source=root/doc['sourceStudy']['directory']; sourcep=read(source/'protocol.json'); sourcec=read(source/'attempts/attempt-0001/completion.json')
    sourceaudit=read(source/'independent-result-verification.json'); files=dict(parent['files'])
    for name,h in doc['sourceStudy']['files'].items():
        path=(source/name).resolve(); a.eq(sha(path),h,'source-study-sha',file=name); files[str(path)]=h
    a.eq(sourcep['protocolHash'],doc['sourceStudy']['protocolHash'],'inherited-protocol-identity')
    a.eq(sourcep['protocolHash'],digest({k:v for k,v in sourcep.items() if k!='protocolHash'}),'inherited-protocol-digest')
    a.eq(sourcep['binding'],parent['identity'],'inherited-binding')
    a.eq(sourcep['registration'],parent['document'],'inherited-registration')
    compare(a,sourceaudit,{'passed':True,'verificationCompleted':True,'failureCount':0,'protocolHash':sourcep['protocolHash'],
        'completionSha256':sha(source/'attempts/attempt-0001/completion.json'),'registrationSha256':doc['sourceRegistration']['sha256'],
        'snapshotFingerprint':sample['snapshotFingerprint'],'targetSessions':len(dates),'implementation':sourcep['implementation'],
        'certified':False,'targetAchieved':False},'prior-actual-independent-audit')
    a.eq(sha(source/'execution-sources.zip'),sourcep['executionArchiveSha256'],'source-execution-archive-sha')
    archive_check(a,source/'execution-sources.zip',sourcep['implementation'],root,'source-archive')
    files[str((source/'execution-sources.zip').resolve())]=sourcep['executionArchiveSha256']
    for key,name in (('signals','signals.jsonl.gz'),('selections','selection.jsonl.gz')):
        name='attempts/attempt-0001/'+name
        a.eq(sourcec['files'][key],{'path':name,'sha256':doc['sourceStudy']['files'][name]},'source-selected-receipt',file=key)
    for name,h in {**sourcep['implementation'],**doc['frozenImplementation'],**doc['engineeringEvidence']}.items():
        path=local(root,name); a.eq(sha(path),h,'frozen-method-sha',file=name); files[str(path)]=h
    files[str(REGISTRATION.resolve())]=REGISTRATION_SHA256
    evidence=calendar_evidence(a,parent,root,files)
    identity={'registrationSha256':REGISTRATION_SHA256,'inputFilesSha256':files,'sourceStudyProtocolHash':sourcep['protocolHash'],
        'sourceStudyAuditSha256':sha(source/'independent-result-verification.json'),'sourceStudyCompletionSha256':sha(source/'attempts/attempt-0001/completion.json'),
        'snapshotFingerprint':sample['snapshotFingerprint'],'fullDatasetContentSha256':sample['fullDatasetContentSha256'],
        'symbols':symbols,'symbolsSha256':parent['identity']['symbolsSha256'],'calendar':parent['calendar'],
        'calendarHash':parent['identity']['calendarHash'],'targetDates':dates,'sourcePanelManifestSha256':parent['identity']['panelManifestSha256'],
        'calendarEvidence':evidence}
    a.eq(p['binding'],identity,'complete-source-identity')
    expected_impl={**sourcep['implementation'],**doc['frozenImplementation'],**{n:sha(local(root,n)) for n in doc['reviewedFiles']}}
    a.eq(p['implementation'],expected_impl,'exact-execution-closure')
    a.eq(p['implementation'].get(VERIFIER_REL),VERIFIER_SHA256,'self-in-frozen-execution-closure')
    a.eq(sha(output/'execution-sources.zip'),p['executionArchiveSha256'],'execution-archive-whole-sha')
    archive_check(a,output/'execution-sources.zip',p['implementation'],root,'execution-archive')
    reviewfiles={n:sha(local(root,n)) for n in doc['reviewedFiles']}
    for key,name in doc['runPrerequisites'].items():
        a.eq(p['prerequisites'][key],{'path':name,'sha256':sha(local(root,name))},'prerun-evidence-sha',kind=key)
        evidence=read(local(root,name)); a.eq(evidence['passed'],True,'prerun-passed',kind=key); a.eq(evidence['files'],reviewfiles,'prerun-current-files',kind=key)
        if key=='scopedTests':
            for k in ('failures','errors','skipped'): a.eq(evidence[k],0,'scoped-tests-zero-failures',field=k)
            a.eq(len(set(evidence['uniqueTestIds'])),evidence['testsRun'],'scoped-unique-tests')
        else:
            a.eq(evidence['failureCount'],0,'prerun-no-findings')
            a.eq(evidence['scopedTestsSha256'],p['prerequisites']['scopedTests']['sha256'],'review-scoped-binding')
    marker=read(output/'freeze-attempt.json'); a.eq(sha(output/'freeze-attempt.json'),p['freezeAttemptSha256'],'exclusive-freeze-marker')
    a.eq(marker['registrationSha256'],REGISTRATION_SHA256,'freeze-registration')
    compare(a,c,{'schema':'account-exit-comparison-completion-v1','attempt':1,'protocolHash':p['protocolHash'],
        'policyOrder':list(POLICIES),'snapshotFingerprint':sample['snapshotFingerprint'],'signalScheduleSha256':p['signalScheduleSha256'],
        'freezeAttemptSha256':p['freezeAttemptSha256'],'declaredActionInstructionsCount':0,'actualCorporateActionsKnown':False},'completion-identity')
    paths={'opportunities':'opportunities.jsonl.gz','result':'result.json.gz','summary':'summary.json','attempt':'attempt.json'}
    for pid in POLICIES:
        for k,suffix in (('Result','result.json.gz'),('Checkpoint','checkpoint.json.gz'),('Summary','summary.json')): paths[pid+k]=pid+'.'+suffix
    a.eq(set(c['files']),set(paths)|{'protocol','executionSources'},'exact-receipt-closure')
    for k,name in {**{k:'attempts/attempt-0001/'+n for k,n in paths.items()},'protocol':'protocol.json','executionSources':'execution-sources.zip'}.items():
        a.eq(c['files'][k],{'path':name,'sha256':sha(output/name)},'receipt-file-sha',file=k)
    attempt=output/'attempts/attempt-0001'
    a.eq({x.name for x in attempt.iterdir()},set(paths.values())|{'completion.json'},'attempt-exact-tree')
    a.eq({x.name for x in (output/'attempts').iterdir()},{'attempt-0001'},'single-attempt')
    allowed={'freeze-attempt.json','protocol.json','execution-sources.zip','events.jsonl','attempts','.run.lock'}
    extras={x.name for x in output.iterdir()}-allowed
    a.check(all(x=='independent-result-verification.json' or (x.startswith('independent-result-verification-failed-') and x.endswith('.json')) for x in extras),'root-exact-tree')
    a.check(all(not x.is_symlink() for x in output.rglob('*')),'output-no-symbolic-links')
    journal=[parse(line) for line in (output/'events.jsonl').read_text(encoding='utf8').splitlines()]
    a.eq([r['event'] for r in journal],['study_started','opportunities_verified','study_succeeded'],'complete-journal')
    a.check(all(r['protocolHash']==p['protocolHash'] for r in journal),'journal-protocol')
    a.eq(journal[-1]['receipt'],c,'journal-final-receipt')
    return doc,p,c,parent,io,source,files


def audit_opportunities(a,output,doc,p,parent,source):
    emitted=iter(records(source/'attempts/attempt-0001/signals.jsonl.gz'))
    selected=iter(records(source/'attempts/attempt-0001/selection.jsonl.gz'))
    observed=iter(records(output/'attempts/attempt-0001/opportunities.jsonl.gz'))
    schedule=[]; lineage=[]; counts=Counter(); mask=Counter(); daycounts={}
    groups=(parent['document']['candidateId'],SOURCE_ID)
    for index,day in enumerate(parent['dates']):
        wrapper=next(selected,None)
        if not isinstance(wrapper,dict): raise ValueError('Source selections ended early')
        a.eq(wrapper['date'],day,'source-complete-date-order')
        a.eq(wrapper['source'],{'panelManifestSha256':parent['identity']['panelManifestSha256'],
            'featureFileSha256':doc['sourceBinding']['files']['features.jsonl.gz'],'calendarHash':parent['identity']['calendarHash'],'targetIndex':index},'selection-full-source-identity')
        sel=wrapper['selection']; slots=sel['slots']; ranked=sel['fullRanking']
        a.eq(len(slots),min(5,len(ranked)),'complete-preactive-top5')
        for rank,row in enumerate(ranked,1):
            a.eq(row['rank'],rank,'original-ranking-order'); a.eq(row['selectedTop5'],rank<=5,'original-top5-marker')
        a.eq(len({r['symbol'] for r in ranked}),len(ranked),'unique-ranked-symbols')
        for rank,slot in enumerate(slots):
            a.eq({k:slot[k] for k in ranked[rank]},ranked[rank],'slot-original-fullranking')
            a.eq(slot['baselineAdmitted'],True,'all-common-slots-baseline')
        expected=[]
        for group in groups:
            for slot in slots:
                if not slot['candidateAdmitted' if group==groups[0] else 'baselineAdmitted']: continue
                expected.append({**slot,'version':parent['document']['featureVersion'],'strategyId':group,
                    'pairedStrategyId':groups[1] if group==groups[0] else groups[0],'signalDate':day,'eligible':True,'certified':False,'targetAchieved':False})
        a.eq(sel['signals'],expected,'complete-preactive-stream-not-old-active')
        a.eq(sel['policyCounts'],{g:sum(r['strategyId']==g for r in expected) for g in groups},'source-policy-counts')
        for row in expected:
            a.eq(next(emitted,None),row,'source-signal-order-eof'); counts[row['strategyId']]+=1
            if row['strategyId']!=SOURCE_ID: continue
            signal={'signalId':SOURCE_ID+':'+row['episodeId']+':'+day,'strategyId':SOURCE_ID,'symbol':row['symbol'],'signalDate':day,'originalRank':row['rank']}
            item={'source':row,'projected':signal,'sourceSignalSha256':digest(row),'sourceSelectionSha256':digest(wrapper)}
            a.eq(next(observed,None),item,'independent-full-opportunity-lineage')
            schedule.append(signal); lineage.append(item); mask[row['earlyMask']]+=1
        daycounts[day]=len(slots)
    a.eq(next(selected,None),None,'source-selection-eof'); a.eq(next(emitted,None),None,'source-signal-eof'); a.eq(next(observed,None),None,'opportunity-eof')
    a.eq(schedule,sorted(schedule,key=lambda r:(r['signalDate'],r['originalRank'],r['symbol'])),'signal-schedule-order')
    a.eq(len({s['signalId'] for s in schedule}),len(schedule),'unique-opportunity-id')
    a.eq(len(schedule),doc['sourceStudy']['expectedSelectedOpportunities'],'registered-whole-input-count')
    accounting={'targetSessions':len(parent['dates']),'sourceSignalsByPolicy':dict(counts),'opportunityCount':len(schedule),
        'opportunitiesByDate':daycounts,'earlyMaskCounts':{k:mask[k] for k in ('true','false','unknown')},
        'signalScheduleSha256':digest(schedule),'sourceLineageSha256':digest(lineage),
        'sourceStreamMeaning':'complete-pre-active-control-Top5;includes-all-earlyMask-states;not-prior-filled-subset'}
    a.eq(p['sourceAccounting'],accounting,'source-complete-accounting'); a.eq(p['signalScheduleSha256'],digest(schedule),'frozen-schedule-sha')
    return schedule,accounting


def verify(output,root=ROOT,dataset=None):
    start=time.perf_counter(); a=Checks(); output=Path(output).resolve(); root=Path(root).resolve()
    result_sha=None; protocol_hash=None; sources={}; files={}; nsignals=None; completion_sha=None
    try:
        a.eq(sha(VERIFIER_PATH),VERIFIER_SHA256,'verifier-start-sha')
        doc,p,c,parent,io,source,inputs=prepare(a,output,root)
        completion_sha=sha(output/'attempts/attempt-0001/completion.json')
        protocol_hash=p['protocolHash']; sources=dict(p['implementation']); files={k:v['sha256'] for k,v in c['files'].items()}
        result_sha=files['result']
        if a.failed: raise ValueError('Source closure failed before account replay')
        signals,accounting=audit_opportunities(a,output,doc,p,parent,source); nsignals=len(signals)
        if dataset is None: dataset=io.load_dataset(argparse.Namespace(workspace=parent['workspace']),parent)
        io.validate_dataset(dataset,parent)
        data_digest=digest(dataset)
        a.eq(data_digest,doc['sample']['fullDatasetContentSha256'],'independent-full-dataset-sha')
        a.eq(dataset['calendar'],parent['calendar'],'independent-exact-calendar')
        a.eq([s['symbol'] for s in dataset['securities']],parent['symbols'],'independent-exact-population')
        a.check(not any(s.get('corporateActions') for s in dataset['securities']),'no-declared-actions-supported-scope')
        evidence=p['binding']['calendarEvidence']
        a.eq(dataset.get('calendarSource'),evidence['originalSource'],'calendar-original-source-retained')
        account_identity={'sourceDatasetDigest':data_digest,'sourceSnapshotFingerprint':dataset['fingerprint'],'calendarEvidence':evidence}
        account_fp=digest({'kind':'fixed-signal-account-calendar-view-v1',**account_identity})
        view={**dataset,'calendarSource':'independently-verified-saved-trading-calendar','calendarEvidence':evidence,
            'sourceDatasetDigest':data_digest,'sourceDatasetFingerprint':dataset['fingerprint'],'fingerprint':account_fp}
        account_digest=digest(view)
        account_identity.update(accountDatasetDigest=account_digest,accountSnapshotFingerprint=account_fp)
        a.eq(p['accountDataset'],account_identity,'independent-frozen-account-view')
        a.eq(c['accountDataset'],account_identity,'completion-account-view')
        if a.failed: raise ValueError('Input schedule/dataset rejected before ledger replay')
        attempt=output/'attempts/attempt-0001'; result=read(attempt/'result.json.gz'); summary=read(attempt/'summary.json')
        a.eq({k:v for k,v in summary.items() if k!='performance'},result,'aggregate-summary-result-equality')
        a.eq(result['accountDataset'],account_identity,'result-account-view')
        compare(a,result,{'schema':'account-exit-comparison-result-v1','studyId':doc['studyId'],'protocolHash':p['protocolHash'],
            'registrationSha256':REGISTRATION_SHA256,'snapshotFingerprint':dataset['fingerprint'],'signalScheduleSha256':digest(signals),
            'sourceAccounting':accounting,'policyOrder':list(POLICIES),'declaredActionInstructionsCount':0,'actualCorporateActionsKnown':False,
            'qualityLimits':doc['qualityLimits'],'automaticWinnerSelection':False,'certified':False,'targetAchieved':False},'aggregate-identity')
        a.eq(len(result['policies']),3,'three-policy-results')
        for policy,policyresult in zip(doc['policies'],result['policies']):
            ident=policy['id']; print(json.dumps({'phase':'independent-account-audit','policy':ident}),flush=True)
            full=read(attempt/(ident+'.result.json.gz')); state=read(attempt/(ident+'.checkpoint.json.gz')); metric=read(attempt/(ident+'.summary.json'))
            a.eq(policyresult,{'policyId':ident,'title':policy['title'],'role':policy['role'],'config':policy['config'],
                'files':{'result':ident+'.result.json.gz','checkpoint':ident+'.checkpoint.json.gz','summary':ident+'.summary.json'},'outcomes':metric},'aggregate-policy-link')
            a.eq(full['config'],policy['config'],'exact-policy-config',policy=ident)
            a.eq(full['status'],'completed','completed-policy',policy=ident)
            a.eq(full['dataDigest'],account_digest,'account-full-data-binding',policy=ident)
            a.eq(full['fingerprint'],account_fp,'account-view-fingerprint',policy=ident)
            a.eq(full['research']['identity']['signalSchedule'],signals,'account-full-schedule',policy=ident)
            a.eq(full['research']['identity'],state['identity'],'checkpoint-result-identity',policy=ident)
            for key in ('certified','targetAchieved'): a.eq(full[key],False,'policy-uncertified',policy=ident,field=key)
            audit_account(a,view,signals,full,state,policy['config'],doc['account']['entryWeight'],doc['feePolicy']['commissionIncludesTransfer'])
            audit_summary(a,metric,full,state,policy)
        a.eq(digest(dataset),data_digest,'dataset-unchanged-through-audit')
        a.eq(digest(view),account_digest,'account-view-unchanged-through-audit')
        for name,h in inputs.items(): a.eq(sha(name),h,'input-post-audit-sha',file=name)
        for name,h in sources.items(): a.eq(sha(local(root,name)),h,'implementation-post-audit-sha',file=name)
        for k,row in c['files'].items(): a.eq(sha(output/row['path']),row['sha256'],'output-post-audit-sha',file=k)
        a.eq(sha(output/'attempts/attempt-0001/completion.json'),completion_sha,'completion-post-audit-sha')
    except Exception as error:
        a.check(False,'verification-exception',errorType=type(error).__name__,message=str(error))
    a.eq(sha(VERIFIER_PATH),VERIFIER_SHA256,'verifier-end-sha')
    return {'schema':'account-exit-comparison-independent-verification-v1','passed':a.failed==0,'verificationCompleted':a.failed==0,
        'checks':a.n,'failureCount':a.failed,'issues':a.issues,'elapsedSeconds':time.perf_counter()-start,
        'protocolHash':protocol_hash,'resultSha256':result_sha,'completionSha256':completion_sha,'registrationSha256':REGISTRATION_SHA256,
        'verifierSha256':VERIFIER_SHA256,'sourcePins':sources,'resultFileHashes':files,'opportunityCount':nsignals,
        'scope':['Complete pre-active source signals and all original Top5 opportunities; exact hashes/archive/EOF.',
            'Independent raw daily account state machine: cash-constrained rank entry, T-close size and one next-open attempt, conservative gates, persistent exits and partial fills.',
            'Independent execution-date fees, paid-fill linkage, cash/shares/cost basis/closed episodes, daily marks and continuous-window outcome arithmetic.'],
        'limitations':['Frozen prior source/panel mathematical audits are inherited, not rerun feature extraction.',
            'No declared corporate-action instructions supported in this version; raw reference changes, quarantine and unaccounted exposure are retained.',
            'Daily bars and simulated capacity do not certify actual opening fills, complete historical universe, rights, broker fees or economic total return.',
            'Existing auxiliary regime/benchmark report metrics and UI text are not independently recomputed; primary account/outcome arithmetic is.',
            'Three exposed development policies are not matched-trade causal estimates, independent future probabilities or a profitable-strategy certificate.'],
        'newStrategyTrials':0,'certified':False,'targetAchieved':False}


def save_report(output,report):
    name='independent-result-verification.json' if report['passed'] else 'independent-result-verification-failed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'
    path=Path(output)/name
    with path.open('x',encoding='utf8',newline='\n') as stream: json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2); stream.write('\n')
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT); args=parser.parse_args(argv)
    report=verify(args.output); path=save_report(args.output,report)
    print(json.dumps({'passed':report['passed'],'checks':report['checks'],'failureCount':report['failureCount'],'report':str(path)},ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

