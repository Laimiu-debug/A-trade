"""Register one industry-lifecycle hypothesis only after complete V2 audit."""
from pathlib import Path
import importlib.util
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_sector_lifecycle_registration_utils',ROOT/'scripts/run-liquid-trend-breakout.py')
U=importlib.util.module_from_spec(spec);sys.modules[spec.name]=U;spec.loader.exec_module(U)
BASE=ROOT/'artifacts/strategy-search'
PARENT=BASE/'market-sector-stock-base-v2/attempt-001'
OUT=BASE/'sector-lifecycle-system-v3'
CONTRACT=ROOT/'docs/sector-lifecycle-system-v3.json'


def register():
    proof=U.read(PARENT/'validation.json')
    if not proof['passed']:raise ValueError('All five V2 accounts must be independently audited')
    result=U.read(U.bound(proof['readout']));coverage=U.read(PARENT/'entry-coverage-001/readout.json')
    for k in ('plan','inputs','fullAccountAudit','diagnostics'):U.bound(proof[k])
    U.bound(coverage['formation']);U.bound(coverage['context']);U.bound(coverage['builder'])
    if len(result['rows'])!=5 or result['profitGoalAchieved']:raise ValueError('Expected complete failed second-system development evidence')
    if OUT.exists() or CONTRACT.exists():raise ValueError('Preserve existing hypothesis')
    rules={'schema':'sector-lifecycle-whole-system-v3-hypothesis','status':'fixed-before-implementation-and-V3-outcomes',
        'researchUnit':'Complete market-sector-stock-rotation portfolio system',
        'parentPreparedBaseProtocol':U.ref(ROOT/'docs/market-sector-stock-base-v2.json'),'parentAccountValidation':U.ref(PARENT/'validation.json'),
        'sourceCoverageDiagnostic':U.ref(PARENT/'entry-coverage-001/readout.json'),
        'rationale':{'observed':'V2 full account remains unprofitable with16 closed episodes. In the frozen primary entry pipeline160 prepared leader-stock events become1 market-and-industry-admitted event;54 market-admitted leader events fail relative turnover alongside potentially other failures.',
            'inference':'Requiring current sector expansion at exactly the individual preparation/reacceleration close may mismatch entry timing. Excluded opportunities are not assumed profitable; no alternative return has yet been observed.',
            'hypothesis':'Use observed expansion to establish an industry phase, then allow prepared entries while longer relative trend and internal breadth remain healthy; classify sustained trend/breadth failure separately as retirement.',
            'noParameterSearch':True},
        'unchanged':[
            'V1 broad market liquidity/trend/breadth admission and3-close composite-crisis exit',
            'All published industry snapshots, same4999 raw source cohort, coverage rules and same-window relative-return inputs',
            'All V2 stock leadership/catchup preparation, ten-session base, real support, first breakout, stock ordering and two-sided opening risk',
            'Cash/fee/slippage/lot/capacity rules, stock20percent/industry40percent/new-price-risk1percent caps, five positions, one next-session attempt, no adds/backfill',
            'Individual stop5, fixed structural support and3-times prior20NTR ratchet, persistent sells, no terminal liquidation'],
        'industryLifecycle':{
            'initialState':'inactive at first available publication snapshot; no unknown past activation invented',
            'establishment':'An industry becomes active on a known close when it appears in V1 selectedSectors, which already requires raw strength3 consecutive closes and top-three ordering. Establishment is an industry observation independent of whether the market currently permits new portfolio entries.',
            'holdingHealth':'Known feature-valid industry with relative60>0 and breadth60>0.5. Current relative20 and participation remain observed/ranked but are not required for holding health.',
            'failureCount':'Advance on every known feature-valid health-false close, reset on health-true. Unknown interrupts/reset count without a negative vote.',
            'retirement':'Three consecutive known health-false closes deactivate the industry and trigger the common industry-risk sell for any holding currently known to belong to it, including entry-admission controls.',
            'admission':'Active, feature-valid and currently health-true industry; choose at most3 by the unchanged V1 composite percentile score, sector-code tie break. Rising stock prices or daily rank changes do not force portfolio sales.',
            'unknown':'Retain past activation memory during unknown observations but prohibit new entries on unknown current health; do not infer retirement.',
            'snapshotChange':'Reset all activation/failure state and dated leader relations at each newly available publication snapshot. An old-held stock with unknown new industry retains its individual risk protection without invented sector-risk votes.',
            'noExpiryTuning':'No fixed active-day expiry or newly scanned threshold; activation and retirement derive from pre-existing strength and longer-trend/breadth definitions.'},
        'attributionDesign':{
            'A':'Retain immutable V2 full account: V2 admission and old raw-strength failure exit.',
            'B':'New primary control with exactly V2 full frozen signal schedule and new health-based industry exit. This checks the exit change while preserving entry candidates.',
            'C':'New full lifecycle system with active-industry admission and the same health-based exit as B. C versus B checks coordinated admission timing under a common exit.',
            'interpretation':'Changes alter cash availability and subsequent fills; component return differences are not additive causal effects. All comparisons use continuous accounts and explicit source/config identity.'},
        'newAccountsPlanned':[
            {'period':'primary','policy':'completeSystem'},
            {'period':'primary','policy':'withoutMarketAdmission'},
            {'period':'primary','policy':'withoutSectorAdmission'},
            {'period':'primary','policy':'withoutCatchupEntry'},
            {'period':'primary','policy':'v2AdmissionWithLifecycleExit','enginePolicy':'completeSystem'},
            {'period':'weakMarketStress','policy':'completeSystem'}],
        'controlAdmissionContexts':'The v2AdmissionWithLifecycleExit account uses its original V2 selectedSectors solely for validating its unchanged frozen entries; health-based risk fields use V3 lifecycle counters. Other accounts use the new active-industry shortlist.',
        'sourceValidationPlan':[
            'Freeze new code and all6 schedules before observing V3 account outcomes; preserve all attempts',
            'Independently reconstruct activation, health streaks, retirement, snapshot resets and shortlists by backward source scans, not by reading production state',
            'Reuse the independently verified V2 direct-window entry oracle against newly derived industry contexts; inspect all candidate/evidence fields',
            'For B, hash-bind the complete V2 frozen primary schedule without dropping/reordering signals',
            'Test future-price perturbations, unknown interruptions, reactivation and snapshot changes; independently replay every held-day exit, changed opening guard, cash/fees and closed results'],
        'successEvaluation':'Primary after2024-09-24 full account must be evaluated jointly for net realized mean winning/loss percentage ratio, PF, mean loss and tails, continuous return/DD, samples, exposure, years, concentration and excess. Weak-market profit not required. No signal or sample shortage is not success.',
        'numericTargetsHumanConfirmed':False,'numericScreen':'Inherited research proposal only; not a human-confirmed hard target',
        'holdoutClaim':False,'certified':False,'targetAchieved':False,'corporateActionResearchExpansion':False,
        'permissionBoundary':'Local isolated research only; no real orders, commits, pushes, packages or publication'}
    OUT.mkdir();U.write(CONTRACT,rules)
    U.write(OUT/'hypothesis-registration.json',{'schema':'sector-lifecycle-v3-registration','registeredAt':U.now(),'builder':U.ref(Path(__file__)),
        'protocol':U.ref(CONTRACT),'parentValidation':U.ref(PARENT/'validation.json'),'sourceCoverage':U.ref(PARENT/'entry-coverage-001/readout.json'),
        'nextCandidateTrialNumber':58,'plannedNewAccounts':6,'implementationFrozen':False,'newAccountsRun':0,'newStrategyTrialsCompleted':0,
        'noParameterSearch':True,'targetAchieved':False,'holdoutClaim':False})
    U.emit({'registered':True,'protocol':U.ref(CONTRACT),'registration':U.ref(OUT/'hypothesis-registration.json'),'plannedNewAccounts':6})


if __name__=='__main__':register()
