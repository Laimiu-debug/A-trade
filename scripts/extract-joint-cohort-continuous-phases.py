"""Read existing audited phase equity to distinguish holding gains from entries.

This follow-up is descriptive and was specified after the joint-state result.
It does not change that registered partition or run a trading rule.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'artifacts/strategy-search'
OUT=BASE/'joint-cohort-regime-v1'
SOURCE=BASE/'reclaim-volatility-trail-v1'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def ref(path):
    return {'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path)}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    source,proof=read(SOURCE/'readout.json'),read(SOURCE/'readout-validation.json')
    parent=read(BASE/'reclaim-relative-feedback-context-v1/readout.json')
    require=parent['sourceAccountReadout']
    assert require==ref(SOURCE/'readout.json')
    assert proof['passed'] and proof['readoutSha256']==sha(SOURCE/'readout.json')
    names=('period','board','policy','phaseId','observedStart','observedEnd','observedSessions',
           'enteringEquity','endingEquity','netPnlAmount','returnPct','buyFillCount','sellFillCount','endingPositionCount')
    rows=[{**{k:r[k] for k in names},'valuationFlagDays':len(r['valuationUnreliableDates'])} for r in source['phaseRows']]
    assert len(rows)==len({(r['period'],r['board'],r['policy'],r['phaseId']) for r in rows})==96
    result={'schema':'joint-cohort-continuous-phase-context-v1','createdAt':datetime.now(timezone.utc).isoformat(),
            'sourceReadout':ref(SOURCE/'readout.json'),'sourceValidation':ref(SOURCE/'readout-validation.json'),
            'extractor':ref(Path(__file__).resolve()),'rows':rows,
            'timing':'Descriptive follow-up specified after joint-state outcomes; not part of the preregistered new partition.',
            'meaning':'Actual original equity carried across every phase. Period cash/positions are not reset. Buy/sell counts count fills, not unique episodes. Gains include marked open holdings and inherited valuation flags.',
            'comparisonWarning':'Formation-time cohorts attribute the eventual position result to entry context. These continuous rows instead measure equity movement during the named period. Their money measures must not be equated.',
            'newAccounts':0,'newSignals':0,'certified':False,'targetAchieved':False}
    with (OUT/'continuous-phase-context.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({'completed':True,'rows':len(rows),'artifact':ref(OUT/'continuous-phase-context.json')},ensure_ascii=False))


if __name__=='__main__':
    main()
