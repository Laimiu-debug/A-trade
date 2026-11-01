"""Source-inspired daily two-path system. No account outcome enters formation."""
from copy import deepcopy
import hashlib
from pathlib import Path
import numpy as np

from .portfolio_engine import _digest, _quantity, lot_rule
from .research_fixed_signal_account import FixedSignalAccount
from .research_market_sector_stock import stock_features, mean_window, return_window, midranks
from .research_volatility_trail import _bar_issue

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'guiyin-dual-path-system-v1'
POLICIES = ('complete', 'Aonly', 'Bonly', 'withoutMarket', 'withoutSector', 'basicExits', 'extra25bpsEachSide')


def pins():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
            ('docs/guiyin-dual-path-system-v1.json', 'service/research_guiyin_dual_path.py')}


def market_state(market, policy):
    if policy == 'withoutMarket':
        return 'green', .6, .2, .005
    if market['admission'] is not True:
        return 'off', 0., 0., 0.
    return ('green', .6, .2, .005) if market['turnover5ToPrior20'] >= 1 else ('yellow', .3, .1, .0025)


def build_inputs(matrix, joins, original, benchmark_close):
    f = stock_features(matrix)
    close = f['close']
    cents = np.rint(np.nan_to_num(close, nan=0.)*100).astype(np.int64)
    ma5, ma10 = mean_window(close, 5), mean_window(close, 10)
    r5 = return_window(close, 5)
    bench = {n: return_window(np.asarray(benchmark_close, float), n) for n in (5, 20)}
    symbols = matrix['symbols'].tolist()
    maps = {j['snapshotId']: {g['sectorCode']: g['cohortRowIndices'] for g in j['sectors']} for j in joins}
    sector_maps = {j['snapshotId']: {symbols[r]: g['sectorCode'] for g in j['sectors'] for r in g['cohortRowIndices']} for j in joins}
    daily, schedules, metadata, all_candidates = [], {p: [] for p in POLICIES}, {}, []
    last_snapshot, failures = None, {}
    for old in original['daily']:
        i, day, snap = old['index'], old['date'], old['snapshotId']
        if snap != last_snapshot:
            failures, last_snapshot = {}, snap
        sectors, choices_by_sector = [], {}
        for source in old['sectors']:
            code = source['sectorCode']
            rows = np.asarray(maps[snap][code], int)
            values = (r5[rows, i], f['return20'][rows, i], f['ma20'][rows, i], f['amount5'][rows, i] / f['priorAmount20'][rows, i])
            masks = [np.isfinite(a) for a in values]
            counts = [int(a.sum()) for a in masks]
            known = len(rows) >= 5 and min(counts) / len(rows) >= .95
            ratio = old['market']['turnover5ToPrior20']
            known = known and ratio is not None and ratio > 0 and all(np.isfinite(bench[n][i]) for n in (5, 20))
            avg5 = float(np.mean(values[0][masks[0]])) if known else None
            avg20 = float(np.mean(values[1][masks[1]])) if known else None
            breadth = float(np.mean(cents[rows[masks[2]], i]*20 > cents[rows[masks[2]], i-19:i+1].sum(axis=1))) if known else None
            amount_mask = masks[3]
            participation = float(f['amount5'][rows[amount_mask], i].sum() / f['priorAmount20'][rows[amount_mask], i].sum() / ratio) if known else None
            rel5, rel20 = (avg5-bench[5][i], avg20-bench[20][i]) if known else (None, None)
            health = bool(rel20 > 0 and breadth > .5) if known else None
            failures[code] = failures.get(code, 0)+1 if health is False else 0
            strong = bool(avg5 > 0 and rel20 > 0 and breadth > .5 and participation >= 1) if known else False
            sector = dict(sectorCode=code, sectorName=source['sectorName'], known=bool(known), coverageCounts=counts,
                          cohortMembers=len(rows), return5=avg5, return20=avg20, relative5=float(rel5) if known else None,
                          relative20=float(rel20) if known else None, breadth20=breadth, participation=participation,
                          strong=strong, health=health, failCount=failures[code], score=None)
            sectors.append(sector)
            choices = []
            prior_ranks = midranks(f['return20'][rows, i-1])
            for j, r in enumerate(rows.tolist()):
                if i < 21 or not matrix['priceValid'][r, i-21:i+1].all():
                    continue
                if not (matrix['volume'][r, i-21:i+1] > 0).all() or not (matrix['amount'][r, i-21:i+1] > 0).all() or matrix['explicitSuspended'][r, i-21:i+1].any():
                    continue
                c, previous, amount, prior_amount, ntr = close[r, i], close[r, i-1], f['amount'][r, i], f['priorAmount20'][r, i], f['meanNtr20'][r, i]
                if not all(np.isfinite(v) for v in (c, previous, prior_amount, ntr, prior_ranks[j], ma10[r, i], ma5[r, i-1])) or prior_amount < 5e7 or not 0 < ntr < 1:
                    continue
                hi, lo = matrix['high'][r, i], matrix['low'][r, i]
                close_position = (c-lo)/(hi-lo) if hi > lo else 0.
                cc, pc, hc, lc = cents[r, i], cents[r, i-1], round(hi*100), round(lo*100)
                ignition = bool(pc*100 <= 115*cents[r,i-21] and c > f['priorHigh20'][r, i] and previous <= f['priorHigh20'][r, i-1]
                                and cc*100 >= pc*105 and amount >= 1.5*prior_amount and hi>lo and cc*4 >= hc*3+lc)
                pullback = bool(pc > cents[r,i-21] and prior_ranks[j] >= .8 and pc*10 > cents[r,i-10:i].sum()
                                and c < previous and lc*5 <= cents[r,i-5:i].sum() and cc*10 >= cents[r,i-9:i+1].sum() and hi>lo and cc*2 >= hc+lc and amount >= .5*prior_amount)
                if not ignition and not pullback:
                    continue
                pattern = 'A_ignition' if ignition else 'B_corePullback'
                floor = max(.95*c, min(float(matrix['low'][r, i-2:i+1].min()), .99*c))
                choices.append(dict(symbol=symbols[r], pattern=pattern, signalClose=float(c), failurePrice=float(floor), meanNtr20=float(ntr),
                                    sectorCode=code, sectorName=source['sectorName'], snapshotId=snap, priorReturn20=float(f['return20'][r, i-1]),
                                    priorIndustryRank20=float(prior_ranks[j]), priorAmount20=float(prior_amount), closePosition=float(close_position),
                                    contextCutoff=day, sourceIndex=i))
            choices_by_sector[code] = sorted(choices, key=lambda v:(v['pattern'], -v['priorIndustryRank20'], -v['priorAmount20'], v['symbol']))
        known_sectors = [s for s in sectors if s['known']]
        if known_sectors:
            ranks = [midranks([s[k] for s in known_sectors]) for k in ('relative5', 'relative20', 'participation')]
            for j, s in enumerate(known_sectors):
                s['score'] = sum(round(float(rank[j])*2*len(known_sectors)) for rank in ranks)/(6*len(known_sectors))
        ordered = sorted(known_sectors, key=lambda s:(-s['score'], s['sectorCode']))
        selected = [s['sectorCode'] for s in ordered if s['strong']][:3]
        daily.append(dict(date=day, index=i, snapshotId=snap, market=deepcopy(old['market']), selectedSectors=selected, sectors=sectors))
        for code, values in choices_by_sector.items():
            for v in values:
                all_candidates.append(dict(signalDate=day, **v))
        for policy in POLICIES:
            if market_state(old['market'], policy)[0] == 'off':
                continue
            chosen = []
            for sector in ordered:
                code = sector['sectorCode']
                if policy != 'withoutSector' and code not in selected:
                    continue
                choices = [v for v in choices_by_sector[code] if policy != 'Aonly' or v['pattern']=='A_ignition']
                choices = [v for v in choices if policy != 'Bonly' or v['pattern']=='B_corePullback']
                if choices:
                    chosen.append({**choices[0], 'sectorScore':sector['score']})
            for rank, meta in enumerate(chosen[:5], 1):
                signal = dict(signalId=f'dual-v1:{policy}:{day}:{meta["symbol"]}', strategyId='marketSectorStockV1', symbol=meta['symbol'], signalDate=day, originalRank=rank)
                schedules[policy].append(signal)
                metadata[signal['signalId']] = {**signal, **meta}
    return dict(version=VERSION, daily=daily, sectorMaps=sector_maps, schedules=schedules, metadata=metadata, allCandidates=all_candidates)


