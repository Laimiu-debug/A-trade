"""Synthetic readout checks; no real sources, schedules or accounts are run."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("persistence_readout",ROOT/"scripts/summarize-leader-persistence-admission.py")
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
KEY=dict(period="2024-2025",board="main",exit="trail10",policy="persistent")


def opportunity(signal,state):
    admission={}
    signals={}
    for p in M.POLICIES:
        allowed=state!="unknown" and (p=="knownAll" or (p=="persistent" and state=="passed")
            or (p=="nonpersistent" and state=="failed"))
        status="unknown" if state=="unknown" else "included" if allowed else "excluded"
        admission[p]=dict(status=status,reason="formationUnknown" if state=="unknown" else p if allowed else "otherKnownGroup")
        signals[p]=deepcopy(signal) if allowed else None
    return {**deepcopy(signal),"formationId":signal["signalId"],"formationDate":signal["signalDate"],
        "peerFormationDate":"2023-12-01","formationStatus":state,"parentDailySha256":"synthetic",
        "parentCandidate":{"originalLeaderRank":None},"policyAdmission":admission,"signals":signals}


def source(opps,extra_candidates=0):
    dates=sorted({r["formationDate"] for r in opps})
    daily=[]
    for day in dates:
        rows=[r for r in opps if r["formationDate"]==day]
        daily.append(dict(formationDate=day,currentCandidates=deepcopy(rows),selectedSignalIds=[r["formationId"] for r in rows]))
    if extra_candidates:
        daily[0]["currentCandidates"].extend([dict(symbol="excluded"+str(i)) for i in range(extra_candidates)])
    daily.append(dict(formationDate="2024-01-09",currentCandidates=[],selectedSignalIds=[]))
    schedules={p:[r["signals"][p] for r in opps if r["signals"][p] is not None] for p in M.POLICIES}
    counts=dict(sampleCount=50,targetDayCount=len(daily),currentCandidateCount=sum(len(r["currentCandidates"]) for r in daily),
        top5OpportunityCount=len(opps),rankExcludedCount=extra_candidates,
        selectedFormationCounts={s:sum(r["formationStatus"]==s for r in opps) for s in M.STATES},
        policyCounts={p:{s:sum(r["policyAdmission"][p]["status"]==s for r in opps)
            for s in ("included","excluded","unknown")} for p in M.POLICIES},
        signals={p:len(schedules[p]) for p in M.POLICIES},notScheduled={p:len(opps)-len(schedules[p]) for p in M.POLICIES})
    return daily,schedules,counts


def fixture():
    original,_,result,summary=M.ACCOUNT.synthetic_fixture()
    opps=[opportunity(s,"passed" if i<3 else "failed") for i,s in enumerate(original)]
    opps.append(opportunity(dict(signalId="unknown",strategyId="synthetic",symbol="missing",signalDate="2024-01-08",originalRank=1),"unknown"))
    daily,schedules,counts=source(opps)
    return opps,daily,schedules,counts,result,summary


class PersistenceReadoutTests(unittest.TestCase):
    def test_three_disjoint_F_states_and_empty_days(self):
        o,d,s,c,*_=fixture();r=M.formation_coverage({},d,o,c,s)
        self.assertEqual(r["selectedFormationCounts"],dict(passed=3,failed=1,unknown=1))
        self.assertEqual(r["signals"],dict(knownAll=4,persistent=3,nonpersistent=1))
        self.assertEqual(r["targetDayCount"],6)

    def test_top5_unknown_slot_is_not_refilled(self):
        o=[opportunity(dict(signalId=str(i),strategyId="synthetic",symbol=str(i),signalDate="2024-01-02",originalRank=i+1),state)
           for i,state in enumerate(("unknown","failed","passed","passed","failed"))]
        d,s,c=source(o,1);r=M.formation_coverage({},d,o,c,s)
        self.assertEqual((r["currentCandidateCount"],r["originalOpportunities"],r["rankExcludedCount"]),(6,5,1))
        self.assertEqual([x["originalRank"] for x in s["persistent"]],[3,4])
        M.same(r,M.formation_reference({},d,o,c,s))

    def test_unknown_cannot_be_nonpersistent(self):
        o,d,s,c,*_=fixture();o[-1]["signals"]["nonpersistent"]=M.original_signal(o[-1])
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)

    def test_missing_F_is_unknown_and_is_not_dropped(self):
        o,d,s,c,*_=fixture();o[-1]["peerFormationDate"]=None
        M.same(M.formation_coverage({},d,o,c,s),M.formation_reference({},d,o,c,s))
        o[0]["peerFormationDate"]=None
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)
        with self.assertRaises(ValueError):M.formation_reference({},d,o,c,s)

    def test_old_rank_must_not_replace_common_rank(self):
        o,d,s,c,*_=fixture();o[0]["originalRank"]=6
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)

    def test_order_or_five_field_mutation_rejected(self):
        o,d,s,c,*_=fixture();s["persistent"].reverse()
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)
        o,d,s,c,*_=fixture();s["persistent"][0]["strategyId"]="wrong"
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)

    def test_missing_duplicate_or_reordered_top5_rejected(self):
        o,d,s,c,*_=fixture();d[0]["selectedSignalIds"]=["a","a"]
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)

    def test_count_drift_rejected(self):
        o,d,s,c,*_=fixture();c["currentCandidateCount"]+=1
        with self.assertRaises(ValueError):M.formation_coverage({},d,o,c,s)
        with self.assertRaises(ValueError):M.formation_reference({},d,o,c,s)

    def test_empty_source_keeps_zero_counts(self):
        d,s,c=source([]);r=M.formation_coverage({},d,[],c,s)
        self.assertEqual(r["selectedFormationCounts"],dict(passed=0,failed=0,unknown=0))
        self.assertEqual(r["opportunityDays"],0);M.same(r,M.formation_reference({},d,[],c,s))

    def test_formation_aggregate_independent_exact(self):
        o,d,s,c,*_=fixture();M.same(M.formation_coverage(KEY,d,o,c,s),M.formation_reference(KEY,d,o,c,s))

    def test_full_original_maturity_and_unknown_denominator(self):
        o,d,s,c,r,z=fixture();detail,entries=M.join_episodes(KEY,o,s["persistent"],r,z)
        cov=M.account_coverage(KEY,o,s["persistent"],detail,entries,z)
        self.assertEqual((len(detail),len(entries),cov["closed"],cov["openAtCutoff"],cov["notScheduled"]),(5,3,2,1,2))
        self.assertEqual(cov["admissionCounts"],dict(included=3,excluded=1,unknown=1))
        self.assertEqual(detail[-1]["signalDate"],"2024-01-08")
        self.assertIsNone(detail[-1]["accountDisposition"])

    def test_same_security_reentry_and_open_not_zero_return(self):
        o,d,s,c,r,z=fixture();_,rows=M.join_episodes(KEY,o,s["persistent"],r,z)
        self.assertEqual([x["formationId"] for x in rows],["a","b","c"])
        self.assertEqual([x["closedPnlAmount"] for x in rows],[100.,-100.,0.])
        self.assertIsNone(rows[-1]["closedReturnPct"])
        self.assertEqual(rows[-1]["openUnrealizedPnlAmount"],100.)

    def test_scheduled_unfilled_does_not_become_zero_return(self):
        o,d,s,c,r,z=fixture();sig=s["knownAll"][-1]
        r["research"]["opportunities"].append({**sig,"status":"notPlanned","reason":"maxPositions",
            "filledQuantity":0,"attemptDate":None})
        key={**KEY,"policy":"knownAll"}
        detail,entries=M.join_episodes(key,o,s["knownAll"],r,z)
        cov=M.account_coverage(key,o,s["knownAll"],detail,entries,z)
        self.assertEqual((cov["scheduledSignals"],cov["entered"],cov["notEntered"]),(4,3,1))
        self.assertEqual(detail[3]["positionState"],"notEntered")
        self.assertIsNone(detail[3]["accountDisposition"]["closedReturnPct"])

    def test_F_then_A_equals_signal_then_entry(self):
        o,d,s,c,r,z=fixture();_,rows=M.join_episodes(KEY,o,s["persistent"],r,z)
        self.assertEqual((rows[0]["peerFormationDate"],rows[0]["formationDate"],rows[0]["signalDate"],rows[0]["entryDate"]),
            ("2023-12-01","2024-01-02","2024-01-02","2024-01-03"))
        o[0]["peerFormationDate"]="2024-01-02"
        with self.assertRaises(ValueError):M.join_episodes(KEY,o,s["persistent"],r,z)

    def test_account_return_not_closed_PnL(self):
        *_,z=fixture();v=M.account_metrics(z,3,5)
        self.assertEqual(v["closedPnlAmount"],0.);self.assertEqual(v["openUnrealizedPnlAmount"],100.)
        self.assertAlmostEqual(v["accountReturnPct"],.02)
        self.assertEqual((v["meanWinPct"],v["meanLossPct"],v["medianWinPct"],v["medianLossPct"]),(10.,-10.,10.,-10.))

    def test_actual_payoff_and_concentration_are_retained(self):
        *_,z=fixture();v=M.account_metrics(z,3,5)
        self.assertEqual((v["payoffRatio"],v["profitFactor"]),(1.,1.))
        self.assertEqual(v["top5ShareOfGrossProfitPct"],100.)
        self.assertEqual(v["expectancyPnlAmount"],0.)
        self.assertIn("holdingSessions",z["closedEpisodes"])

    def test_scalar_mapping_independent_reference(self):
        *_,z=fixture();old=M.load_file(M.OLD)
        expected=old.metric_reference(z,3,5)
        expected.update({name:z["closedEpisodes"][source] for name,source in (
            ("medianWinPct","medianWinReturnPct"),("medianLossPct","medianLossReturnPct"),
            ("expectancyReturnPct","expectancyReturnPct"),("expectancyPnlAmount","expectancyPnlAmount"))})
        M.same(M.account_metrics(z,3,5),expected)

    def test_continuous_intervals_and_queued_sell(self):
        o,d,s,c,r,z=fixture();r["decisions"]=[dict(date="2024-01-04",action=a,status="queued",reason=a) for a in ("sell","exit")]
        rows=M.interval_rows(KEY,z,r,o,s["persistent"])
        self.assertEqual(rows[-1]["enteringEquity"],500100.)
        self.assertEqual(rows[0]["queuedExitReasons"],dict(sell=1))
        self.assertEqual(rows[0]["formationStatusCounts"],dict(passed=3,failed=1,unknown=1))
        self.assertTrue(all(x["passed"] for x in M.ACCOUNT.partition_intervals(rows,z,r["curve"])))

    def test_independent_interval_reference_and_capital_reset_rejection(self):
        o,d,s,c,r,z=fixture();old=M.load_file(M.OLD)
        phases={"part1":{"title":"First"},"part2":{"title":"Second"}}
        expected=M.interval_rows(KEY,z,r,o,s["persistent"])
        for x in expected:
            if x["kind"]=="retrospectiveNarrativePhase":x["phaseTitle"]=phases[x["id"]]["title"]
        independent,checks=M.interval_reference(KEY,z,r,o,s["persistent"],phases,old)
        M.same(expected,independent);self.assertTrue(checks["passed"])
        z["periods"][-1]["enteringEquity"]=500000.
        with self.assertRaises(ValueError):M.interval_reference(KEY,z,r,o,s["persistent"],phases,old)

    def test_all18_and_six_correctly_signed_primary(self):
        metric={"persistent":-10.,"knownAll":-3.,"nonpersistent":50.}
        rows=[dict(period=p,board=b,exit="trail10",policy=q,metrics=dict(accountReturnPct=metric[q],profitFactor=None))
            for p in M.PERIODS for b in M.BOARDS for q in M.POLICIES]
        result=M.compare_accounts(rows)
        self.assertEqual(len(result),6)
        self.assertTrue(all(x["candidateMinusControl"]==dict(accountReturnPct=7.,profitFactor=None) for x in result))
        self.assertTrue(all(x["candidate"]=="knownAll" and x["control"]=="persistent" for x in result))
        with self.assertRaises(ValueError):M.compare_accounts(rows[:-1])

    def test_no_invented_ratio_for_empty_or_undefined(self):
        *_,z=fixture();z["closedEpisodes"]["profitFactor"]=None;z["closedEpisodes"]["payoffRatio"]=None
        v=M.account_metrics(z,3,5);self.assertIsNone(v["profitFactor"]);self.assertIsNone(v["payoffRatio"])

    def test_inputs_not_mutated(self):
        args=fixture();before=deepcopy(args);o,d,s,c,r,z=args
        M.formation_coverage({},d,o,c,s);M.formation_reference({},d,o,c,s)
        M.join_episodes(KEY,o,s["persistent"],r,z);M.interval_rows(KEY,z,r,o,s["persistent"])
        self.assertEqual(args,before)

    def test_tuple_key_diagnostics(self):
        key=("2024-2025","main","trail10");M.same({key:dict(value=1.)},{key:dict(value=1.)})
        with self.assertRaises(ValueError):M.same({key:1.},{key:2.})

    def test_independent_raw_fills_partial_open_and_final_closed(self):
        old,episodes=M.load_file(M.OLD),M.load_file(M.EPISODES)
        raw=episodes.synthetic_fixture();buys=[x for x in raw["trades"] if x["side"]=="buy"]
        signals=[dict(signalId=x["signalId"],strategyId="synthetic",symbol=x["symbol"],signalDate=x["signalDate"],originalRank=1) for x in buys]
        o=[opportunity(s,"passed") for s in signals]
        o.append(opportunity(dict(signalId="none",strategyId="synthetic",symbol="n",signalDate="2024-01-07",originalRank=1),"unknown"))
        raw["research"]={"opportunities":[{**sig,"status":"filled","reason":"filled","filledQuantity":buy["quantity"],"attemptDate":buy["date"]}
            for sig,buy in zip(signals,buys)]}
        detail,entries=M.episode_reference(KEY,o,signals,raw,old,episodes)
        self.assertEqual((len(detail),len(entries)),(3,2));self.assertEqual(entries[0]["closedPnlAmount"],16.)
        self.assertEqual(entries[1]["openUnrealizedPnlAmount"],48.)
        self.assertIsNone(entries[1]["closedReturnPct"]);self.assertTrue(entries[1]["openMarkStale"])


if __name__=="__main__":unittest.main()
