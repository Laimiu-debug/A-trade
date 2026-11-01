"""Current market/industry strength, long leaders and frozen prepared catchup."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import numpy as np

from .research_market_sector_stock import stock_features,prior_extreme,midranks,SID
from .research_market_sector_account import MarketSectorStockAccount
from .research_prepared_base_system import coherent
from .research_sector_lifecycle_precision import PrecisionSafeLifecycleAccount,source_pins as precision_pins
from .research_volatility_trail import _positive

ROOT=Path(__file__).resolve().parents[1]
VERSION='sector-long-breakout-system-v4'
POLICIES=('completeSystem','withoutMarketAdmission','withoutSectorAdmission','withoutCatchupEntry','catchupOnly')
SOURCE_FILES=('docs/sector-long-breakout-system-v4.json','service/research_sector_long_breakout_system.py')


def source_pins():return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def rolling_sum(values,width):
    total=np.concatenate([np.zeros((len(values),1),dtype=np.int64),np.cumsum(values,axis=1,dtype=np.int64)],axis=1)
    answer=np.zeros(values.shape,dtype=np.int64)
    if values.shape[1]>=width:answer[:,width-1:]=total[:,width:]-total[:,:-width]
    return answer


def long_features(matrix):
    close=matrix['close'];valid=matrix['priceValid'] & ~matrix['explicitSuspended']
    known=np.isfinite(close)&(close>0)
    raw=np.where(known,close*100,0.)
    if not np.all(abs(raw[known]-np.rint(raw[known]))<1e-6):raise ValueError('Long price arithmetic requires the validated raw cent grid')
    cents=np.rint(raw).astype(np.int64)
    complete=valid.copy()
    for field in ('open','high','low','close','volume','amount'):complete &= np.isfinite(matrix[field])&(matrix[field]>0)
    complete &= matrix['low']<=np.minimum(matrix['open'],close)
    complete &= np.maximum(matrix['open'],close)<=matrix['high']
    known141=rolling_sum(complete.astype(np.int64),141)==141
    peer_known=rolling_sum(valid.astype(np.int64),121)==121
    previous=np.zeros(cents.shape,dtype=np.int64);previous[:,120:]=cents[:,:-120]
    with np.errstate(divide='ignore',invalid='ignore'):
        r120=np.where(peer_known,cents/previous-1,np.nan)
    sum60,sum120=rolling_sum(cents,60),rolling_sum(cents,120)
    old_sum=np.zeros(sum120.shape,dtype=np.int64);old_sum[:,20:]=sum120[:,:-20]
    trend=(cents*60>sum60)&(sum60*2>sum120)&(sum120>old_sum)
    calm=np.zeros(cents.shape,bool);calm[:,21:]=cents[:,20:-1]*100<=cents[:,:-21]*115
    high120=prior_extreme(matrix['high'],valid,120,True)
    first=np.zeros(cents.shape,bool)
    first[:,1:]=(close[:,1:]>high120[:,1:])&(close[:,:-1]<=high120[:,:-1])
    return {'ready':known141&trend&calm&first,'return120':r120,'cents':cents,'sum60':sum60,'sum120':sum120,
        'oldSum120':old_sum,'priorHigh120':high120}


def build_inputs(matrix,joins,original_context,prepared_anchors):
    features=stock_features(matrix);long=long_features(matrix)
    dates,symbols=matrix['calendar'].tolist(),matrix['symbols'].tolist()
    maps={j['snapshotId']:{g['sectorCode']:g['cohortRowIndices'] for g in j['sectors']} for j in joins}
    anchors=defaultdict(dict)
    for policy,signals in prepared_anchors['schedules'].items():
        for signal in signals:
            meta=prepared_anchors['metadata'][signal['signalId']]
            if meta['pattern']!='catchup':continue
            key=policy,signal['signalDate'];code=meta['sectorCode']
            if code in anchors[key]:raise ValueError('Frozen catchup anchor duplicates an industry close')
            anchors[key][code]=meta
    schedules,metadata,counts={p:[] for p in POLICIES},{},[]
    for day in original_context['daily']:
        i=day['index'];ordered=sorted((s for s in day['sectors'] if s['featureValid']),key=lambda s:(-s['score'],s['sectorCode']))
        leaders={}
        for sector in ordered:
            code=sector['sectorCode'];rows=np.asarray(maps[day['snapshotId']][code],int)
            returns=long['return120'][rows,i];observed=np.isfinite(returns);n=int(observed.sum())
            choices=[]
            if len(rows)>=5 and n/len(rows)>=.95:
                ranks=midranks(returns);average=math.fsum(returns[observed].tolist())/n
                for j,r in enumerate(rows.tolist()):
                    if i<140 or not long['ready'][r,i] or not (returns[j]>0 and returns[j]>average and ranks[j]>=.8):continue
                    amount=math.fsum(matrix['amount'][r,i-20:i].tolist())/20
                    if amount<50_000_000:continue
                    close,ntr=features['close'][r,i],features['meanNtr20'][r,i]
                    floor=max(features['priorLow10'][r,i],.95*close)
                    if not np.isfinite([close,ntr,floor]).all() or not (0<=ntr<1 and 0<floor<close):continue
                    info={'pattern':'longLeaderBreakout','signalClose':float(close),'failurePrice':float(floor),'meanNtr20':float(ntr),
                        'sectorCode':code,'snapshotId':day['snapshotId'],'sectorScore':sector['score'],'stockReturn120Midrank':float(ranks[j]),
                        'priorAmount20':float(amount),'longEvidence':{'windowFrom':dates[i-140],'windowThrough':day['date'],'observedSlots':141,
                            'priorHigh120':float(long['priorHigh120'][r,i]),'ma60':float(long['sum60'][r,i]/6000),
                            'ma120':float(long['sum120'][r,i]/12000),'ma120Lag20':float(long['oldSum120'][r,i]/12000),
                            'prior20Return':float(long['cents'][r,i-1]/long['cents'][r,i-21]-1),'return120':float(returns[j]),
                            'industryMeanReturn120':average,'industry120Known':n,'industryMembers':len(rows),
                            'priorLow10':float(features['priorLow10'][r,i]),'protective95Close':float(.95*close)}}
                    choices.append(((-amount,-ranks[j],symbols[r]),symbols[r],info))
            leaders[code]=min(choices,key=lambda q:q[0]) if choices else None
            if choices:counts.append({'date':day['date'],'sectorCode':code,'longLeaders':len(choices)})
        for policy in POLICIES:
            if policy!='withoutMarketAdmission' and day['market']['admission'] is not True:continue
            anchor_policy=policy if policy in ('withoutMarketAdmission','withoutSectorAdmission') else 'completeSystem'
            chosen=[]
            for sector in ordered:
                code=sector['sectorCode']
                if policy!='withoutSectorAdmission' and code not in day['selectedSectors']:continue
                candidate=leaders[code] if policy!='catchupOnly' else None
                if candidate:
                    _,symbol,info=candidate;chosen.append((symbol,info))
                elif policy!='withoutCatchupEntry' and code in anchors[anchor_policy,day['date']]:
                    old=anchors[anchor_policy,day['date']][code]
                    if old['snapshotId']!=day['snapshotId']:raise ValueError('Catchup anchor snapshot mismatch')
                    info={k:deepcopy(v) for k,v in old.items() if k not in ('signalId','symbol','signalDate','originalRank')}
                    info.update(anchorSourceId=old['signalId'],anchorOriginalRank=old['originalRank'])
                    chosen.append((old['symbol'],info))
            for rank,(symbol,info) in enumerate(chosen[:5],1):
                identifier=f'sector-long-v4:{policy}:{day["date"]}:{symbol}'
                signal={'signalId':identifier,'strategyId':SID,'symbol':symbol,'signalDate':day['date'],'originalRank':rank}
                schedules[policy].append(signal);metadata[identifier]={**info,**{k:v for k,v in signal.items() if k!='strategyId'}}
    return {'version':VERSION,'schedules':schedules,'metadata':metadata,'longCandidateCounts':counts,
        'contextDecisionDays':len(original_context['daily']),'counts':{p:len(v) for p,v in schedules.items()}}


class SectorLongAccount(MarketSectorStockAccount):
    _risk_exits=PrecisionSafeLifecycleAccount._risk_exits

    def __init__(self,*args,policy_label,**kwargs):
        self.long_identity={'version':VERSION,'policy':policy_label,'sourcePins':source_pins()}
        self.precision_identity={'version':'sector-lifecycle-structural-price-precision-v1','comparison':'sixteenUlpsAtStructuralPrice','sourcePins':precision_pins()}
        self._catchup_opening=[]
        super().__init__(*args,**kwargs)
        for signal in self._schedule:
            m=self._system_meta[signal['signalId']]
            if m['pattern'] not in ('longLeaderBreakout','catchup'):raise ValueError('Registered long-leader/catchup role required')
            if m['pattern']=='catchup' and (not coherent(m['signalClose'],m['failurePrice'],m['meanNtr20']) or m['base']['failurePrice']!=m['failurePrice']):
                raise ValueError('Inherited catchup support/risk mismatch')

    def _identity_stable(self):
        super()._identity_stable()
        if self.long_identity['sourcePins']!=source_pins() or self.precision_identity['sourcePins']!=precision_pins():raise ValueError('Long-system/precision source changed')

    def _transaction_snapshot(self):return super()._transaction_snapshot(),len(self._catchup_opening)

    def _transaction_restore(self,snapshot):
        parent,n=snapshot;super()._transaction_restore(parent);del self._catchup_opening[n:]

    def _execute_order(self,day,symbol,side,requested,pending):
        if side=='buy' and self._system_meta[pending['signalId']]['pattern']=='catchup':
            m=self._system_meta[pending['signalId']];bar,_=self._bar(symbol,day)
            if bar is not None and _positive(bar.get('open')) and m['failurePrice']<bar['open']<=m['signalClose']*(1+m['meanNtr20']):
                price=self._fill_price(symbol,day,side)
                if price is not None:
                    reason='preparedBaseOpeningRiskTooWide' if m['failurePrice']<.95*price else 'preparedBaseOpeningRiskInsideOneNtr' if m['failurePrice']>price*(1-m['meanNtr20']) else None
                    self._catchup_opening.append({'date':day,'symbol':symbol,'signalId':pending['signalId'],'modeledPrice':price,'failurePrice':m['failurePrice'],
                        'signalNtr20':m['meanNtr20'],'status':'rejected' if reason else 'passed','reason':reason})
                    if reason:self._decision(day,symbol,'buy','deferred',reason,signalDate=pending['signalDate']);return
        super()._execute_order(day,symbol,side,requested,pending)

    def _system_result(self):return {**super()._system_result(),'longSystemIdentity':deepcopy(self.long_identity),
        'precisionIdentity':deepcopy(self.precision_identity),'catchupOpeningGuards':deepcopy(self._catchup_opening)}