class DualPathAccount(FixedSignalAccount):
    def __init__(self, dataset, signals, config, *, system_inputs, policy, fee_policy):
        if policy not in POLICIES:
            raise ValueError('Unknown fixed policy')
        self.system_inputs, self.policy = deepcopy(system_inputs), policy
        self.context = {d['date']:d for d in self.system_inputs['daily']}
        self.meta = self.system_inputs['metadata']
        self.live, self.plans, self.openings, self.risks = {}, [], [], []
        self.system_identity = dict(version=VERSION, policy=policy, inputDigest=_digest(self.system_inputs), sourcePins=pins())
        for s in signals:
            m, d = self.meta[s['signalId']], self.context[s['signalDate']]
            if any(m[k] != s[k] for k in s) or m['snapshotId'] != d['snapshotId'] or self.system_inputs['sectorMaps'][m['snapshotId']].get(s['symbol']) != m['sectorCode']:
                raise ValueError('Signal provenance mismatch')
            if market_state(d['market'], policy)[0]=='off' or (policy!='withoutSector' and m['sectorCode'] not in d['selectedSectors']):
                raise ValueError('Admission bypass')
        super().__init__(dataset, signals, config, entry_weight=.2, fee_policy=fee_policy)

    def _identity_stable(self):
        super()._identity_stable()
        if self.system_identity != dict(version=VERSION, policy=self.policy, inputDigest=_digest(self.system_inputs), sourcePins=pins()):
            raise ValueError('Frozen system drift')

    def _transaction_snapshot(self):
        return super()._transaction_snapshot(), deepcopy(self.live), len(self.plans), len(self.openings), len(self.risks)

    def _transaction_restore(self, snapshot):
        base, self.live, a, b, c = snapshot
        super()._transaction_restore(base)
        del self.plans[a:]; del self.openings[b:]; del self.risks[c:]

    def _values(self, day, opening_day=None):
        context, sectors, total = self.context[day], {}, 0.
        mapping = self.system_inputs['sectorMaps'].get(context['snapshotId'], {})
        for symbol, p in self.state['positions'].items():
            code = mapping.get(symbol, self.live[symbol]['sectorCode'])
            bar, _ = self._bar(symbol, opening_day) if opening_day else (None, None)
            price = bar['open'] if bar is not None and bar.get('open', 0)>0 else self.state['lastPrices'][symbol]
            value = p['quantity']*price
            sectors[code] = sectors.get(code, 0.)+value
            total += value
        return sectors, total

    def _select_targets(self, scored, excluded=None):
        day, equity = scored['date'], self.state['curve'][-1]['equity']
        state, gross_cap, stock_cap, risk = market_state(self.context[day]['market'], self.policy)
        sectors, total = self._values(day)
        cash = max(0., self.state['cash']-self.state['reservedActionCash'])
        free = self.config['maxPositions']-len(self.state['positions'])
        chosen, weights, priorities = [], {}, {}
        self.research_state['closePlan'] = []
        for row in self._opportunities(day):
            m = self.meta[row['signalId']]
            row['nextSession'] = self._next_session(day)
            reason = self._close_exclusion(row['symbol'], day, excluded or set())
            reason = reason or ('plannedSlotsOccupied' if free<=0 else None) or ('marketOff' if state=='off' else None)
            if reason:
                row.update(status='notPlanned', reason=reason)
                continue
            free -= 1
            price = m['signalClose']*(1+self.config['slippageBps']/10000)
            minimum, increment = lot_rule(row['symbol'])
            risk_quantity = _quantity(risk*equity/(price-m['failurePrice']), minimum, increment)
            budget = min(stock_cap*equity, max(0., gross_cap*equity-total), max(0., .4*equity-sectors.get(m['sectorCode'],0.)), cash, risk_quantity*price)
            ctx = self._context(row['symbol'], day, day, 'signalReserve')
            qty = self.fee_ledger.affordable_quantity(min(risk_quantity, _quantity(budget/price, minimum, increment)), minimum, increment, price, budget, ctx)
            quote = self.fee_ledger.quote(qty*price, 'buy', ctx)
            row.update(budget=budget, reservePrice=price, plannedQuantity=qty, reserveQuoteId=quote['quoteId'], unfilledQuantity=qty,
                       equityAtSignal=equity, stopRiskBudget=risk*equity, grossCap=gross_cap)
            if row['nextSession'] is None or row['nextSession']>self.dates[-1]:
                row.update(status='censored', reason='nextSessionOutsideStudy')
            elif not qty:
                row.update(status='notPlanned', reason='cashRiskGrossOrSectorBelowLot')
            else:
                row.update(status='planned', reason='nextSessionOnly')
                chosen.append(row['symbol']); self.research_state['closePlan'].append(row['signalId'])
                weights[row['symbol']] = budget/equity
                priorities[row['symbol']] = dict(budgetWeight=budget/equity, weightedScore=(6-row['originalRank'])/5, originalRank=row['originalRank'], signalId=row['signalId'])
                cash -= qty*price+quote['breakdown']['total']; total += qty*price
                sectors[m['sectorCode']] = sectors.get(m['sectorCode'], 0.)+qty*price
            self.plans.append(dict(date=day, signalId=row['signalId'], state=state, equity=equity, budget=budget, quantity=qty, totalReservedMarketValue=total, grossCap=gross_cap, stopRiskBudget=risk*equity))
        return {self.strategy_id:chosen}, weights, {self.strategy_id:1.}, {self.strategy_id:[deepcopy(r) for r in self._opportunities(day) if r['symbol'] in chosen]}, [], priorities

    def _execute_order(self, day, symbol, side, requested, pending):
        start = len(self.state['trades'])
        if side == 'buy':
            m = self.meta[pending['signalId']]
            row = next(r for r in self._opportunities(pending['signalDate']) if r['signalId']==pending['signalId'])
            bar, _ = self._bar(symbol, day)
            if bar is not None and bar.get('open',0)>0:
                opening = bar['open']
                reason = 'openingBelowStructuralFloor' if opening<=m['failurePrice'] else 'openingTooExtended' if opening>m['signalClose']*(1+min(m['meanNtr20'],.03)) else None
                if reason:
                    self.openings.append(dict(date=day, signalId=m['signalId'], status='rejected', reason=reason))
                    self._decision(day, symbol, 'buy', 'deferred', reason, signalDate=pending['signalDate']); return
                price = self._fill_price(symbol, day, side)
                if price is not None:
                    minimum, increment = lot_rule(symbol)
                    floor = max(m['failurePrice'], .95*price)
                    sectors, total = self._values(pending['signalDate'], day)
                    room = min(max(0.,row['grossCap']*row['equityAtSignal']-total),max(0.,.4*row['equityAtSignal']-sectors.get(m['sectorCode'],0.)))
                    qty = min(requested, _quantity(row['stopRiskBudget']/(price-floor), minimum, increment), _quantity(room/price, minimum, increment))
                    self.openings.append(dict(date=day, signalId=m['signalId'], status='submitted', opening=opening, price=price, quantityCap=qty, priorValue=total, room=room, floor=floor))
                    if not qty:
                        self._decision(day,symbol,'buy','deferred','openingRiskGrossOrSectorBelowLot',signalDate=pending['signalDate']); return
                    quote = self.fee_ledger.quote(qty*price,'buy',self._context(symbol,pending['signalDate'],day,'executionSizing'))
                    pending = deepcopy(pending); pending['budgets'][symbol] = min(pending['budgets'][symbol], qty*price+quote['breakdown']['total'])
        super()._execute_order(day, symbol, side, requested, pending)
        fills = self.state['trades'][start:]
        if side == 'sell' and symbol not in self.state['positions']:
            self.live.pop(symbol, None)
        elif side == 'buy' and fills:
            fill = fills[0]; m = self.meta[fill['signalId']]
            self.live[symbol] = dict(signalId=m['signalId'], sectorCode=m['sectorCode'], pattern=m['pattern'], floorRatio=max(m['failurePrice'],.95*fill['price'])/fill['price'], trailRatio=0., ntr=m['meanNtr20'])

    def _queue(self, day, symbol, reason, evidence):
        self.state['forcedExits'][symbol] = dict(signalDate=day, reason=reason, riskEvidence=deepcopy(evidence))
        pending = self.state['pendingTarget']
        if pending is not None:
            for key in ('weights','budgets','priorityBySymbol','signalDates'):
                pending.get(key,{}).pop(symbol,None)
            pending['quantities'][symbol] = 0
        self._decision(day,symbol,'sell','queued',reason,signalDate=day,riskEvidence=deepcopy(evidence))

    def _risk_exits(self, day, eligibility):
        super()._risk_exits(day,eligibility)
        if self.policy == 'basicExits':
            return
        context = self.context[day]
        sectors = {s['sectorCode']:s for s in context['sectors']}
        mapping = self.system_inputs['sectorMaps'].get(context['snapshotId'],{})
        for symbol, p in sorted(self.state['positions'].items()):
            b, _ = self._bar(symbol,day)
            if symbol in self.state['forcedExits'] or _bar_issue(b,day):
                continue
            live = self.live[symbol]
            gain = b['close']/p['riskEntryPrice']-1
            sessions = self.calendar_index[day]-self.calendar_index[p['entryDate']]+1
            if p['closePeak']/p['riskEntryPrice'] >= 1.06:
                live['trailRatio'] = max(live['trailRatio'],p['closePeak']/p['riskEntryPrice']*(1-max(.03,min(.08,1.5*live['ntr']))))
            sector = sectors.get(mapping.get(symbol,live['sectorCode']))
            reason = ('structuralFailure' if b['close']<=live['floorRatio']*p['riskEntryPrice'] else
                      'entryNoFollowThrough' if sessions==1 and gain<=0 else
                      'secondSessionNoProgress' if sessions>=2 and gain<.02 else
                      'profitRatchet' if b['close']<=live['trailRatio']*p['riskEntryPrice'] else
                      'sectorHealthFailure' if self.policy!='withoutSector' and sector is not None and sector['failCount']>=2 else None)
            evidence = dict(date=day,symbol=symbol,close=b['close'],gain=gain,sessions=sessions,structuralRatio=live['floorRatio'],trailRatio=live['trailRatio'],sectorFailCount=sector['failCount'] if sector else None)
            self.risks.append({**evidence,'reason':reason})
            if reason:
                self._queue(day,symbol,reason,evidence)
        _, cap, _, _ = market_state(context['market'],self.policy)
        retained = [(self.state['lastPrices'][s]/p['riskEntryPrice']-1,s,p['quantity']*self.state['lastPrices'][s]) for s,p in self.state['positions'].items() if s not in self.state['forcedExits']]
        total = sum(v for _,_,v in retained); equity = self.state['curve'][-1]['equity']
        for gain,symbol,value in sorted(retained):
            if total<=cap*equity+1e-8:
                break
            self._queue(day,symbol,'marketExposureReduction',dict(grossCap=cap,equity=equity,unreservedValueBefore=total,gain=gain))
            total -= value

    def result(self):
        r = super().result()
        r['research']['dualSystem'] = dict(identity=deepcopy(self.system_identity),plans=deepcopy(self.plans),openingGuards=deepcopy(self.openings),riskObservations=deepcopy(self.risks))
        return r
