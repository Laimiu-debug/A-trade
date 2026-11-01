"""Numerical structural-price comparison repair without changing source data."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

from .research_sector_lifecycle_system import SectorLifecycleAccount
from .research_volatility_trail import VolatilityTrailAccount,_bar_issue

ROOT=Path(__file__).resolve().parents[1]
VERSION='sector-lifecycle-structural-price-precision-v1'
SOURCE_FILES=('docs/sector-lifecycle-price-boundary-repair-v1.json','service/research_sector_lifecycle_precision.py')


def source_pins():return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}
def price_tolerance(threshold):return 16*math.ulp(threshold)


class PrecisionSafeLifecycleAccount(SectorLifecycleAccount):
    def __init__(self,*args,**kwargs):
        self.precision_identity={'version':VERSION,'sourcePins':source_pins(),'comparison':'sixteenUlpsAtStructuralPrice'}
        super().__init__(*args,**kwargs)

    def _identity_stable(self):
        super()._identity_stable()
        if self.precision_identity['sourcePins']!=source_pins():raise ValueError('Numerical repair source changed')

    def _risk_exits(self,day,eligibility):
        # Controlled copy of the system seam: only structural comparison and
        # its explicit tolerance evidence differ. Existing first exits win.
        VolatilityTrailAccount._risk_exits(self,day,eligibility)
        context=self._system_day(day)
        sectors={r['sectorCode']:r for r in context['sectors']}
        mapping=self._system_maps.get(context['snapshotId'],{})
        for symbol,position in sorted(self.state['positions'].items()):
            binding=self._system_live[symbol]
            if symbol in mapping:binding['lastKnownSector']=mapping[symbol]
            sector=sectors.get(mapping.get(symbol));bar,_=self._bar(symbol,day)
            valid=_bar_issue(bar,day) is None
            threshold=binding['failureRatio']*position['riskEntryPrice']
            tolerance=price_tolerance(threshold)
            structural=bool(valid and bar['close']<=threshold+tolerance)
            market=context['market']['riskExit'] is True
            industry=sector is not None and sector['riskExit'] is True
            earlier=self.state['forcedExits'].get(symbol)
            reason=earlier['reason'] if earlier else 'systemStructuralFailure' if structural else 'systemMarketRisk' if market else 'systemSectorRisk' if industry else None
            evidence={'date':day,'symbol':symbol,'signalId':binding['signalId'],'entryDate':position['entryDate'],
                'riskEntryPrice':position['riskEntryPrice'],'failureRatio':binding['failureRatio'],'failureThreshold':threshold,
                'observedClose':bar['close'] if bar else None,'currentPriceValid':valid,'structuralTriggered':structural,
                'marketRiskTriggered':market,'marketCrisisCount':context['market']['crisisCount'],'knownSector':mapping.get(symbol),
                'sectorFailCount':sector['failCount'] if sector else None,'sectorRiskTriggered':industry,
                'earlierRiskRetained':earlier is not None,'reason':reason,'structuralPriceTolerance':tolerance}
            self._system_risk.append(evidence)
            if reason and earlier is None:self._queue_system_exit(day,symbol,reason,evidence)

    def _system_result(self):return {**super()._system_result(),'precisionIdentity':deepcopy(self.precision_identity)}
